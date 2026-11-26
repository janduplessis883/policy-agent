import logging
import re
from dataclasses import replace
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from nathan.domain import Document, Result, SourceError
from nathan.model import Assessment, Selection
from nathan.passages import section_in_text
from nathan.policy_search import lexical_policy_ids
from nathan.register import is_register_question, render_register
from nathan.tools.create_placeholder import create_placeholder
from nathan.tools.current_date import current_date
from nathan.tools.list_overdue_policies import is_overdue_question, list_overdue_policies

WRITE_LOCK = Lock()
MAX_POLICY_READS = 15
logger = logging.getLogger(__name__)


def label(text):
    return re.sub(r"([\\`*{}\[\]()<>_!|#])", r"\\\1", text).replace("\n", " ")


def source_link(title, url):
    if not isinstance(title, str):
        title = ""
    if not isinstance(url, str):
        url = ""
    safe = label(title)
    if url.startswith("https://") and not any(char in url for char in "()<>\n\r "):
        return f"[{safe}]({url})"
    return safe


def previous_context_question(history):
    """The previous resolved topic, or the previous user question when that was not stored."""
    prior_user = ""
    found = ""
    for message in list(history or [])[-12:]:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            prior_user = message["content"].strip()
        context = message.get("conversation_context")
        if message.get("role") == "assistant" and isinstance(context, dict):
            question = context.get("question")
            if isinstance(question, str) and question.strip():
                found = question.strip()
    return found or prior_user


def citation_lines(citations, profile, by_id, by_document, today):
    lines = []
    unsupported = False
    for citation in citations:
        if citation.policy_id == profile.id:
            shown = (
                f"{source_link(profile.title, profile.public_url)} · Surgery Profile, "
                f"{label(citation.section)}"
            )
            source_text = profile.text
        else:
            policy = by_id[citation.policy_id]
            state = "Live" if policy.is_live(today) else "Not Live"
            document = by_document[policy.id]
            read_status = "✓ Read in full" if document.complete else "⚠ Incomplete read"
            shown = (
                f"{source_link(policy.title, policy.public_url)}, {label(citation.section)} "
                f"({state}) · {read_status}"
            )
            source_text = document.text
        if not section_in_text(citation.section, source_text):
            unsupported = True
            shown += " · section not found in retrieved text"
        if shown not in lines:
            lines.append(shown)
    return lines, unsupported


def grounding_notes(assessment, unsupported, excerpted):
    notes = []
    if unsupported:
        notes.append(
            "**Cited section not found.** At least one cited section does not appear "
            "in the retrieved text. Check the policy before relying on it."
        )
    if (
        assessment.classification in {None, "Covered", "Revision needed"}
        and not assessment.citations
    ):
        notes.append(
            "**Not covered by a cited policy section.** No retrieved policy or Surgery "
            "Profile section was cited. Do not treat this reply as written surgery policy."
        )
    titles = [title for title in excerpted or [] if isinstance(title, str) and title.strip()]
    if titles:
        notes.append(
            "**Focused read.** These long policies were retrieved in full, but this answer "
            "used the sections that matched the question: "
            + "; ".join(label(title) for title in titles)
            + ". Ask about a named section if you need the rest."
        )
    return notes


