"""Chat lines must not show agent prefixes or raw catalog ids."""

from app.copy import chat_line, hide_ids, strip_speaker


def test_maya_prefix_is_stripped_and_she_speaks_in_first_person():
    assert strip_speaker("Maya", "Maya: I found earbuds.") == "I found earbuds."
    line = chat_line("Maya", "Maya found earbuds in the sample catalog.")
    assert not line.lower().startswith("maya")
    assert line.startswith("I found")


def test_raw_ids_become_names():
    text = hide_ids("P041 is safer than S24.", {"P041": "Laptop Stand", "S24": "QuickShip"})
    assert text == "Laptop Stand is safer than QuickShip."
    assert "P041" not in hide_ids("Skip P041 please.")
