"""Web版UIの仕様: 動画を選んでから解析を始め、確認画面で候補を1件ずつ確かめる。"""
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


# --- 開始画面 (動画を選ぶ・解析) ---

def _video(tmp_path, seconds=4):
    """ffmpeg で、音声つきの短い動画を作る。"""
    import shutil
    import subprocess
    import pytest
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が必要")
    path = tmp_path / "match.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=gray:s=320x180:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest", "-y", str(path)],
        check=True,
    )
    return path


def _choose(at, path):
    at.text_input(key="local_path_input").input(str(path)).run()
    return at


def _start_button(at):
    return next(b for b in at.button if b.label == "解析を始める")


def test_analysis_cannot_start_before_a_video_is_chosen():
    assert _start_button(_app()).disabled is True


def test_choosing_a_video_shows_it_without_starting_analysis(tmp_path):
    at = _choose(_app(), _video(tmp_path))
    assert any("match.mp4" in m.value for m in at.markdown)
    assert any("長さ 00:04" in m.value for m in at.markdown)
    assert _start_button(at).disabled is False
    assert at.session_state.state == 1


def test_a_missing_file_is_reported(tmp_path):
    at = _choose(_app(), tmp_path / "nothing.mp4")
    assert any("見つかりません" in e.value for e in at.error)
    assert _start_button(at).disabled is True


def test_another_video_can_be_chosen_again(tmp_path):
    at = _choose(_app(), _video(tmp_path))
    next(b for b in at.button if b.label == "別の動画を選ぶ").click().run()
    assert _start_button(at).disabled is True


def test_settings_are_folded_and_summarized(tmp_path):
    at = _app()
    assert [e.label for e in at.expander] == ["詳細設定"]
    assert any("上の句の1.5秒前から下の句の3.0秒後まで" in c.value for c in at.caption)


def test_starting_analysis_goes_to_the_review_screen(tmp_path):
    # 候補の検出は7秒以上の音声を前提にしている (utils.return_before_scores) ので、15秒にする
    at = _choose(_app(), _video(tmp_path, seconds=15))
    at.checkbox(key="identify_poems").uncheck().run()
    _start_button(at).click().run(timeout=60)
    assert not at.exception
    assert at.session_state.state == 2
    assert at.session_state.clip_range == (1.5, 3.0)


# --- 確認画面 (State 2) ---

def _review_app(tmp_path, unidentified=(), identified=True):
    """4候補の確認画面。unidentified に挙げた番号 (1始まり) だけ歌を特定できなかったとする。"""
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
        "segment_enabled": {idx: True for idx, _ in scores}, "preview_clips": {},
        "readings": readings, "reviewed": set(), "clip_range": (1.5, 3.0),
    }.items():
        at.session_state[key] = value
    at.run()
    return at


def _current(at):
    """確認中の候補番号 (例: "#2")。"""
    return next(m.value.split()[0].strip("*") for m in at.markdown if "元動画" in m.value)


def _click(at, label):
    next(b for b in at.button if b.label == label).click().run()


def test_review_screen_does_not_show_video_selection_or_settings(tmp_path):
    at = _review_app(tmp_path, unidentified=(2,))
    assert len(at.get("file_uploader")) == 0
    assert len(at.slider) == 0
    assert "identify_poems" not in [c.key for c in at.checkbox]


def test_review_starts_at_the_first_card_to_check(tmp_path):
    at = _review_app(tmp_path, unidentified=(2, 4))
    assert _current(at) == "#2"
    assert any("2件を確かめてください" in m.value for m in at.markdown)


def test_removing_and_keeping_move_to_the_next_card_to_check(tmp_path):
    at = _review_app(tmp_path, unidentified=(2, 4))
    _click(at, "外す")
    assert _current(at) == "#4"
    assert at.session_state.segment_enabled[200] is False
    assert 200 in at.session_state.reviewed
    _click(at, "残す")
    assert at.session_state.segment_enabled[400] is True
    assert any("確かめ終わりました" in i.value for i in at.info)


def test_previous_and_next_move_among_cards_to_check(tmp_path):
    at = _review_app(tmp_path, unidentified=(2, 4))
    _click(at, "次の件")
    assert _current(at) == "#4"
    _click(at, "前の件")
    assert _current(at) == "#2"


def test_recommended_decision_is_the_primary_button(tmp_path):
    import numpy as np  # noqa: F401
    at = _review_app(tmp_path, unidentified=(2,))
    keep = next(b for b in at.button if b.label == "残す")
    remove = next(b for b in at.button if b.label == "外す")
    # 歌が分からない候補には、おすすめを出さない
    assert keep.proto.type == remove.proto.type == "secondary"


def test_summary_shows_kept_scenes_and_the_create_button(tmp_path):
    at = _review_app(tmp_path, unidentified=(2,))
    _click(at, "外す")
    assert [m.value for m in at.metric if m.label == "残す場面"] == ["3"]
    assert any(b.label == "短縮版を作る" for b in at.button)


def test_review_says_so_when_every_poem_is_identified(tmp_path):
    at = _review_app(tmp_path, unidentified=())
    assert any("確かめる札はありません" in i.value for i in at.info)


def test_without_identification_every_candidate_is_reviewed_in_order(tmp_path):
    at = _review_app(tmp_path, identified=False)
    assert _current(at) == "#1"
    _click(at, "次の件")
    assert _current(at) == "#2"


