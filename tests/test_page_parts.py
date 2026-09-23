"""画面の共通部品の仕様: 手順の表示と、作業が消えるときのページ離脱の確認。"""
from page_parts import needs_leave_guard, stepper_html


def test_leaving_is_confirmed_while_review_or_export_results_would_be_lost():
    assert needs_leave_guard(state=2, saved=False) is True   # 確認中 (解析の結果が消える)
    assert needs_leave_guard(state=3, saved=False) is True   # 書き出し中


def test_leaving_is_confirmed_on_the_done_screen_until_the_video_is_saved():
    assert needs_leave_guard(state=4, saved=False) is True
    assert needs_leave_guard(state=4, saved=True) is False


def test_leaving_is_free_before_analysis():
    assert needs_leave_guard(state=1, saved=False) is False


def test_stepper_marks_the_current_step_and_the_finished_ones():
    html = stepper_html(current=3)
    assert html.count('aria-current="step"') == 1
    assert html.index("確認") > html.index('aria-current="step"') > html.index("解析")
    assert html.count("完了した手順") == 2  # 動画を選ぶ・解析


def test_stepper_shows_every_step_finished_on_the_done_screen():
    html = stepper_html(current=5)
    assert 'aria-current="step"' not in html
    assert html.count("完了した手順") == 4
