"""Administrator detection and relaunch (UAC)."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

NO_ELEVATE_ENV = "WASAPI_RECORDER_NO_ELEVATE"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - non Windows
        return False


def _command_line() -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        exe = sys.executable
        args = sys.argv[1:]
    else:
        exe = sys.executable
        args = [os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    return exe, subprocess.list2cmdline(args)


def relaunch_as_admin() -> bool:
    """Start this program again with the UAC prompt; True if it was started."""

    exe, params = _command_line()
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, os.getcwd(), 1
        )
    except Exception:
        return False
    return int(result) > 32


def show_error(title: str, message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:
        print("%s: %s" % (title, message), file=sys.stderr)


def ensure_admin(allow_prompt: bool = True) -> bool:
    """Return True when running elevated (or elevation was accepted)."""

    if is_admin():
        return True
    if os.environ.get(NO_ELEVATE_ENV) == "1":
        return True
    if not allow_prompt:
        return False
    if relaunch_as_admin():
        return False  # a new process is starting; this one should exit
    show_error(
        "需要管理员权限",
        "本程序需要以管理员身份运行才能稳定访问音频设备。\n"
        "请在右键菜单中选择「以管理员身份运行」，或在弹出 UAC 提示时选择「是」。",
    )
    return False
