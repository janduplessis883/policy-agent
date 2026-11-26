from nathan.chunks import EMBED_TOKEN_LIMIT, MAX_WORDS, chunk_policy, estimate_tokens


def test_sections_stay_separate_and_tiny_ones_merge():
    text = """
# Fridge temperatures

Record the vaccine fridge minimum and maximum twice each working day in the log book.

# Note

See above.
"""
    chunks = chunk_policy("Cold chain", text)
    assert len(chunks) == 1
    assert chunks[0].heading == "Fridge temperatures"
    assert "See above." in chunks[0].text
    assert chunks[0].embed_text.startswith("Cold chain\nFridge temperatures\n")
    assert chunks[0].chunk_index == 0
    assert len(chunks[0].content_hash) == 64


def test_long_section_is_split_with_overlap_and_keeps_order():
    words = " ".join(f"word{i}" for i in range(MAX_WORDS + 80))
    chunks = chunk_policy("Cold chain", f"# Fridge temperatures\n\n{words}")
    assert len(chunks) >= 2
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.heading == "Fridge temperatures" for chunk in chunks)
    assert all(len(chunk.text.split()) <= MAX_WORDS for chunk in chunks)
    first = set(chunks[0].text.split()[-40:])
    second = set(chunks[1].text.split()[:40])
    assert first & second


def test_blank_and_illegal_characters_are_not_embedded():
    chunks = chunk_policy(
        "Cold chain",
        "# Blank\n\n   \x00\n\n# Fridge temperatures\n\nRecord the minimum every day in the log.",
    )
    assert chunks
    assert all(chunk.text.strip() for chunk in chunks)
    assert all("\x00" not in chunk.embed_text for chunk in chunks)
    assert all("\ud800" not in chunk.embed_text for chunk in chunks)
    surrogates = chunk_policy("Cold chain", "# Fridge\n\nRecord\ud800 the minimum every day.")
    assert surrogates
    assert "\ud800" not in surrogates[0].embed_text


def test_token_count_falls_back_to_about_two_characters(monkeypatch):
    from nathan import chunks

    monkeypatch.setattr(chunks, "_encoder_failed", True)
    monkeypatch.setattr(chunks, "_encoder", None)
    text = "a" * 20_000
    assert chunks.count_tokens(text) == 10_000
    parts = chunks.split_for_limit(text, 8000)
    assert len(parts) >= 2
    assert all(len(part) <= 16_000 for part in parts)


def test_a_dense_chunk_over_8192_tokens_is_split_on_token_boundaries():
    import tiktoken

    encoder = tiktoken.get_encoding("cl100k_base")
    blob = "字" * 12_000
    assert len(encoder.encode(blob)) > 8192
    chunks = chunk_policy("Cold chain", f"# Fridge temperatures\n\n{blob}")
    assert len(chunks) >= 2
    assert all(len(encoder.encode(chunk.embed_text)) <= 8000 for chunk in chunks)
    assert all(estimate_tokens(chunk.embed_text) <= EMBED_TOKEN_LIMIT for chunk in chunks)
