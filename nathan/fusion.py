"""Reciprocal rank fusion.

Scores are not mixed. Each list contributes 1 / (k + rank), and the lists are
the vector hits, the full-text hits, and the existing title/tag matches.
"""

RRF_K = 60


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen = set()
        rank = 0
        for item in ranking:
            if not item or item in seen:
                continue
            seen.add(item)
            rank += 1
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda item: (-scores[item], item))
