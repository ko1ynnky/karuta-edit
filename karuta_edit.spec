# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec ファイル（Windows でのビルド用）。

ビルド方法:
    pyinstaller karuta_edit.spec --noconfirm

完成物: dist/karuta-edit/ フォルダ（中に karuta-edit.exe）
配布時はこの dist/karuta-edit フォルダごと zip にして渡す。
"""

import os

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = []
binaries = []
hiddenimports = []

# 依存パッケージのデータ・バイナリ・隠れ import をまとめて収集
for pkg in (
    "streamlit",
    "altair",
    "moviepy",
    "soundfile",
    "matplotlib",
    "pandas",
    "numpy",
    "tqdm",
    "ffmpeg",  # ffmpeg-python
    "sherpa_onnx",  # 読まれた歌の聞き分け (音声認識)。onnxruntime などの共有ライブラリを含む
    "pykakasi",  # 聞き取った言葉をかなにする。辞書データを含む
):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Streamlit はバージョン情報(metadata)を実行時に参照するため明示的に同梱
datas += copy_metadata("streamlit")

# アプリ本体。streamlit_app.py は Streamlit がファイルとして読み込むので、PyInstaller は
# 中の import をたどらない。足したファイルは tests/test_build_spec.py が漏れを見つける
APP_FILES = [
    "streamlit_app.py",
    "card_strip.py",
    "file_dialog.py",
    "hyakunin_isshu.py",
    "offline_app.py",
    "page_parts.py",
    "poem_id.py",
    "reader_voice.py",
    "utils.py",
]
datas += [(f, ".") for f in APP_FILES]
datas += [
    ("static", "static"),  # 札の筆文字 (server.enableStaticServing で配る)
    (".streamlit/config.toml", ".streamlit"),
]
# 各ファイルの import (標準ライブラリの bz2・tarfile など) も同梱させるため、モジュールとして解析させる
hiddenimports += [os.path.splitext(f)[0] for f in APP_FILES]

# 同梱 ffmpeg（ビルド前に ./ffmpeg/ へ ffmpeg.exe と ffprobe.exe を置く）
_ffmpeg_dir = "ffmpeg"
if os.path.isdir(_ffmpeg_dir):
    for _fn in os.listdir(_ffmpeg_dir):
        _src = os.path.join(_ffmpeg_dir, _fn)
        if os.path.isfile(_src):
            binaries += [(_src, "ffmpeg")]


a = Analysis(
    ["run_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="karuta-edit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 起動ログ・エラーが見えるよう黒い窓を残す（安定運用優先）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="karuta-edit",
)
