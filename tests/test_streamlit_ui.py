"""Web版UIの仕様: 読手の声で候補を絞るかを解析前に選べる。"""
from streamlit.testing.v1 import AppTest


def _app():
    at = AppTest.from_file("streamlit_app.py", default_timeout=30)
    at.run()
    return at


def test_reader_voice_toggle_is_shown_and_off_by_default():
    at = _app()
    toggle = at.checkbox(key="use_reader_voice")
    assert toggle.value is False
    assert "読手の声" in toggle.label


def test_reader_voice_toggle_explains_when_to_use_it_in_tooltip():
    toggle = _app().checkbox(key="use_reader_voice")
    assert toggle.help
    assert "空調" in toggle.help
    assert "オフ" in toggle.help


def test_reader_voice_toggle_can_be_turned_on_before_analysis():
    at = _app()
    at.checkbox(key="use_reader_voice").check().run()
    assert at.checkbox(key="use_reader_voice").value is True
    assert not at.exception
