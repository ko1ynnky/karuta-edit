"""配布版の起動の仕様: Streamlit をどの設定で動かし、ブラウザをいつ開くか。"""
from run_app import open_browser_when_ready, pick_port, streamlit_argv


def test_streamlit_never_waits_for_the_first_run_email_prompt():
    # headless でないと、Streamlit は初めての起動でメールアドレスの入力を待つ。ダブルクリックで
    # 開くと答えられず、終了コード 255 ですぐ終わる (ユーザーの Mac で確認、2026-09-24)
    argv = streamlit_argv("/app", port=8501)
    assert "--server.headless=true" in argv
    assert "--server.headless=false" not in argv


def test_standalone_app_does_not_watch_source_files():
    # 配布版では読み込んだライブラリまで見張りの対象になり、解析のあとは終了の合図 (SIGTERM) でも
    # 止まらなくなった (2026-09-23 に確認)。配布版は中身を書き換えないので、見張りは要らない
    assert "--server.fileWatcherType=none" in streamlit_argv("/app", port=8501)


def test_runs_the_bundled_app_script_on_the_chosen_port():
    argv = streamlit_argv("/app", port=8502)
    assert argv[:3] == ["streamlit", "run", "/app/streamlit_app.py"]
    assert "--server.port=8502" in argv


def test_uses_the_first_free_port_from_8501():
    # 2つ目を開いたときは、Streamlit と同じく次の番号にする
    assert pick_port(is_free=lambda port: port >= 8503) == 8503


def test_opens_the_browser_once_the_server_answers():
    answers = iter([False, False, True])
    opened = []
    assert open_browser_when_ready("http://localhost:8501", lambda: next(answers), opened.append,
                                   sleep=lambda _: None) is True
    assert opened == ["http://localhost:8501"]


def test_does_not_open_the_browser_if_the_server_never_answers():
    opened = []
    assert open_browser_when_ready("http://localhost:8501", lambda: False, opened.append,
                                   timeout=3, interval=1, sleep=lambda _: None) is False
    assert opened == []