def test_without_identification_checking_every_candidate_is_not_demanded(tmp_path):
    # 実際には全候補を通しで確かめる人は少なく、誤検知が少し混ざる前提でそのまま作ることが多い
    at = _review_app(tmp_path, identified=False)
    texts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    assert any("読みの候補が4件見つかりました" in t for t in texts)
    assert any("そのまま短縮版を作れます" in t for t in texts)
    assert not any("確かめてください" in t for t in texts)


def test_current_card_says_whether_it_was_reviewed(tmp_path):
    at = _review_app(tmp_path, identified=False)
    assert any("未確認（このままなら残ります）" in c.value for c in at.caption)
    _click(at, "外す")
    _click(at, "前の件")
    assert _current(at) == "#1"
    assert any("確認済み：外す" in c.value for c in at.caption)


def test_summary_shows_how_many_candidates_were_reviewed(tmp_path):
    at = _review_app(tmp_path, identified=False)
    _click(at, "残す")
    _click(at, "外す")
    assert [m.value for m in at.metric if m.label == "確認済み"] == ["2 / 4"]


def test_auto_advance_is_off_by_default(tmp_path):
    at = _review_app(tmp_path, identified=False)
    assert at.checkbox(key="auto_advance").value is False


# --- 書き出し (State 3) と完了 (State 4) ---

def test_export_makes_the_video_and_shows_the_done_screen(tmp_path):
    import numpy as np
    video = _video(tmp_path, seconds=15)
    scores = [(50, 5000), (110, 5000)]
    at = AppTest.from_file("streamlit_app.py", default_timeout=120)
    for key, value in {
        "state": 3, "tmpdir": str(tmp_path), "input_video": str(video), "source_name": "match.mp4",
        "source_duration": 15.0, "waveform": np.zeros(150), "sorted_scores": scores,
        "segment_enabled": {idx: True for idx, _ in scores}, "preview_clips": {}, "readings": {},
        "reviewed": set(), "clip_range": (1.5, 3.0),
    }.items():
        at.session_state[key] = value
    at.run()
    assert not at.exception
    assert at.session_state.state == 4
    result = at.session_state.result
    assert result["scenes"] == 2
    assert result["chapters"] == 2
    assert result["first_chapter"].startswith("01 元動画 00:00:03.")
    assert 8 < result["duration"] < 10  # 4.5秒 × 2場面
    assert any("短縮版ができました" in m.value for m in at.markdown)


def _done_app(tmp_path, saved=False):
    at = AppTest.from_file("streamlit_app.py", default_timeout=30)
    for key, value in {
        "state": 4, "tmpdir": str(tmp_path), "source_name": "match.mp4", "source_duration": 3343.0,
        "processed_video": b"0" * 1000, "saved": saved,
        "result": {"duration": 446.4, "scenes": 100, "chapters": 87, "first_chapter": "01 元動画 00:01:39.600"},
    }.items():
        at.session_state[key] = value
    at.run()
    return at


def test_done_screen_says_how_much_shorter_the_video_became(tmp_path):
    at = _done_app(tmp_path)
    assert any("55分43秒の試合が、7分26秒になりました" in m.value for m in at.markdown)
    assert any("match_short.mp4" in m.value for m in at.markdown)
    assert [m.value for m in at.metric if m.label == "チャプター"] == ["87"]


def test_done_screen_warns_that_closing_before_saving_loses_the_video(tmp_path):
    texts = [c.value for c in _done_app(tmp_path).caption]
    assert any("保存するまで" in t for t in texts)


def test_starting_over_before_saving_asks_first(tmp_path):
    at = _done_app(tmp_path, saved=False)
    _click(at, "別の動画を短縮する")
    assert at.session_state.state == 4
    assert any("まだ保存していません" in w.value for w in at.warning)
    _click(at, "保存せずに最初に戻る")
    assert at.session_state.state == 1


def test_starting_over_before_saving_can_be_cancelled(tmp_path):
    at = _done_app(tmp_path, saved=False)
    _click(at, "別の動画を短縮する")
    _click(at, "やめる")
    assert at.session_state.state == 4
    assert not at.warning


def test_starting_over_after_saving_goes_straight_back_to_the_start(tmp_path):
    at = _done_app(tmp_path, saved=True)
    _click(at, "別の動画を短縮する")
    assert at.session_state.state == 1


def test_export_screen_can_cover_every_element_of_the_review_screen(tmp_path):
    # 書き出しの間は実行が終わらないので、上書きされなかった確認画面の要素が薄く残ってしまう
    # (2026-09-23 に Chrome で確認)。書き出し画面は LEFTOVER_SLOTS 個の空の要素で上書きする
    from page_parts import LEFTOVER_SLOTS
    for kwargs in (dict(unidentified=(2,)), dict(unidentified=()), dict(identified=False)):
        assert len(_review_app(tmp_path, **kwargs).main.children) <= LEFTOVER_SLOTS


def test_done_screen_explains_merged_chapters_only_when_scenes_were_merged(tmp_path):
    assert any("1つのチャプターにまとめています" in c.value for c in _done_app(tmp_path).caption)
    at = _done_app(tmp_path)
    at.session_state["result"] = {**at.session_state["result"], "chapters": 100}
    at.run()
    assert not any("1つのチャプターにまとめています" in c.value for c in at.caption)
