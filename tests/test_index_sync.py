from nathan.chunks import chunk_policy
from nathan.domain import Policy
from nathan.fusion import reciprocal_rank_fusion
from nathan.index_sync import StoredChunk, plan_sync, same_content


def test_rank_fusion_prefers_an_item_that_appears_in_every_list():
    fused = reciprocal_rank_fusion(
        [
            ["vector-only", "shared"],
            ["fts-only", "shared"],
            ["shared", "title-only"],
        ]
    )
    assert fused[0] == "shared"
    assert "vector-only" in fused
    assert "title-only" in fused


def test_plan_skips_current_pages_and_deletes_missing_ones():
    current = Policy("keep", "Keep", last_edited_time="2026-01-01T00:00:00Z")
    changed = Policy("edit", "Edit", last_edited_time="2026-02-01T00:00:00Z")
    archived = Policy("old", "Old", excluded=True, last_edited_time="2026-01-01T00:00:00Z")
    stored = {
        "keep": [_row("2026-01-01T00:00:00Z")],
        "edit": [_row("2026-01-01T00:00:00Z")],
        "gone": [_row("2026-01-01T00:00:00Z")],
        "old": [_row("2026-01-01T00:00:00Z")],
    }
    plan = plan_sync([current, changed, archived], stored, "text-embedding-3-small", 1536)
    assert plan.skip_ids == ["keep"]
    assert [policy.id for policy in plan.refresh] == ["edit"]
    assert set(plan.delete_ids) == {"gone", "old"}


def test_a_page_with_a_null_embedding_is_not_already_current():
    policy = Policy("p", "Cold chain", last_edited_time="2026-01-01T00:00:00Z")
    stored = {
        "p": [
            StoredChunk(
                0,
                "abc",
                last_edited_time="2026-01-01T00:00:00Z",
                embedding_model="openai/text-embedding-3-small",
                embedding_dimensions=1536,
                has_embedding=False,
            )
        ]
    }
    plan = plan_sync([policy], stored, "openai/text-embedding-3-small", 1536)
    assert plan.skip_ids == []
    assert [item.id for item in plan.refresh] == ["p"]
    assert plan.unembedded == 1
    different_model = StoredChunk(
        0,
        "abc",
        last_edited_time="2026-01-01T00:00:00Z",
        embedding_model="text-embedding-3-small",
        embedding_dimensions=1536,
        has_embedding=True,
    )
    plan = plan_sync([policy], {"p": [different_model]}, "openai/text-embedding-3-small", 1536)
    assert plan.skip_ids == []
    assert plan.unembedded == 0


def test_same_words_are_not_reembedded_but_a_new_model_is():
    chunks = chunk_policy("Cold chain", "# Fridge temperatures\n\nRecord the minimum daily.")
    stored = [
        StoredChunk(
            chunk.chunk_index,
            chunk.content_hash,
            embedding_model="text-embedding-3-small",
            embedding_dimensions=1536,
        )
        for chunk in chunks
    ]
    assert same_content(chunks, stored, "text-embedding-3-small", 1536)
    assert not same_content(chunks, stored, "other-model", 1536)
    stored[0].has_embedding = False
    assert not same_content(chunks, stored, "text-embedding-3-small", 1536)


def _row(edited):
    return StoredChunk(
        chunk_index=0,
        content_hash="abc",
        last_edited_time=edited,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=1536,
    )
