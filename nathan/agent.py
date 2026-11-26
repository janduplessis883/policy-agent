import logging
import re
import time
from dataclasses import replace
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from nathan.domain import Document, Result, SourceError
from nathan.model import Assessment, Selection
from nathan.passages import section_in_text
from nathan.policy_index import NullIndex, documents_from_hits
from nathan.policy_search import lexical_policy_ids
from nathan.progress import Progress, brief, emit, use_progress
from nathan.register import is_register_question, policy_status, render_register
from nathan.tools.create_placeholder import create_placeholder
from nathan.tools.current_date import current_date
from nathan.tools.list_overdue_policies import is_overdue_question, list_overdue_policies

WRITE_LOCK = Lock()
MAX_POLICY_READS = 15
logger = logging.getLogger(__name__)


def _safe_reason(exc) -> str:
    text = " ".join(str(exc).split())
    lowered = text.casefold()
    if any(mark in lowered for mark in ("bearer", "sb_secret", "sb_publishable", "api_key", "eyj")):
        return type(exc).__name__
    return brief(f"{type(exc).__name__}: {text}", 140)


def _cited(result) -> str:
    text = result.text or ""
    if "**Sources**" not in text:
        return "Sources: none cited."
    lines = []
    for line in text.split("**Sources**", 1)[1].splitlines():
        line = line.strip()
        if not line.startswith("- "):
            if lines:
                break
            continue
        lines.append(brief(line[2:], 90))
        if len(lines) == 6:
            break
    if not lines:
        return "Sources: none cited."
    return "Sources: " + "; ".join(lines) + "."


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
            if getattr(document, "indexed", False):
                read_status = "Indexed sections"
            elif document.complete:
                read_status = "✓ Read in full"
            else:
                read_status = "⚠ Incomplete read"
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


