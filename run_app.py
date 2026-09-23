"""PyInstaller でexe化するための起動用ランチャー。

このスクリプトを exe のエントリポイントにする。
exe 実行時に Streamlit サーバーを起動し、ブラウザでアプリを開く。
同梱した ffmpeg / ffprobe があれば PATH の先頭に追加する。

--self-test を付けると、アプリを起動せずに、配布版に必要なものが揃っているかを確かめる
(リリースのワークフローが、配る前に実行する)。
"""

import importlib
import os
import subprocess
import sys


def _base_dir() -> str:
    """データファイルの基準ディレクトリを返す。

    PyInstaller でフリーズされている場合は展開先 (sys._MEIPASS) を、
    通常の Python 実行時はこのファイルのあるディレクトリを返す。
    """
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def _check_https(url: str) -> None:
    import urllib.error
    import urllib.request

    # 証明書の検証まで通れば十分なので、HTTP のエラー (403 など) は問題にしない
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30)
    except urllib.error.HTTPError:
        pass


def _check_start_screen(base: str) -> None:
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(os.path.join(base, "streamlit_app.py"), default_timeout=60)
    at.run()
    if at.exception:
        raise RuntimeError(at.exception[0].message)


def self_test(base: str) -> list[str]:
    """配布版に必要なものが揃っているかを確かめ、問題の一覧を返す (空なら問題なし)。

    ビルドは、足りないファイルやライブラリがあっても通り、起動して画面を開いたときに初めて落ちる。
    """
    problems = []

    def check(name, fn):
        try:
            fn()
            print(f"ok  {name}", flush=True)
        except Exception as e:  # 1つ失敗しても、残りも確かめて一覧にする
            problems.append(f"{name}: {e!r}")
            print(f"NG  {name}: {e!r}", flush=True)

    modules = sorted(
        f[:-3] for f in os.listdir(base)
        if f.endswith(".py") and f not in ("streamlit_app.py", "run_app.py")
    )
    check(f"アプリのモジュール ({', '.join(modules)})", lambda: [importlib.import_module(m) for m in modules])
    check("音声認識 (sherpa_onnx)", lambda: importlib.import_module("sherpa_onnx"))
    check("かな変換 (pykakasi)", lambda: importlib.import_module("pykakasi").kakasi().convert("百人一首"))
    for tool in ("ffmpeg", "ffprobe"):
        check(tool, lambda tool=tool: subprocess.run([tool, "-version"], check=True, capture_output=True))
    check("札の筆文字", lambda: open(os.path.join(base, "static", "fonts", "YujiSyuku-kana.woff"), "rb").close())
    check("画面の設定", lambda: open(os.path.join(base, ".streamlit", "config.toml"), "rb").close())
    check("音声認識モデルの取得先に HTTPS でつながる", lambda: _check_https(importlib.import_module("poem_id").MODEL_URL))
    check("最初の画面を描く", lambda: _check_start_screen(base))
    return problems


def streamlit_argv(base: str, headless: bool) -> list[str]:
    return [
        "streamlit",
        "run",
        os.path.join(base, "streamlit_app.py"),
        "--global.developmentMode=false",
        f"--server.headless={'true' if headless else 'false'}",
        # 配布版では、読み込んだライブラリまで変更の見張りの対象になり、解析のあとは
        # 終了の合図 (SIGTERM) でも止まらなくなった。配布版は中身を書き換えないので見張らない
        "--server.fileWatcherType=none",
    ]


def main() -> None:
    base = _base_dir()

    # 同梱した ffmpeg.exe / ffprobe.exe を PATH 先頭に追加（あれば）
    ffmpeg_dir = os.path.join(base, "ffmpeg")
    if os.path.isdir(ffmpeg_dir):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    # streamlit_app.py からの相対 import / 相対パスを解決できるよう作業Dirを合わせる
    os.chdir(base)

    if "--self-test" in sys.argv[1:]:
        # CI の Windows ではパイプの文字コードが cp1252 になり、日本語を出すと落ちる
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if base not in sys.path:
            sys.path.insert(0, base)
        problems = self_test(base)
        print("自己診断: " + ("問題なし" if not problems else f"{len(problems)}件の問題"), flush=True)
        sys.exit(1 if problems else 0)

    import streamlit.web.cli as stcli

    # KARUTA_EDIT_HEADLESS=1 はブラウザを開かない (CI で起動を確かめるとき用)
    sys.argv = streamlit_argv(base, headless=os.environ.get("KARUTA_EDIT_HEADLESS") == "1")
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
