from nathan.retrieval_eval import evaluate


def test_draft_set_shows_hybrid_ahead_of_title_search():
    report = evaluate()
    assert report["questions"] >= 30
    assert "draft" in report["source"]
    assert report["hybrid_recall_at_5"] > report["keyword_recall_at_5"]
    assert report["hybrid_recall_at_5"] >= 0.8
    assert report["hybrid_section_accuracy"] >= 0.8
