"""動画ファイルを選ぶ、OS のファイル選択ダイアログ。

ブラウザのセキュリティ制約上、ページ内のファイル選択UIからは
ローカルパスを取得できないため、同一マシンで動くこのプロセス側から開く。
Streamlitのスクリプトスレッドからtkinterを直接使うとmacOSで
クラッシュするため、いずれの方式もサブプロセスで実行する。
"""
import importlib.util
import subprocess
import sys

_TK_DIALOG_CODE = """
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askopenfilename(
    title="動画ファイルを選択",
    filetypes=[("動画", "*.mp4 *.mov *.webm *.mkv"), ("すべて", "*.*")],
)
print(path)
"""


def dialog_command(platform: str, frozen: bool, has_tkinter: bool, python: str) -> list[str]:
    if platform == "darwin":
        # macOSはtkinterが未導入のPython環境が多いため、標準のosascriptを使う
        return [
            "osascript", "-e",
            'POSIX path of (choose file with prompt "動画ファイルを選択")',
        ]
    # 配布版 (PyInstaller) の python はアプリ本体で、"-c" を解釈せずアプリをもう1つ起動してしまう
    if has_tkinter and not frozen:
        return [python, "-c", _TK_DIALOG_CODE]
    if platform == "win32":
        return [
            "powershell", "-NoProfile", "-Command",
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.OpenFileDialog; "
            "$d.Filter = '動画|*.mp4;*.mov;*.webm;*.mkv|すべて|*.*'; "
            "if ($d.ShowDialog() -eq 'OK') { $d.FileName }",
        ]
    return [
        "zenity", "--file-selection", "--title=動画ファイルを選択",
        "--file-filter=動画 | *.mp4 *.mov *.webm *.mkv",
    ]


def pick_video_file() -> str | None:
    """選んだファイルのパスを返す。キャンセル時・ダイアログを開けない環境では None。"""
    cmd = dialog_command(
        sys.platform,
        frozen=getattr(sys, "frozen", False),
        has_tkinter=importlib.util.find_spec("tkinter") is not None,
        python=sys.executable,
    )
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
