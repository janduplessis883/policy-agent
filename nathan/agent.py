import re
from dataclasses import asdict, replace
from threading import Lock

from pydantic import ValidationError

from nathan.domain import Result, SourceError
from nathan.model import Assessment, Selection
from nathan.tools.create_placeholder import create_placeholder, normalise
from nathan.tools.current_date import current_date

WRITE_LOCK = Lock()


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
        ids = list(by_id) if gap else list(dict.fromkeys(selection.policy_ids))
        docs = []
        for pid in ids:
            self.on_event(f"Read Policy · {by_id[pid].title}")
            docs.append(self.sources.read(by_id[pid]))
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
        read_ids = {d.id for d in docs} | {profile.id}
        if any(c.policy_id not in read_ids for c in assessment.citations):
            raise SourceError("The answer contained an unverified policy citation.")
        if assessment.classification == "Confirmed gap" and not exhaustive:
            raise SourceError(
                "Coverage could not be fully verified. A confirmed gap cannot be established."
            )
        # Model-authored links cannot become fabricated policy citations.
        if re.search(r"\]\s*\(|https?://", assessment.answer):
            raise SourceError("The answer contained a source link that could not be verified.")
        return today, profile, inventory, docs, selection, assessment

    def ask(self, question, history):
        today, profile, inventory, docs, _selection, assessment = self._evaluate(question, history)
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
            if assessment.classification in {"Confirmed gap", "Potential gap"}
            else None
        )
        return Result(text, proposal, model=getattr(self.model, "response_model", None))

    def create(self, title, original_question, history):
        # One process-wide critical section covers new evidence, gap assessment and creation.
        with WRITE_LOCK:
            instruction = (
                f"{original_question}\nI explicitly require written policy coverage for "
                f'"{title}" and authorise one empty placeholder if a gap is confirmed. '
                f'Assess this exact title; placeholder_title must be "{title}" if missing.'
            )
            today, _profile, inventory, _docs, _selection, assessment = self._evaluate(
                instruction, history, force_gap=True
            )
            if (
                assessment.classification != "Confirmed gap"
                or not assessment.placeholder_title
                or normalise(assessment.placeholder_title) != normalise(title)
            ):
                raise SourceError(
                    "A gap for this placeholder could not be reconfirmed. "
                    "No record was created. " + assessment.answer
                )
            # Refresh immediately before mutation. Any inventory change invalidates this proposal.
            self.on_event("List Policies · pre-creation refresh")
            fresh = self.sources.inventory()
            if [asdict(p) for p in fresh] != [asdict(p) for p in inventory]:
                raise SourceError(
                    "The policy inventory changed. Ask again before creating a placeholder."
                )
            self.on_event("Create Placeholder · verify fields and empty body")
            created = create_placeholder(self.sources.client, title, today, fresh)
            return Result(
                f"Created {source_link(title, created.get('public_url'))}: tagged "
                f"**NEW POLICY REQUIRED**; **Review Date:** {today.isoformat()}; "
                "page body empty for the superuser. Reason: written coverage was "
                "requested and no coverage was found in the accessible Notion Policies collection."
            )
