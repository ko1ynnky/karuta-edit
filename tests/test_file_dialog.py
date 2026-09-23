"""ファイル選択ダイアログの仕様: OS ごとに、別のプロセスでダイアログを開くコマンドを選ぶ。"""
from file_dialog import dialog_command


def test_macos_uses_osascript():
    assert dialog_command("darwin", frozen=False, has_tkinter=True, python="py")[0] == "osascript"


def test_python_with_tkinter_opens_tkinter_in_a_child_python():
    cmd = dialog_command("win32", frozen=False, has_tkinter=True, python="python.exe")
    assert cmd[:2] == ["python.exe", "-c"]


def test_standalone_windows_build_does_not_start_itself_again():
    # 配布版では sys.executable がアプリ本体なので、"-c" を付けて呼ぶとアプリがもう1つ起動する
    cmd = dialog_command("win32", frozen=True, has_tkinter=True, python="karuta-edit.exe")
    assert cmd[0] == "powershell"
    assert "karuta-edit.exe" not in cmd


def test_windows_without_tkinter_uses_powershell():
    assert dialog_command("win32", frozen=False, has_tkinter=False, python="python.exe")[0] == "powershell"


def test_linux_without_tkinter_uses_zenity():
    assert dialog_command("linux", frozen=False, has_tkinter=False, python="python")[0] == "zenity"
