"""Web版UIの仕様: 読手の声で候補を絞るか、読まれた歌を特定するかを解析前に選べる。"""
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


def test_poem_identification_toggle_is_shown_and_on_by_default():
    toggle = _app().checkbox(key="identify_poems")
    assert toggle.value is True
    assert "歌" in toggle.label


def test_poem_identification_toggle_explains_download_and_time():
    toggle = _app().checkbox(key="identify_poems")
    assert "ダウンロード" in toggle.help
    assert "MB" in toggle.help


def test_poem_identification_toggle_can_be_turned_off_before_analysis():
    at = _app()
    at.checkbox(key="identify_poems").uncheck().run()
    assert at.checkbox(key="identify_poems").value is False
    assert not at.exception


# --- レビュー画面 (State 2) ---

def _review_app(tmp_path, unidentified=(), identified=True):
    """4候補のレビュー画面。unidentified に挙げた番号 (1始まり) だけ歌を特定できなかったとする。"""
    import numpy as np
    import soundfile as sf
    from poem_id import Reading

    wav = tmp_path / "audio.wav"
    sf.write(wav, np.full(16000 * 60, 0.01, dtype="float32"), 16000)  # 無音だと st.audio の正規化が0で割る
    scores = [(100, 5000), (200, 5000), (300, 5000), (400, 5000)]
    readings = {
        idx: Reading(idx / 10, None, None, poem=None if n in unidentified else 17, source=None if n in unidentified else "kami")
        for n, (idx, _) in enumerate(scores, 1)
    } if identified else {}
    at = AppTest.from_file("streamlit_app.py", default_timeout=30)
    for key, value in {
        "state": 2, "tmpdir": str(tmp_path), "input_video": str(tmp_path / "v.mp4"), "source_name": "v.mp4",
        "audio_path": str(wav), "waveform": np.zeros(600), "sorted_scores": scores,
        "segment_enabled": {idx: True for idx, _ in scores}, "preview_clips": {}, "review_idx": 0,
        "readings": readings,
    }.items():
        at.session_state[key] = value
    at.run()
    return at


def _labels(buttons):
    return [b.label.removeprefix(">> ").split()[0] for b in buttons if "#" in b.label]


def _scene_labels(at):
    """全シーン一覧の、折りたたみの外に並んだ候補。"""
    folded = set(_folded_labels(at))
    return [label for label in _labels(at.sidebar.button) if label not in folded]


def _folded_labels(at):
    return [label for e in at.sidebar.expander for label in _labels(e.button)]


def _current(at):
    return next(m.value.split("**")[1] for m in at.markdown if m.value.startswith("**#"))


def _click(at, label):
    next(b for b in at.button if b.label == label).click().run()


def test_auto_advance_is_off_by_default(tmp_path):
    at = _review_app(tmp_path, identified=False)
    assert at.checkbox(key="auto_advance").value is False


def test_unidentified_filter_is_on_by_default_and_narrows_scene_list(tmp_path):
    at = _review_app(tmp_path, unidentified=(2, 4))
    toggle = at.checkbox(key="only_unidentified")
    assert toggle.value is True
    assert "2件" in toggle.label
    assert _scene_labels(at) == ["#2", "#4"]
    assert _folded_labels(at) == ["#1", "#3"]  # 特定できた候補は折りたたみの中に残る


def test_scene_list_shows_all_candidates_when_filter_is_off(tmp_path):
    at = _review_app(tmp_path, unidentified=(2, 4))
    at.checkbox(key="only_unidentified").uncheck().run()
    assert _scene_labels(at) == ["#1", "#2", "#3", "#4"]
    assert _folded_labels(at) == []


def test_narrowed_review_moves_between_unidentified_candidates_only(tmp_path):
    at = _review_app(tmp_path, unidentified=(2, 4))
    assert _current(at) == "#2"
    _click(at, "はい")
    assert _current(at) == "#4"
    _click(at, "← 戻る")
    assert _current(at) == "#2"
    _click(at, "いいえ")
    _click(at, "スキップ →")
    assert not any(m.value.startswith("**#") for m in at.markdown)
    assert any("確認が完了" in i.value for i in at.info)
    assert at.session_state.segment_enabled[200] is False


def test_review_says_so_when_every_poem_is_identified(tmp_path):
    at = _review_app(tmp_path, unidentified=())
    assert any("特定できなかった候補はありません" in i.value for i in at.info)


def test_unidentified_filter_is_hidden_without_poem_identification(tmp_path):
    at = _review_app(tmp_path, identified=False)
    assert "only_unidentified" not in [c.key for c in at.checkbox]
    assert _scene_labels(at) == ["#1", "#2", "#3", "#4"]
    assert _current(at) == "#1"


def test_choices_are_kept_while_the_filter_is_switched(tmp_path):
    at = _review_app(tmp_path, unidentified=(2, 4))
    _click(at, "いいえ")
    at.checkbox(key="only_unidentified").uncheck().run()
    assert at.session_state.segment_enabled == {100: True, 200: False, 300: True, 400: True}
    _click(at, "全解除")
    at.checkbox(key="only_unidentified").check().run()
    at.checkbox(key="only_unidentified").uncheck().run()
    assert at.session_state.segment_enabled == {100: False, 200: False, 300: False, 400: False}
    assert {k: at.checkbox(key=f"sb_cb_{k}").value for k in (100, 200, 300, 400)} == {
        100: False, 200: False, 300: False, 400: False,
    }
