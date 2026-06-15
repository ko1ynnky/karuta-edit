@echo off
REM ============================================================
REM  karuta-edit Windows exe ビルドスクリプト
REM  Windows 上で、このファイルをダブルクリックするだけで
REM  dist\karuta-edit\karuta-edit.exe を生成します。
REM
REM  事前準備:
REM    1. Python 3.12 をインストール済みであること（PATH追加済み）
REM    2. このスクリプトと同じフォルダに ffmpeg フォルダを作り、
REM       ffmpeg.exe と ffprobe.exe を入れておくこと（exeに同梱されます）
REM ============================================================

setlocal
cd /d "%~dp0"

echo [1/4] 仮想環境を作成します...
if not exist ".venv_build" (
    py -3.12 -m venv .venv_build || python -m venv .venv_build
)

echo [2/4] 依存パッケージと PyInstaller をインストールします...
call .venv_build\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

echo [3/4] exe をビルドします（数分かかります）...
pyinstaller karuta_edit.spec --noconfirm

echo [4/4] 完了です。
echo.
echo   出来上がり: dist\karuta-edit\karuta-edit.exe
echo   配布する場合は dist\karuta-edit フォルダごと zip にしてください。
echo.
pause