def _currency_note(docs, by_id, today):
    """Review status for the policies actually used. Not an inventory list."""
    lines = []
    for document in docs:
        policy = by_id.get(document.id)
        if policy is None:
            continue
        status = policy_status(policy, today)
        if status == "Live":
            continue
        when = policy.review_date if isinstance(policy.review_date, str) else ""
        when = when.strip() or "no review date"
        lines.append(f"- {label(policy.title or 'Untitled policy')}: {status} (review date {when})")
    if not lines:
        return ""
    return (
        "\n\n**Review status of policies used.** Taken from Review_Date and tags, "
        "not from the model. Procedure text from these records is withheld. "
        "This is not a list of every policy.\n" + "\n".join(lines)
    )


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
        self,
        sources,
        model,
        clock=current_date,
        on_event=None,
        on_text=None,
        log_answer=None,
        index=None,
    ):
        self.sources, self.model, self.clock = sources, model, clock
        self.on_event = on_event or (lambda _event: None)
        self.on_text = on_text
        self.log_answer = log_answer
        self.index = index if index is not None else NullIndex()

    def _event(self, label, detail):
        emit(label, detail)

    def _read_one(self, policy):
        started = time.perf_counter()
        try:
            document = self.sources.read(policy)
        except SourceError:
            document = Document(policy.id, policy.title, "", complete=False)
        took = time.perf_counter() - started
        kind = "incomplete" if not document.complete else "full text"
        self._event(
            "Reading Notion",
            f"Read Notion: {brief(policy.title)}, live, {took:.2f}s, {kind}, "
            f"{len(document.text):,} characters.",
        )
        return document

    def _index_hits(self, question, keyword_ids, operation, gap):
        if not getattr(self.index, "enabled", False):
            self._event(
                "Index search",
                "Index search skipped: the policy index is not configured. "
                "Falling back to title and tag matches and Notion reads.",
            )
            return []
        try:
            return self.index.search(
                question,
                keyword_ids=keyword_ids,
                live_only=operation == "answer" and not gap,
            )
        except Exception as exc:
            logger.exception("Policy index search failed; using keyword mode")
            self.index.degraded = True
            self._event(
                "Index search",
                f"Index search failed ({_safe_reason(exc)}). Falling back to Notion reads.",
            )
            return []

    def _evaluate(self, question, history, force_gap=False):
        today = self.clock()
        self._event("Current date", f"Current date: Europe/London, {today.isoformat()}.")
        started = time.perf_counter()
        profile = self.sources.profile()
        self._event(
            "Reading Notion",
            f"Surgery profile: live Notion read, {time.perf_counter() - started:.2f}s, "
            f"{brief(profile.title)}.",
        )
        started = time.perf_counter()
        inventory = self.sources.inventory()
        self._event(
            "Policy inventory",
            f"Policy inventory: {len(inventory)} pages, {time.perf_counter() - started:.2f}s.",
        )
        try:
            selection = Selection.model_validate(self.model.select(question, history, inventory))
        except ValidationError as exc:
            raise SourceError("The model could not identify a valid policy scope.") from exc
        resolved_question = (selection.resolved_question or question).strip() or question
        by_id = {p.id: p for p in inventory}
        if any(pid not in by_id for pid in selection.policy_ids):
            raise SourceError("The model referenced an unavailable policy. No action was taken.")
        gap = force_gap or selection.operation in {"gap", "placeholder"}
        lexical = lexical_policy_ids(
            resolved_question,
            inventory,
            previous_question=previous_context_question(history),
        )
        lexical_titles = [brief(by_id[item].title) for item in lexical if item in by_id][:8]
        self._event(
            "Searching index: title and tags",
            f"Title and tag matches: {len(lexical)}. {', '.join(lexical_titles) or 'none'}.",
        )
        self._event(
            "Selecting policies",
            f"Selection: operation {selection.operation}, "
            f"{len(selection.policy_ids)} policies chosen by the model.",
        )
        selected = list(dict.fromkeys(lexical + selection.policy_ids))
        selection.policy_ids = selected
        ids = selected
        hits = self._index_hits(resolved_question, selected, selection.operation, gap)
        docs = (
            documents_from_hits(
                hits,
                by_id,
                today,
                withhold_non_live=selection.operation == "answer" and not gap,
            )
            if hits
            else []
        )
        if docs:
            # Chunks stand in for whole documents, so more than 15 policies can be used.
            for document in docs:
                if getattr(document, "indexed", False):
                    self._event(
                        "Indexed sections",
                        f"Indexed sections: {brief(document.title)}, not fetched from Notion.",
                    )
            covered = {document.id for document in docs}
            for pid in [policy_id for policy_id in selected if policy_id not in covered][:3]:
                docs.append(self._read_one(by_id[pid]))
            ids = [document.id for document in docs]
            limited = False
        else:
            limited = len(ids) > MAX_POLICY_READS
            ids = ids[:MAX_POLICY_READS]
            docs = [self._read_one(by_id[pid]) for pid in ids]
        exhaustive = set(ids) == set(by_id) and all(d.complete for d in docs)
        response_docs = docs
        not_live = [
            brief(by_id[document.id].title)
            for document in docs
            if not by_id[document.id].is_live(today)
        ]
        self._event(
            "Checking Live status",
            f"Live check: {len(not_live)} of {len(docs)} policies are not Live"
            + (f" and procedure text is withheld: {', '.join(not_live[:8])}" if not_live else "")
            + ".",
        )
        status_bits = [
            f"{brief(by_id[document.id].title)} {policy_status(by_id[document.id], today)}"
            for document in docs
        ]
        self._event(
            "Review status",
            "Review status: "
            + ("; ".join(status_bits[:12]) if status_bits else "no policy was used")
            + ".",
        )
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
        if any(getattr(document, "indexed", False) for document in docs):
            assessment.answer += (
                "\n\nIndexed sections were used from the policy text search. "
                "This is not a full read of every matching page."
            )
        excerpted = getattr(self.model, "excerpted_titles", None) or []
        if excerpted:
            self._event(
                "Size budget",
                "Truncated to matching sections: "
                + ", ".join(brief(title) for title in excerpted[:8])
                + ".",
            )
        if limited:
            self._event("Size budget", "Notion reads were limited to 15 policies.")
        return today, profile, inventory, docs, selection, assessment

    def ask(self, question, history):
        progress = Progress(self.on_event)
        with use_progress(progress):
            if is_overdue_question(question):
                self._event(
                    "Routing",
                    "Routing: overdue list. This is a short request for out-of-date policies, "
                    "so policy text is not opened.",
                )
                result = self._answer_overdue(question)
                cited = "Sources: overdue list. Policy text was not opened."
            elif is_register_question(question):
                self._event(
                    "Routing",
                    "Routing: policy register. This asks about the inventory, not what to do.",
                )
                result = self._answer_from_register(question)
                cited = "Sources: policy register. Policy text was not opened."
            else:
                self._event(
                    "Routing",
                    "Routing: normal retrieval. This is not a short overdue-list or register request.",
                )
                result = self._answer_from_policies(question, history)
                cited = _cited(result)
            self._event("Finished", f"Finished in {progress.elapsed:.2f}s. {cited}")
            self._event("Summary", "Summary: " + " → ".join(progress.labels))
            return result

    def _answer_from_policies(self, question, history):
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
        currency = _currency_note(docs, by_id, today)
        if currency:
            text += currency
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
            today = self.clock()
            self._event("Current date", f"Current date: Europe/London, {today.isoformat()}.")
            started = time.perf_counter()
            inventory = self.sources.inventory()
            self._event(
                "Policy inventory",
                f"Policy inventory: {len(inventory)} pages, {time.perf_counter() - started:.2f}s. "
                "List Overdue Policies: titles, owners, review dates and tags only.",
            )
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
            today = self.clock()
            self._event("Current date", f"Current date: Europe/London, {today.isoformat()}.")
            started = time.perf_counter()
            self.sources.profile()
            self._event(
                "Reading Notion",
                f"Surgery profile: live Notion read, {time.perf_counter() - started:.2f}s.",
            )
            started = time.perf_counter()
            inventory = self.sources.inventory()
            self._event(
                "Policy inventory",
                f"Policy inventory: {len(inventory)} pages, {time.perf_counter() - started:.2f}s. "
                "Policy register: titles, tags, owners and review dates.",
            )
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
            self._event("Log answer", "Log Answer · Notion did not confirm the save")
        else:
            self._event("Log answer", "Log Answer · saved to Notion")

    def create(self, title, advice, inventory):
        """Create the user-authorised proposal using its previously reviewed inventory."""
        progress = Progress(self.on_event)
        with use_progress(progress), WRITE_LOCK:
            if inventory is None:
                raise SourceError("The saved proposal is unavailable. Ask for a new proposal.")
            today = self.clock()
            self._event(
                "Creating placeholder",
                f"Current date: Europe/London, {today.isoformat()}. "
                "Create Placeholder: verify fields, Agent Advice and empty body.",
            )
            started = time.perf_counter()
            created = create_placeholder(self.sources.client, title, today, inventory, advice)
            self._event(
                "Creating placeholder",
                f"Placeholder written in {time.perf_counter() - started:.2f}s: {brief(title)}.",
            )
            return Result(
                f"Created {source_link(title, created.get('public_url'))}: tagged "
                f"**NEW POLICY REQUIRED**; **Review Date:** {today.isoformat()}; "
                "synopsis saved in **Agent Advice**; page body empty for the superuser."
            )
