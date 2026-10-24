"""Entry point: elevate, open the audio system, show the window."""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _enable_dpi_awareness() -> None:
    """Draw at native resolution instead of letting Windows blur-stretch the window."""

    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _selftest() -> int:
    """Headless bundling check: are PortAudio and the WASAPI host reachable?"""

    import json

    import pyaudiowpatch as pyaudio

    import audio_devices

    pa = pyaudio.PyAudio()
    try:
        found = audio_devices.discover(pa)
        payload = {
            "frozen": bool(getattr(sys, "frozen", False)),
            "system": found["system"].short_name if found["system"] else None,
            "system_rate": found["system"].rate if found["system"] else None,
            "mic": found["mic"].short_name if found["mic"] else None,
            "errors": found["errors"],
            "host_apis": [pa.get_host_api_info_by_index(i)["name"]
                          for i in range(pa.get_host_api_count())],
            "open": {},
        }
        if "--open" in sys.argv:
            import time

            import recorder as rec_mod

            for mode in ("system", "mic", "mix"):
                rec = rec_mod.Recorder(pa)
                rec.devices = found
                try:
                    rec.start(mode)
                    time.sleep(0.4)
                    sess = rec.stop()
                    payload["open"][mode] = {"ok": True, "seconds": round(sess.duration, 2)}
                    rec_mod.cleanup_session(sess)
                except Exception as exc:  # noqa: BLE001
                    payload["open"][mode] = {"ok": False, "error": str(exc)}
                    try:
                        rec.cancel()
                    except Exception:
                        pass
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if found["system"] is not None else 2
    finally:
        pa.terminate()


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()

    if "--no-elevate" in sys.argv:
        os.environ["WASAPI_RECORDER_NO_ELEVATE"] = "1"

    import admin

    if not admin.is_admin() and os.environ.get("WASAPI_RECORDER_NO_ELEVATE") != "1":
        if admin.relaunch_as_admin():
            return 0
        admin.show_error(
            "需要管理员权限",
            "本程序需要以管理员身份运行才能稳定访问音频设备。\n"
            "请在右键菜单中选择「以管理员身份运行」，或在 UAC 提示中选择「是」。",
        )
        return 1

    import tkinter as tk

    _enable_dpi_awareness()

    import pyaudiowpatch as pyaudio

    try:
        pa = pyaudio.PyAudio()
    except Exception as exc:  # pragma: no cover - depends on the machine
        admin.show_error("音频系统不可用", "无法初始化音频系统：\n%s" % exc)
        return 1

    from ui import RecorderApp

    root = tk.Tk()
    try:
        RecorderApp(root, pa)
    except Exception as exc:  # pragma: no cover
        admin.show_error("启动失败", str(exc))
        pa.terminate()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
