#!/usr/bin/env bash
# ============================================================
#  karuta-edit macOS .app ビルドスクリプト
#  Apple Silicon (arm64) 専用にネイティブビルドする。
#  (Rosetta 不要・高速・軽量。Intel Mac は対象外)
#
#  使い方:
#      cd karuta-edit
#      ./build_macos.sh
#
#  完成物: dist/karuta-edit.app
#  配布: GitHub の Release ワークフロー (.github/workflows/release.yml) で作る。
#        初めて開くときの手順は packaging/macos/ご一読ください.html にある。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_VERSION="3.12"
BUILD_VENV=".venv_mac_build"
UV_BIN="$HOME/.local/bin/uv"

# arm64 専用ビルド。Intel Mac 上では実行しないこと。
if [ "$(uname -m)" != "arm64" ]; then
    echo "エラー: このスクリプトは Apple Silicon (arm64) 上で実行してください。" >&2
    exit 1
fi

echo "[1/5] uv (Python 取得用) を準備します..."
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$UV_BIN" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

echo "[2/5] arm64 版 Python ${PYTHON_VERSION} を取得します..."
uv python install "${PYTHON_VERSION}"
PYX="$(uv python find "${PYTHON_VERSION}")"
echo "      使用Python: $PYX"

echo "[3/5] ビルド用 venv を作成し依存をインストールします..."
"$PYX" -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --upgrade pip
# 版は constraints-build.txt で、開発環境で確かめた版に固定する
"$BUILD_VENV/bin/python" -m pip install -r requirements.txt -c constraints-build.txt pyinstaller

echo "[4/5] arm64 版 ffmpeg / ffprobe を用意します (未取得なら DL)..."
mkdir -p ffmpeg
if [ ! -f ffmpeg/ffmpeg ]; then
    curl -sL -o /tmp/ffmpeg_arm.zip  "https://www.osxexperts.net/ffmpeg81arm.zip"
    ( cd ffmpeg && unzip -oq /tmp/ffmpeg_arm.zip ) && rm -f /tmp/ffmpeg_arm.zip
fi
if [ ! -f ffmpeg/ffprobe ]; then
    curl -sL -o /tmp/ffprobe_arm.zip "https://www.osxexperts.net/ffprobe81arm.zip"
    ( cd ffmpeg && unzip -oq /tmp/ffprobe_arm.zip ) && rm -f /tmp/ffprobe_arm.zip
fi
rm -rf ffmpeg/__MACOSX
chmod +x ffmpeg/ffmpeg ffmpeg/ffprobe

echo "[5/5] .app をビルドします (数分かかります)..."
"$BUILD_VENV/bin/pyinstaller" karuta_edit_mac.spec --noconfirm

echo ""
echo "完了です。"
echo "  出来上がり: dist/karuta-edit.app"
echo "  配布用の zip は、GitHub の Release ワークフローで作ります。"
