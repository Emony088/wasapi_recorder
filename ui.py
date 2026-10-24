"""Main window for the WASAPI recorder."""

from __future__ import annotations

import math
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import pyaudiowpatch as pyaudio

import admin
import audio_devices
import mixing
import player as player_mod
import recorder as recorder_mod
import ui_widgets as w


def format_clock(seconds: float, tenths: bool = True) -> str:
    seconds = max(0.0, float(seconds))
    minutes, rest = divmod(seconds, 60.0)
    if tenths:
        return "%02d:%04.1f" % (int(minutes), rest)
    return "%02d:%02d" % (int(minutes), int(rest))


def db_text(value: float) -> str:
    if value <= 0.0005:
        return "  -∞ dB"
    return "%5.1f dB" % (20 * math.log10(value))


class RecorderApp:
    def __init__(self, root: tk.Tk, pa: "pyaudio.PyAudio") -> None:
        self.root = root
        self.pa = pa
        self.recorder = recorder_mod.Recorder(pa)
        self.devices: dict = {}
        self.session: recorder_mod.Session | None = None
        self.player: player_mod.PreviewPlayer | None = None
        self.state = "idle"
        self.mode = tk.StringVar(value="mix")
        self._closing = False
        self._last_tick = time.perf_counter()
        self._blink = False
        self._gain_sys = 100
        self._gain_mic = 100
        self._export_path = ""
        self._export_progress = 0.0
        self.mode_available: dict[str, bool] = {"system": False, "mic": False, "mix": False}

        self.root.title("WASAPI 录音机")
        self.root.configure(bg=w.BG)
        # Keep the layout grid in 96-dpi pixels: the process is DPI aware (app.py),
        # so point-sized fonts must not grow beyond the boxes that hold them.
        try:
            self.root.tk.call("tk", "scaling", 96.0 / 72.0)
        except Exception:
            pass
        self.root.geometry("1000x748")
        self.root.minsize(940, 720)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Escape>", lambda _e: self._toggle_record() if self.state == "recording" else None)

        self._build()
        self._apply_icon()
        self._refresh_devices()
        self._select_mode(self._default_mode())
        self._set_state("idle")
        self.root.after(33, self._tick)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        main = tk.Frame(self.root, bg=w.BG)
        main.pack(fill="both", expand=True, padx=22, pady=(14, 12))

        self._build_header(main)
        self._build_mode(main)
        self._build_devices(main)
        self._build_meters(main)
        self._build_transport(main)
        self._build_preview(main)

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=w.BG)
        header.pack(fill="x")

        logo = tk.Canvas(header, width=40, height=40, bg=w.BG, highlightthickness=0, bd=0)
        logo.pack(side="left")
        for i, (x, h) in enumerate(((6, 12), (13, 24), (20, 32), (27, 18), (34, 26))):
            logo.create_line(x, 20 - h / 2, x, 20 + h / 2, fill=w.SYS if i % 2 == 0 else w.MIC,
                             width=4, capstyle="round")

        titles = tk.Frame(header, bg=w.BG)
        titles.pack(side="left", padx=(12, 0))
        tk.Label(titles, text="WASAPI 录音机", bg=w.BG, fg=w.TEXT,
                 font=w.font(17, True)).pack(anchor="w")
        tk.Label(titles, text="系统声音 · 麦克风 · 混音试听", bg=w.BG, fg=w.MUTED,
                 font=w.font(10)).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg=w.BG)
        right.pack(side="right")
        self.refresh_btn = w.FlatButton(right, "刷新设备", command=self._refresh_devices,
                                        width=92, height=30, bg=w.PANEL_2, fg=w.MUTED,
                                        text_font=w.font(9))
        self.refresh_btn.pack(side="right")

        badge = tk.Frame(right, bg=w.BG)
        badge.pack(side="right", padx=(0, 14))
        self.badge_dot = tk.Canvas(badge, width=8, height=8, bg=w.BG, highlightthickness=0, bd=0)
        self.badge_dot.pack(side="left", pady=(1, 0))
        self.badge_text = tk.Label(badge, text="", bg=w.BG, fg=w.MUTED, font=w.font(9))
        self.badge_text.pack(side="left", padx=(6, 0))
        if admin.is_admin():
            self.badge_dot.create_oval(0, 0, 8, 8, fill=w.OK, outline="")
            self.badge_text.configure(text="管理员权限")
        else:
            self.badge_dot.create_oval(0, 0, 8, 8, fill=w.WARN, outline="")
            self.badge_text.configure(text="普通权限")

    def _build_mode(self, parent: tk.Frame) -> None:
        body = self._card(parent, pady=(14, 0))
        row = tk.Frame(body, bg=w.PANEL)
        row.pack(fill="x", padx=18, pady=(12, 0))
        tk.Label(row, text="录制模式", bg=w.PANEL, fg=w.MUTED, font=w.font(10, True)).pack(side="left")

        buttons = tk.Frame(row, bg=w.PANEL)
        buttons.pack(side="left", padx=(16, 0))
        self.mode_buttons: dict[str, w.FlatButton] = {}
        for key, label in (("system", "仅系统声音"), ("mic", "仅麦克风"), ("mix", "混合录制")):
            btn = w.FlatButton(buttons, label, command=lambda k=key: self._select_mode(k),
                               width=118 if key != "mix" else 104, height=34,
                               bg=w.PANEL_2, fg=w.MUTED, radius=9, text_font=w.font(10))
            btn.pack(side="left", padx=(0, 8))
            self.mode_buttons[key] = btn

        self.mode_hint = tk.Label(body, text="", bg=w.PANEL, fg=w.FAINT, font=w.font(9),
                                  anchor="w", justify="left")
        self.mode_hint.pack(fill="x", padx=18, pady=(8, 12))

    def _build_devices(self, parent: tk.Frame) -> None:
        body = self._card(parent, pady=(10, 0))
        tk.Frame(body, bg=w.PANEL, height=10).pack(fill="x")
        self.sys_name, self.sys_spec = self._device_row(body, w.SYS, "系统声音")
        self.mic_name, self.mic_spec = self._device_row(body, w.MIC, "麦克风")
        tk.Frame(body, bg=w.PANEL, height=10).pack(fill="x")

    def _device_row(self, parent: tk.Frame, color: str, title: str):
        row = tk.Frame(parent, bg=w.PANEL)
        row.pack(fill="x", padx=18, pady=2)
        dot = tk.Canvas(row, width=8, height=8, bg=w.PANEL, highlightthickness=0, bd=0)
        dot.pack(side="left", pady=(3, 0))
        dot.create_oval(0, 0, 8, 8, fill=color, outline="")
        tk.Label(row, text=title, bg=w.PANEL, fg=w.TEXT, font=w.font(10, True),
                 width=9, anchor="w").pack(side="left", padx=(8, 0))
        name = tk.Label(row, text="—", bg=w.PANEL, fg=w.MUTED, font=w.font(10), anchor="w")
        name.pack(side="left", fill="x", expand=True)
        spec = tk.Label(row, text="", bg=w.PANEL, fg=w.FAINT, font=w.font(9))
        spec.pack(side="right")
        return name, spec

    def _build_meters(self, parent: tk.Frame) -> None:
        body = self._card(parent, pady=(10, 0))
        tk.Frame(body, bg=w.PANEL, height=8).pack(fill="x")
        self.sys_label, self.sys_meter, self.sys_db = self._meter_row(body, "系统", w.SYS)
        self.mic_label, self.mic_meter, self.mic_db = self._meter_row(body, "麦克风", w.MIC)
        tk.Frame(body, bg=w.PANEL, height=8).pack(fill="x")

    def _meter_row(self, parent: tk.Frame, title: str, color: str):
        row = tk.Frame(parent, bg=w.PANEL)
        row.pack(fill="x", padx=18, pady=3)
        label = tk.Label(row, text=title, bg=w.PANEL, fg=color, font=w.font(10, True),
                         width=6, anchor="w")
        label.pack(side="left")
        meter = w.Meter(row, color=color, height=14)
        meter.pack(side="left", fill="x", expand=True, padx=(0, 12))
        value = tk.Label(row, text="  -∞ dB", bg=w.PANEL, fg=w.FAINT, font=w.mono(9, False),
                         width=8, anchor="e")
        value.pack(side="right")
        return label, meter, value

    def _build_transport(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=w.BG)
        bar.pack(fill="x", pady=(14, 0))
        self.record_btn = w.FlatButton(bar, "●  开始录音", command=self._toggle_record,
                                       width=192, height=54, bg=w.REC, fg="#1B0608",
                                       radius=14, text_font=w.font(13, True))
        self.record_btn.pack(side="left")

        info = tk.Frame(bar, bg=w.BG)
        info.pack(side="left", padx=(18, 0))
        self.timer_label = tk.Label(info, text="00:00.0", bg=w.BG, fg=w.TEXT, font=w.mono(26))
        self.timer_label.pack(anchor="w")
        self.status_label = tk.Label(info, text="就绪", bg=w.BG, fg=w.MUTED, font=w.font(10))
        self.status_label.pack(anchor="w", pady=(1, 0))

        self.format_label = tk.Label(bar, text="", bg=w.BG, fg=w.FAINT, font=w.font(9))
        self.format_label.pack(side="right")

    def _build_preview(self, parent: tk.Frame) -> None:
        border = tk.Frame(parent, bg=w.LINE)
        border.pack(fill="both", expand=True, pady=(14, 0))
        body = tk.Frame(border, bg=w.PANEL)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        head = tk.Frame(body, bg=w.PANEL)
        head.pack(fill="x", padx=18, pady=(12, 0))
        tk.Label(head, text="试听与混音", bg=w.PANEL, fg=w.TEXT,
                 font=w.font(12, True)).pack(side="left")
        self.preview_hint = tk.Label(head, text="录音结束后可在这里试听，并拖动滑块调整两路音量比例",
                                     bg=w.PANEL, fg=w.FAINT, font=w.font(9))
        self.preview_hint.pack(side="right")

        self.sys_slider, self.sys_value = self._slider_row(body, "系统声音", w.SYS, self._on_gain)
        self.mic_slider, self.mic_value = self._slider_row(body, "麦克风", w.MIC, self._on_gain)

        controls = tk.Frame(body, bg=w.PANEL)
        controls.pack(fill="x", padx=18, pady=(12, 0))
        self.play_btn = w.FlatButton(controls, "▶  试听", command=self._toggle_play,
                                     width=104, height=34, bg=w.PANEL_2, fg=w.TEXT,
                                     text_font=w.font(10))
        self.play_btn.pack(side="left")
        self.progress = w.ProgressBar(controls, height=6, color=w.SYS)
        self.progress.pack(side="left", fill="x", expand=True, padx=(14, 12))
        self.time_label = tk.Label(controls, text="00:00 / 00:00", bg=w.PANEL, fg=w.MUTED,
                                   font=w.mono(9, False))
        self.time_label.pack(side="right")

        actions = tk.Frame(body, bg=w.PANEL)
        actions.pack(fill="x", padx=18, pady=(12, 14))
        self.save_btn = w.FlatButton(actions, "保存 WAV", command=self._save,
                                     width=132, height=38, bg=w.SYS, fg="#06121A",
                                     radius=10, text_font=w.font(10, True))
        self.save_btn.pack(side="right")
        self.reset_btn = w.FlatButton(actions, "重新录制", command=self._reset,
                                      width=108, height=38, bg=w.PANEL_2, fg=w.MUTED,
                                      text_font=w.font(10))
        self.reset_btn.pack(side="right", padx=(0, 10))
        self.length_label = tk.Label(actions, text="", bg=w.PANEL, fg=w.FAINT, font=w.font(9))
        self.length_label.pack(side="left")

        tk.Label(body, text="提示：混合模式下两路分别录制成独立音轨，保存时按当前滑块比例混合导出；试听时的调节不改变原始录音。",
                 bg=w.PANEL, fg=w.FAINT, font=w.font(9), anchor="w").pack(
            side="bottom", fill="x", padx=18, pady=(0, 10))

    def _slider_row(self, parent: tk.Frame, title: str, color: str, command):
        row = tk.Frame(parent, bg=w.PANEL)
        row.pack(fill="x", padx=18, pady=(10, 0))
        label = tk.Label(row, text=title, bg=w.PANEL, fg=color, font=w.font(10, True),
                         width=8, anchor="w")
        label.pack(side="left")
        slider = w.HSlider(row, 100, 0, 200, command, color=color, height=26)
        slider.pack(side="left", fill="x", expand=True, padx=(2, 12))
        value = tk.Label(row, text="100%", bg=w.PANEL, fg=w.MUTED, font=w.mono(9, False),
                         width=5, anchor="e")
        value.pack(side="right")
        return slider, value

    def _card(self, parent: tk.Frame, pady=(10, 0)) -> tk.Frame:
        border = tk.Frame(parent, bg=w.LINE)
        border.pack(fill="x", pady=pady)
        body = tk.Frame(border, bg=w.PANEL)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        return body

    def _apply_icon(self) -> None:
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.ico")
        if os.path.exists(icon):
            try:
                self.root.iconbitmap(icon)
            except Exception:
                pass

    # ------------------------------------------------------------- devices
    def _refresh_devices(self) -> None:
        self.devices = audio_devices.discover(self.pa)
        system = self.devices.get("system")
        mic = self.devices.get("mic")

        if system is not None:
            self.sys_name.configure(text=system.short_name, fg=w.TEXT)
            self.sys_spec.configure(text="%d Hz · %d ch · 环回" % (system.rate, system.channels))
        else:
            self.sys_name.configure(text=self.devices["errors"].get("system", "未找到设备"), fg=w.WARN)
            self.sys_spec.configure(text="")

        if mic is not None:
            self.mic_name.configure(text=mic.short_name, fg=w.TEXT)
            self.mic_spec.configure(text="%d Hz · %d ch" % (mic.rate, mic.channels))
        else:
            self.mic_name.configure(text=self.devices["errors"].get("mic", "未找到设备"), fg=w.WARN)
            self.mic_spec.configure(text="")

        for key, btn in self.mode_buttons.items():
            needed = {"system": system, "mic": mic, "mix": system if system and mic else None}
            btn.set_enabled(needed is not None)
        self.mode_available = {
            "system": system is not None,
            "mic": mic is not None,
            "mix": system is not None and mic is not None,
        }
        self.recorder.devices = self.devices

        rate = (system.rate if system else (mic.rate if mic else 48000))
        self.format_label.configure(text="%d Hz / 16 bit" % rate)

        available = [k for k, d in (("system", system), ("mic", mic)) if d is not None]
        if self.mode.get() not in available and not (self.mode.get() == "mix" and len(available) == 2):
            self._select_mode(self._default_mode())

    def _default_mode(self) -> str:
        system = self.devices.get("system")
        mic = self.devices.get("mic")
        if system and mic:
            return "mix"
        if system:
            return "system"
        return "mic"

    # ------------------------------------------------------------------ mode
    def _select_mode(self, mode: str) -> None:
        if mode not in self.mode_buttons or not self.mode_available.get(mode, False):
            return
        self.mode.set(mode)
        for key, btn in self.mode_buttons.items():
            btn.set_selected(key == mode)
            btn.set_colors(fg=w.TEXT if key == mode else w.MUTED)
        hints = {
            "system": "只录制扬声器 / 耳机里正在播放的声音（WASAPI 环回）。没有声音或系统静音时会自动补等长静音，保证时间轴连续。",
            "mic": "只录制麦克风输入。",
            "mix": "同时录制系统声音与麦克风，两条轨道独立写入；停止后可在下方试听并调节两路音量比例。",
        }
        self.mode_hint.configure(text=hints[mode])
        if self.state == "idle":
            if mode == "system":
                self.mic_label.configure(fg=w.FAINT)
                self.sys_label.configure(fg=w.SYS)
            elif mode == "mic":
                self.sys_label.configure(fg=w.FAINT)
                self.mic_label.configure(fg=w.MIC)
            else:
                self.sys_label.configure(fg=w.SYS)
                self.mic_label.configure(fg=w.MIC)
        self._sync_sliders()

    def _sync_sliders(self) -> None:
        if self.session is None:
            return
        keys = {t.key for t in self.session.tracks}
        self.sys_slider.set_enabled("system" in keys)
        self.mic_slider.set_enabled("mic" in keys)

    # ------------------------------------------------------------------ state
    def _set_state(self, state: str) -> None:
        self.state = state
        recording = state == "recording"
        preview = state == "preview"
        exporting = state == "exporting"

        self.record_btn.set_text("■  停止录音" if recording else "●  开始录音")
        self.record_btn.set_enabled(state in ("idle", "recording"))
        if state in ("idle", "recording"):
            self.record_btn.set_colors(bg=w.REC, fg="#1B0608")
        else:
            self.record_btn.set_colors(bg=w.PANEL_2, fg=w.FAINT)
        self.refresh_btn.set_enabled(state == "idle")
        for key, btn in self.mode_buttons.items():
            btn.set_enabled(self.mode_available.get(key, False) and state == "idle")
            btn.set_colors(fg=(w.TEXT if key == self.mode.get() else w.MUTED))

        self.play_btn.set_enabled(preview)
        self.save_btn.set_enabled(preview)
        self.reset_btn.set_enabled(preview)
        if state == "idle":
            self.sys_slider.set_enabled(False)
            self.mic_slider.set_enabled(False)
            self.progress.set(0.0)
            self.time_label.configure(text="00:00 / 00:00")
            self.length_label.configure(text="")
            self.timer_label.configure(text="00:00.0")
            self.preview_hint.configure(text="录音结束后可在这里试听，并拖动滑块调整两路音量比例")
        elif preview:
            self._sync_sliders()
            self.preview_hint.configure(text="拖动滑块实时改变混音比例")
        if exporting:
            self.play_btn.set_enabled(False)
            self.save_btn.set_enabled(False)

    # ---------------------------------------------------------------- record
    def _toggle_record(self) -> None:
        if self.state == "recording":
            self._stop_recording()
        elif self.state == "idle":
            self._start_recording()

    def _start_recording(self) -> None:
        # Re-enumerate right before opening: the user may have plugged in a
        # headset, or the endpoint may have moved since the window opened.
        self._refresh_devices()
        mode = self.mode.get()
        if mode in ("mic", "mix") and self.devices.get("mic") is None:
            messagebox.showerror("无法录音", "没有可用的麦克风设备", parent=self.root)
            return
        if mode in ("system", "mix") and self.devices.get("system") is None:
            messagebox.showerror("无法录音", "没有可用的系统声音设备", parent=self.root)
            return
        self.status_label.configure(text="正在打开设备…", fg=w.MUTED)
        self.root.update_idletasks()
        try:
            self.recorder.devices = self.devices
            self.recorder.start(mode)
        except Exception as exc:
            self._set_state("idle")
            self.status_label.configure(text="打开设备失败", fg=w.WARN)
            log_path = os.path.join(recorder_mod.data_root(), "recorder.log")
            messagebox.showerror(
                "无法开始录音",
                "%s\n\n"
                "可能的原因：\n"
                "· 设备被其他程序独占（会议、播放器、直播软件）\n"
                "· 耳机 / 麦克风刚插拔，端点编号已经变化\n"
                "· 驱动不支持所请求的采样率或声道数\n\n"
                "已写入日志：%s\n"
                "建议先点「刷新设备」再试，或换一种录制模式。" % (exc, log_path),
                parent=self.root,
            )
            self._refresh_devices()
            return
        self._set_state("recording")
        self.status_label.configure(text="正在录制…", fg=w.REC)
        self.preview_hint.configure(text="录制中，停止后可试听")
        self._blink = True

    def _stop_recording(self) -> None:
        self.status_label.configure(text="正在整理录音…", fg=w.MUTED)
        self.root.update_idletasks()
        try:
            session = self.recorder.stop()
        except Exception as exc:
            messagebox.showerror("停止录音失败", str(exc), parent=self.root)
            self._set_state("idle")
            return
        if session.frames <= 0 or not session.tracks:
            recorder_mod.cleanup_session(session)
            messagebox.showwarning("录音为空", "没有捕获到任何数据，请检查设备后重试。", parent=self.root)
            self._set_state("idle")
            return

        self.session = session
        self._setup_player()
        self._set_state("preview")
        self.timer_label.configure(text=format_clock(session.duration))
        self.status_label.configure(text="录音完成，时长 %s" % format_clock(session.duration, False), fg=w.OK)
        self.length_label.configure(
            text="%s · %d Hz · %s" % (
                format_clock(session.duration, False), session.rate,
                "混合双轨" if len(session.tracks) == 2 else session.tracks[0].label,
            )
        )

    def _setup_player(self) -> None:
        if self.session is None:
            return
        self._teardown_player()
        wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        device = int(wasapi.get("defaultOutputDevice", -1))
        try:
            self.player = player_mod.PreviewPlayer(self.pa, self.session, device if device >= 0 else None)
            self.player.open()
            self.player.set_gains(self._gains())
        except Exception as exc:
            self.player = None
            messagebox.showwarning("试听不可用", "无法打开播放设备：%s\n仍可直接保存文件。" % exc, parent=self.root)

    def _teardown_player(self) -> None:
        if self.player is not None:
            try:
                self.player.close()
            except Exception:
                pass
            self.player = None
        self.play_btn.set_text("▶  试听")

    # --------------------------------------------------------------- preview
    def _gains(self) -> dict:
        return {"system": self._gain_sys / 100.0, "mic": self._gain_mic / 100.0}

    def _on_gain(self, _value: int = 0) -> None:
        self._gain_sys = self.sys_slider.get()
        self._gain_mic = self.mic_slider.get()
        self.sys_value.configure(text="%d%%" % self._gain_sys)
        self.mic_value.configure(text="%d%%" % self._gain_mic)
        if self.player is not None:
            self.player.set_gains(self._gains())

    def _toggle_play(self) -> None:
        if self.player is None:
            return
        if self.player.playing:
            self.player.stop()
            self.play_btn.set_text("▶  试听")
        else:
            self.player.play()
            self.play_btn.set_text("❚❚  暂停")

    # ----------------------------------------------------------------- export
    def _save(self) -> None:
        if self.session is None:
            return
        default = "录音_%s.wav" % time.strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self.root, title="保存混音 WAV", defaultextension=".wav",
            initialfile=default, filetypes=[("WAV 音频", "*.wav")],
        )
        if not path:
            return
        self._export_path = path
        self._export_progress = 0.0
        self._set_state("exporting")
        self.status_label.configure(text="正在保存… 0%", fg=w.MUTED)
        sources = [(t.path, t.key) for t in self.session.tracks]
        threading.Thread(target=self._export_worker, args=(path, sources), daemon=True).start()

    def _export_worker(self, path: str, sources) -> None:
        try:
            mixing.export_mixed(
                path, sources, self.session.frames, self.session.rate, self._gains(),
                progress=self._on_export_progress,
            )
            error = None
        except Exception as exc:  # pragma: no cover - depends on the filesystem
            error = exc
        self.root.after(0, self._export_done, path, error)

    def _on_export_progress(self, value: float) -> None:
        self._export_progress = value

    def _export_done(self, path: str, error) -> None:
        if self._closing:
            return
        if error is not None:
            self._set_state("preview")
            self.status_label.configure(text="保存失败", fg=w.WARN)
            messagebox.showerror("保存失败", str(error), parent=self.root)
            return
        self._set_state("preview")
        self.progress.set(0.0)
        self.status_label.configure(text="已保存：%s" % os.path.basename(path), fg=w.OK)
        messagebox.showinfo("保存完成", "文件已保存到：\n%s" % path, parent=self.root)

    # ------------------------------------------------------------------ reset
    def _reset(self) -> None:
        self._teardown_player()
        self._teardown_session()
        self._set_state("idle")
        self.status_label.configure(text="就绪", fg=w.MUTED)
        self.sys_meter.set_level(0.0)
        self.mic_meter.set_level(0.0)

    def _teardown_session(self) -> None:
        if self.session is not None:
            recorder_mod.cleanup_session(self.session)
            self.session = None

    # ------------------------------------------------------------------- tick
    def _tick(self) -> None:
        if self._closing:
            return
        now = time.perf_counter()
        dt = min(0.2, now - self._last_tick)
        self._last_tick = now

        if self.state == "recording":
            for key, meter, label in (("system", self.sys_meter, self.sys_db),
                                      ("mic", self.mic_meter, self.mic_db)):
                writer = self.recorder.writer(key)
                value = writer.peak if writer is not None else 0.0
                meter.set_level(value)
                label.configure(text=db_text(value))
            self.timer_label.configure(text=format_clock(self.recorder.elapsed()))
            self._blink = not self._blink
            self.status_label.configure(text="● 正在录制…" if self._blink else "○ 正在录制…", fg=w.REC)
        else:
            for meter, label in ((self.sys_meter, self.sys_db), (self.mic_meter, self.mic_db)):
                meter.set_level(0.0)
                label.configure(text=db_text(0.0))

        self.sys_meter.decay(dt)
        self.mic_meter.decay(dt)

        if self.state == "preview" and self.player is not None:
            self.progress.set(self.player.position)
            self.time_label.configure(
                text="%s / %s" % (format_clock(self.player.seconds, False),
                                  format_clock(self.session.duration, False))
            )
            if self.player.finished and self.player.playing is False:
                self.play_btn.set_text("▶  试听")
        if self.state == "exporting":
            self.progress.set(self._export_progress)
            self.status_label.configure(text="正在保存… %d%%" % int(self._export_progress * 100),
                                        fg=w.MUTED)

        self.root.after(33, self._tick)

    # ------------------------------------------------------------------ close
    def _on_close(self) -> None:
        self._closing = True
        try:
            if self.state == "recording":
                self.recorder.cancel()
        except Exception:
            pass
        self._teardown_player()
        self._teardown_session()
        try:
            self.pa.terminate()
        except Exception:
            pass
        self.root.destroy()
