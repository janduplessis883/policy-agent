import re
from dataclasses import replace
from threading import Lock

from pydantic import ValidationError

from nathan.domain import Document, Result, SourceError
from nathan.model import Assessment, Selection
from nathan.policy_search import ranked_policy_ids
from nathan.tools.create_placeholder import create_placeholder
from nathan.tools.current_date import current_date

WRITE_LOCK = Lock()
MAX_POLICY_READS = 15


def label(text):
    return re.sub(r"([\\`*{}\[\]()<>_!|#])", r"\\\1", text).replace("\n", " ")


def source_link(title, url):
    safe = label(title)
    if url and url.startswith("https://") and not any(c in url for c in "()<>\n\r "):
        return f"[{safe}]({url})"
    return safe


class Agent:
    def __init__(self, sources, model, clock=current_date, on_event=None, on_text=None):
        self.sources, self.model, self.clock = sources, model, clock
        self.on_event = on_event or (lambda _event: None)
        self.on_text = on_text

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
        by_id = {p.id: p for p in inventory}
        if any(pid not in by_id for pid in selection.policy_ids):
            raise SourceError("The model referenced an unavailable policy. No action was taken.")
        gap = force_gap or selection.operation in {"gap", "placeholder"}
        selected = list(
            dict.fromkeys(ranked_policy_ids(question, inventory) + selection.policy_ids)
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
                    question,
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
            incomplete = [label(d.title) for d in docs if not d.complete]
            if incomplete:
                assessment.answer += (
                    "\n\nRelevant policies were found but could not be read in full: "
                    + "; ".join(incomplete)
                    + ". Unavailable content is not evidence of missing coverage."
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
        today, profile, inventory, docs, selection, assessment = self._evaluate(question, history)
        by_id = {p.id: p for p in inventory}
        text = assessment.answer
        if assessment.classification:
            text = f"**{assessment.classification}.** {text}"
        citations = []
        for c in assessment.citations:
            if c.policy_id == profile.id:
                citation = (
                    f"{source_link(profile.title, profile.public_url)} · Surgery Profile, "
                    f"{label(c.section)}"
                )
            else:
                p = by_id[c.policy_id]
                state = "Live" if p.is_live(today) else "Not Live"
                citation = f"{source_link(p.title, p.public_url)}, {label(c.section)} ({state})"
            if citation not in citations:
                citations.append(citation)
        if citations:
            text += "\n\n" + "; ".join(citations)
        if any(not d.complete for d in docs):
            text += "\n\nSome source content could not be read in full; this assessment is limited."
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
        return Result(
            text,
            proposal,
            model=getattr(self.model, "response_model", None),
            proposal_advice=assessment.placeholder_advice if proposal else None,
            proposal_inventory=list(inventory) if proposal else None,
        )

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
