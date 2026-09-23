"""配布版のビルド設定の仕様: アプリを動かすファイルとライブラリが、macOS 版・Windows 版の両方に入る。

streamlit_app.py は Streamlit がファイルとして読み込んで動かすので、PyInstaller は中の import を
たどらない。設定に書き漏らしたファイルやライブラリは、ビルドは通っても起動時に初めて落ちる。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPECS = ["karuta_edit.spec", "karuta_edit_mac.spec"]
APP_FILES = sorted(p.name for p in ROOT.glob("*.py") if p.name != "run_app.py")


@pytest.mark.parametrize("spec", SPECS)
def test_every_app_file_is_bundled(spec):
    text = (ROOT / spec).read_text(encoding="utf-8")
    assert [f for f in APP_FILES if f'"{f}"' not in text] == []


@pytest.mark.parametrize("spec", SPECS)
def test_static_files_and_the_speech_recognition_libraries_are_bundled(spec):
    text = (ROOT / spec).read_text(encoding="utf-8")
    for needed in ('"static"', '"sherpa_onnx"', '"pykakasi"'):
        assert needed in text
