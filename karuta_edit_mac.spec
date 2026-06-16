# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec ファイル（macOS での .app ビルド用）。

Apple Silicon (arm64) 専用にネイティブビルドする。
(Rosetta 不要・高速・軽量。Intel Mac は対象外)

ビルド方法（arm64 ツールチェーン上で実行すること）:
    pyinstaller karuta_edit_mac.spec --noconfirm

完成物: dist/karuta-edit.app
配布時はこの .app を zip にして渡す。
受け取った人は初回のみ「右クリック → 開く」で起動する。
"""

import os

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = []
binaries = []
hiddenimports = []

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

datas += copy_metadata("streamlit")

datas += [
    ("streamlit_app.py", "."),
    ("utils.py", "."),
    ("offline_app.py", "."),
    (".streamlit/config.toml", ".streamlit"),
]

# 同梱 ffmpeg（ビルド前に ./ffmpeg/ へ arm64 版 ffmpeg と ffprobe を置く）
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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
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

app = BUNDLE(
    coll,
    name="karuta-edit.app",
    icon=None,
    bundle_identifier="jp.karuta.edit",
    info_plist={
        "CFBundleName": "karuta-edit",
        "CFBundleDisplayName": "かるた動画自動編集",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.15.0",
    },
)
