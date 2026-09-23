"""画面の共通部品の仕様: 手順の表示と、作業が消えるときのページ離脱の確認。"""
from page_parts import duration_jp, export_progress_text, needs_leave_guard, stepper_html


def test_leaving_is_confirmed_while_review_or_export_results_would_be_lost():
    assert needs_leave_guard(state=2, saved=False) is True   # 確認中 (解析の結果が消える)
    assert needs_leave_guard(state=3, saved=False) is True   # 書き出し中


def test_leaving_is_confirmed_on_the_done_screen_until_the_video_is_saved():
    assert needs_leave_guard(state=4, saved=False) is True
    assert needs_leave_guard(state=4, saved=True) is False


def test_leaving_is_free_before_analysis():
    assert needs_leave_guard(state=1, saved=False) is False


def test_leaving_is_confirmed_while_analyzing():
    assert needs_leave_guard(state=1, saved=False, analyzing=True) is True


def test_stepper_marks_the_current_step_and_the_finished_ones():
    html = stepper_html(current=3)
    assert html.count('aria-current="step"') == 1
    assert html.index("確認") > html.index('aria-current="step"') > html.index("解析")
    assert html.count("完了した手順") == 2  # 動画を選ぶ・解析


def test_stepper_shows_every_step_finished_on_the_done_screen():
    html = stepper_html(current=5)
    assert 'aria-current="step"' not in html
    assert html.count("完了した手順") == 4


def test_durations_are_written_in_japanese_units():
    assert duration_jp(3343) == "55分43秒"
    assert duration_jp(446.4) == "7分26秒"
    assert duration_jp(3723) == "1時間2分3秒"
    assert duration_jp(45) == "45秒"


def test_export_progress_shows_the_percentage_and_the_time_left():
    assert export_progress_text(0.5, elapsed=30) == "50%　残り約30秒"
    assert export_progress_text(0.25, elapsed=60) == "25%　残り約3分"


def test_export_progress_does_not_guess_the_time_left_at_the_start():
    assert export_progress_text(0.0, elapsed=0) == "準備しています"
    # 出だしは準備の時間が混ざり、見積もりが大きく外れる (第2試合で 0% のとき「残り約9分」、実際は約2分)
    assert export_progress_text(0.03, elapsed=6) == "3%　残り時間を見積もっています"


def test_export_progress_says_it_is_finishing_after_the_scenes_are_cut():
    # 場面の切り出しは 97% まで。そのあとは音声合わせとチャプターで、残り時間を見積もれない
    assert export_progress_text(0.98, elapsed=100) == "98%　仕上げています"
