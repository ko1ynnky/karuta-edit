"""PyInstaller でexe化するための起動用ランチャー。

このスクリプトを exe のエントリポイントにする。
exe 実行時に Streamlit サーバーを起動し、ブラウザでアプリを開く。
同梱した ffmpeg / ffprobe があれば PATH の先頭に追加する。
"""

import os
import sys

import streamlit.web.cli as stcli


def _base_dir() -> str:
    """データファイルの基準ディレクトリを返す。

    PyInstaller でフリーズされている場合は展開先 (sys._MEIPASS) を、
    通常の Python 実行時はこのファイルのあるディレクトリを返す。
    """
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    base = _base_dir()

    # 同梱した ffmpeg.exe / ffprobe.exe を PATH 先頭に追加（あれば）
    ffmpeg_dir = os.path.join(base, "ffmpeg")
    if os.path.isdir(ffmpeg_dir):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    # streamlit_app.py からの相対 import / 相対パスを解決できるよう作業Dirを合わせる
    os.chdir(base)

    sys.argv = [
        "streamlit",
        "run",
        os.path.join(base, "streamlit_app.py"),
        "--global.developmentMode=false",
        "--server.headless=false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