class Agent:
    def __init__(
        self, sources, model, clock=current_date, on_event=None, on_text=None, log_answer=None
    ):
        self.sources, self.model, self.clock = sources, model, clock
        self.on_event = on_event or (lambda _event: None)
        self.on_text = on_text
        self.log_answer = log_answer

    def _evaluate(self, question, history, force_gap=False):
        self.on_event("Current Date · Europe/London")
        today = self.clock()
        self.on_event("Get Surgery Profile")
        profile = self.sources.profile()
        self.on_event("List Policies")
        inventory = self.sources.inventory()
        try:
            selection = Selection.model_validate(self.model.select(question, history, inventory))
        except ValidationError as exc:
            raise SourceError("The model could not identify a valid policy scope.") from exc
        resolved_question = (selection.resolved_question or question).strip() or question
        by_id = {p.id: p for p in inventory}
        if any(pid not in by_id for pid in selection.policy_ids):
            raise SourceError("The model referenced an unavailable policy. No action was taken.")
        gap = force_gap or selection.operation in {"gap", "placeholder"}
        selected = list(
            dict.fromkeys(
                lexical_policy_ids(
                    resolved_question,
                    inventory,
                    previous_question=previous_context_question(history),
                )
                + selection.policy_ids
            )
        )
        selection.policy_ids = selected
        ids = selected
        limited = len(ids) > MAX_POLICY_READS
        ids = ids[:MAX_POLICY_READS]
        docs = []
        for pid in ids:
            self.on_event(f"Read Policy · {by_id[pid].title}")
            try:
                docs.append(self.sources.read(by_id[pid]))
            except SourceError:
                docs.append(Document(pid, by_id[pid].title, "", complete=False))
        exhaustive = set(ids) == set(by_id) and all(d.complete for d in docs)
        response_docs = docs
        if selection.operation == "answer" and not gap:
            response_docs = [
                d
                if by_id[d.id].is_live(today)
                else replace(d, text="Not Live: procedure text withheld from operational answers.")
                for d in docs
            ]
        try:
            assessment = Assessment.model_validate(
                self.model.respond(
                    resolved_question,
                    history,
                    today,
                    profile,
                    inventory,
                    response_docs,
                    exhaustive,
                    **({"on_text": self.on_text} if self.on_text else {}),
                )
            )
        except ValidationError as exc:
            raise SourceError("The model could not produce a validated assessment.") from exc
        read_ids = {d.id for d in docs if d.text} | {profile.id}
        if any(c.policy_id not in read_ids for c in assessment.citations):
            raise SourceError("The answer contained an unverified policy citation.")
        if assessment.classification == "Confirmed gap" and not exhaustive:
            assessment.classification = "Potential gap"
            assessment.answer = (
                "There may be a gap in the requested policy coverage, but the available "
                "evidence is insufficient to confirm it."
            )
        if gap or assessment.classification in {"Confirmed gap", "Potential gap"}:
            if not ids:
                assessment.answer += (
                    "\n\nNo relevant policy was found in the accessible title and tag inventory. "
                    "Coverage inside differently named policies has not been ruled out."
                )
            if set(ids) != set(by_id):
                assessment.answer += (
                    "\n\nOther policies were not reviewed; this is a topic-focused assessment, "
                    "not confirmation that the whole collection lacks coverage."
                )
        # Model-authored links cannot become fabricated policy citations.
        if re.search(r"\]\s*\(|https?://", assessment.answer):
            raise SourceError("The answer contained a source link that could not be verified.")
        if limited:
            assessment.answer += (
                "\n\nThis review was limited to 15 policies. "
                "Please narrow the question to review the remaining policies."
            )
        return today, profile, inventory, docs, selection, assessment

    def ask(self, question, history):
        if is_overdue_question(question):
            return self._answer_overdue(question)
        if is_register_question(question):
            return self._answer_from_register(question)
        today, profile, inventory, docs, selection, assessment = self._evaluate(question, history)
        by_id = {p.id: p for p in inventory}
        by_document_id = {d.id: d for d in docs}
        text = assessment.answer
        if assessment.classification:
            text = f"**{assessment.classification}.** {text}"
        citations, unsupported = citation_lines(
            assessment.citations, profile, by_id, by_document_id, today
        )
        if citations:
            text += "\n\n**Sources**\n" + "\n".join(f"- {citation}" for citation in citations)
        notes = grounding_notes(
            assessment, unsupported, getattr(self.model, "excerpted_titles", None)
        )
        if notes:
            text += "\n\n" + "\n\n".join(notes)
        incomplete = [source_link(d.title, by_id[d.id].public_url) for d in docs if not d.complete]
        if incomplete:
            text += (
                "\n\n**⚠ Limited assessment.** These policies could not be read in full:\n\n"
                + "\n".join(f"- {policy}" for policy in incomplete)
            )
            if assessment.classification in {"Confirmed gap", "Potential gap"}:
                text += "\n\nUnavailable content is not evidence of missing coverage."
        proposal = (
            assessment.placeholder_title
            if (
                assessment.classification in {"Confirmed gap", "Potential gap"}
                and assessment.placeholder_advice
                and all(d.complete for d in docs)
                and len(set(selection.policy_ids)) <= MAX_POLICY_READS
            )
            else None
        )
        if proposal:
            text += f"\n\nUse Create placeholder to create an empty {label(proposal)} record."
        self._log(question, text)
        return Result(
            text,
            proposal,
            model=getattr(self.model, "response_model", None),
            proposal_advice=assessment.placeholder_advice if proposal else None,
            proposal_inventory=list(inventory) if proposal else None,
            conversation_context={
                "question": (selection.resolved_question or question).strip() or question,
                "policies": [{"id": d.id, "title": d.title} for d in docs],
            },
        )

    def _answer_overdue(self, question):
        """Out-of-date list from inventory metadata. No profile body, policy text, or model."""
        try:
            self.on_event("Current Date · Europe/London")
            today = self.clock()
            self.on_event("List Overdue Policies · titles, owners, review dates and tags only")
            inventory = self.sources.inventory()
            text = list_overdue_policies(inventory, today, question, source_link)
        except SourceError:
            raise
        except Exception as exc:
            logger.exception("Overdue policy list failed")
            raise SourceError(
                "The overdue policy list could not be built from Notion. "
                "Policy text was not read. The details were written to the server log "
                "and do not include the API token."
            ) from exc
        self._log(question, text)
        return Result(
            text,
            conversation_context={"question": question.strip() or question, "policies": []},
            source_note=(
                "Answered from the overdue-policy list, not the language model. "
                "Policy text was not read."
            ),
        )

    def _answer_from_register(self, question):
        """Review status for the whole inventory. Dates come from the records, not the model."""
        try:
            self.on_event("Current Date · Europe/London")
            today = self.clock()
            self.on_event("Get Surgery Profile")
            self.sources.profile()
            self.on_event("List Policies")
            inventory = self.sources.inventory()
            self.on_event("Policy register · titles, tags, owners and review dates")
            text = render_register(question, inventory, today, source_link)
        except SourceError:
            raise
        except Exception as exc:
            logger.exception("Policy register failed")
            raise SourceError(
                "The policy register could not be built from Notion. "
                "The details were written to the server log and do not include the API token. "
                "Check that the integration can read the Policies database."
            ) from exc
        self._log(question, text)
        return Result(
            text,
            conversation_context={"question": question.strip() or question, "policies": []},
            source_note="Answered from the policy register, not the language model.",
        )

    def _log(self, question, text):
        if not self.log_answer:
            return
        try:
            calls = getattr(self.model, "calls", [])
            models = list(dict.fromkeys(model for model, _ in calls if model))
            counts = [tokens for _, tokens in calls]
            total = sum(counts) if counts and None not in counts else None
            self.log_answer(question, text, datetime.now(ZoneInfo("Europe/London")), models, total)
        except Exception:  # noqa: BLE001 -- a log failure must not discard a verified answer
            self.on_event("Log Answer · Notion did not confirm the save")
        else:
            self.on_event("Log Answer · saved to Notion")

    def create(self, title, advice, inventory):
        """Create the user-authorised proposal using its previously reviewed inventory."""
        with WRITE_LOCK:
            if inventory is None:
                raise SourceError("The saved proposal is unavailable. Ask for a new proposal.")
            self.on_event("Current Date · Europe/London")
            today = self.clock()
            self.on_event("Create Placeholder · verify fields, Agent Advice and empty body")
            created = create_placeholder(self.sources.client, title, today, inventory, advice)
            return Result(
                f"Created {source_link(title, created.get('public_url'))}: tagged "
                f"**NEW POLICY REQUIRED**; **Review Date:** {today.isoformat()}; "
                "synopsis saved in **Agent Advice**; page body empty for the superuser."
            )
