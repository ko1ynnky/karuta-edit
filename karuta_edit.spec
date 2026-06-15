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
):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Streamlit はバージョン情報(metadata)を実行時に参照するため明示的に同梱
datas += copy_metadata("streamlit")

# アプリ本体スクリプト群（exe 展開先のルートに配置）
datas += [
    ("streamlit_app.py", "."),
    ("utils.py", "."),
    ("offline_app.py", "."),
    (".streamlit/config.toml", ".streamlit"),
]

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
