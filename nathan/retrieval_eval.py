"""Compare keyword retrieval with hybrid retrieval on a draft question set.

Run: python -m nathan.retrieval_eval

The questions in evals/draft_set.py are written from fixture headings. They are not
real staff questions. To use real ones, create evals/staff_questions.json as a list of
{"question", "policy_id", "section"} and this report will use that file instead.
There is no reranker: the draft set is the baseline, not a tuning loop.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from nathan.chunks import chunk_policy
from nathan.domain import Policy
from nathan.fusion import reciprocal_rank_fusion
from nathan.policy_search import ranked_policy_ids, tokens

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "evals" / "draft_set.py"
STAFF = ROOT / "evals" / "staff_questions.json"


@dataclass
class Question:
    question: str
    policy_id: str
    section: str
    title: str
    body: str


def load_questions() -> tuple[list[Question], str]:
    if STAFF.exists():
        raw = json.loads(STAFF.read_text())
        return [
            Question(
                item["question"], item["policy_id"], item["section"], item.get("title", ""), ""
            )
            for item in raw
        ], "staff_questions.json"
    from evals.draft_set import DRAFT_POLICIES

    questions = []
    for policy in DRAFT_POLICIES:
        for section, body, question in policy["sections"]:
            questions.append(Question(question, policy["id"], section, policy["title"], body))
    return questions, "evals/draft_set.py (draft, not real staff questions)"


def evaluate(k: int = 5) -> dict:
    questions, source = load_questions()
    policies = _policies(questions)
    chunks = _chunks(questions)
    keyword_hits = hybrid_hits = section_hits = 0
    for item in questions:
        keyword = ranked_policy_ids(item.question, policies)
        fts = _local_fts(item.question, chunks)
        hybrid = reciprocal_rank_fusion([fts, keyword])
        if item.policy_id in keyword[:k]:
            keyword_hits += 1
        if item.policy_id in hybrid[:k]:
            hybrid_hits += 1
        if _section_hit(item, chunks):
            section_hits += 1
    total = len(questions) or 1
    return {
        "source": source,
        "questions": len(questions),
        "keyword_recall_at_5": keyword_hits / total,
        "hybrid_recall_at_5": hybrid_hits / total,
        "hybrid_section_accuracy": section_hits / total,
    }


def _policies(questions: list[Question]) -> list[Policy]:
    seen = {}
    for item in questions:
        seen[item.policy_id] = Policy(item.policy_id, item.title or item.policy_id)
    return list(seen.values())


def _chunks(questions: list[Question]):
    grouped: dict[str, list] = {}
    bodies: dict[tuple[str, str], str] = {}
    titles = {}
    for item in questions:
        titles[item.policy_id] = item.title or item.policy_id
        if item.body:
            bodies[(item.policy_id, item.section)] = item.body
    by_policy: dict[str, list[tuple[str, str]]] = {}
    for (policy_id, section), body in bodies.items():
        by_policy.setdefault(policy_id, []).append((section, body))
    for policy_id, sections in by_policy.items():
        markdown = "\n\n".join(f"# {section}\n\n{body}" for section, body in sections)
        grouped[policy_id] = chunk_policy(titles[policy_id], markdown)
    return grouped


def _local_fts(question: str, chunks) -> list[str]:
    """Token overlap stand-in for Postgres full-text, so the draft eval needs no database."""
    query = tokens(question)
    scored = []
    for policy_id, policy_chunks in chunks.items():
        best = 0
        for chunk in policy_chunks:
            overlap = len(tokens(chunk.text) & query) + 2 * len(tokens(chunk.heading) & query)
            best = max(best, overlap)
        if best:
            scored.append((best, policy_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [policy_id for _, policy_id in scored]


def _section_hit(item: Question, chunks) -> bool:
    query = tokens(item.question)
    best_heading, best = "", -1
    for chunk in chunks.get(item.policy_id, []):
        overlap = len(tokens(chunk.text) & query) + 2 * len(tokens(chunk.heading) & query)
        if overlap > best:
            best, best_heading = overlap, chunk.heading
    return best_heading == item.section and best > 0


def main():
    report = evaluate()
    print(f"Source: {report['source']}")
    print(f"Questions: {report['questions']}")
    print(f"Keyword recall@5: {report['keyword_recall_at_5']:.2f}")
    print(f"Hybrid recall@5: {report['hybrid_recall_at_5']:.2f}")
    print(f"Hybrid section accuracy: {report['hybrid_section_accuracy']:.2f}")
    print("No reranker is applied.")


if __name__ == "__main__":
    main()
