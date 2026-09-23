"""配布版の起動の仕様: Streamlit をどの設定で動かすか。"""
from run_app import streamlit_argv


def test_standalone_app_does_not_watch_source_files():
    # 配布版では読み込んだライブラリまで見張りの対象になり、解析のあとは終了の合図 (SIGTERM) でも
    # 止まらなくなった (2026-09-23 に確認)。配布版は中身を書き換えないので、見張りは要らない
    assert "--server.fileWatcherType=none" in streamlit_argv("/app", headless=False)


def test_browser_opens_unless_headless():
    assert "--server.headless=false" in streamlit_argv("/app", headless=False)
    assert "--server.headless=true" in streamlit_argv("/app", headless=True)


def test_runs_the_bundled_app_script():
    argv = streamlit_argv("/app", headless=False)
    assert argv[:3] == ["streamlit", "run", "/app/streamlit_app.py"]
