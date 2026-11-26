from nathan.chunks import MAX_WORDS, chunk_policy


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
