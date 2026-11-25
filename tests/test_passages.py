from nathan.passages import FULL_TEXT_LIMIT, pack_text, section_in_text


def test_section_must_appear_in_the_retrieved_text():
    text = "# Fridge temperatures\nRecord the cold chain minimum every day."
    assert section_in_text("Fridge temperatures", text)
    assert section_in_text("not stated", "Anything at all")
    assert section_in_text("Not stated.", text)
    assert not section_in_text("Imaginary section", text)


def test_short_policy_is_not_excerpted():
    text = "# Purpose\n\nKeep vaccines cold.\n"
    packed, scope, sections, omitted = pack_text("cold chain", text)
    assert packed == text
    assert scope == "full"
    assert sections == ["Purpose"]
    assert omitted == []


def test_long_policy_keeps_the_matching_section_and_names_the_rest():
    parts = ["# Purpose\n\nThis policy sets out responsibilities.\n"]
    parts.append("# Fridge temperatures\n\nRecord the cold chain minimum and maximum every day.\n")
    for index in range(40):
        parts.append(f"# Unrelated {index}\n\n" + ("administrative wording " * 40))
    text = "\n".join(parts)
    assert len(text) > FULL_TEXT_LIMIT
    packed, scope, sections, omitted = pack_text(
        "What is the cold chain fridge temperature procedure?", text
    )
    assert scope == "relevant_sections"
    assert "Fridge temperatures" in packed
    assert "Purpose" in packed
    assert "not evidence of absence" in packed
    assert sections[0] == "Purpose"
    assert omitted
    assert all(title.startswith("Unrelated") for title in omitted)
    assert "Unrelated 10" not in packed


def test_long_policy_with_no_matching_section_stays_complete():
    text = "# Purpose\n\n" + ("general administration " * 1200)
    assert len(text) > FULL_TEXT_LIMIT
    packed, scope, sections, omitted = pack_text("cold chain fridge", text)
    assert scope == "full"
    assert packed == text
    assert sections == ["Purpose"]
    assert omitted == []
