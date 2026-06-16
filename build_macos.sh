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
#  配布: dist/karuta-edit.app を zip にして渡す。
#        受け取った人は初回のみ「右クリック → 開く」で起動する。
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
"$BUILD_VENV/bin/python" -m pip install -r requirements.txt pyinstaller

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
echo "  配布する場合は dist/karuta-edit.app を zip にしてください。"
echo "  受け取った人は初回だけ「右クリック → 開く」で起動します。"
