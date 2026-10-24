"""WASAPI capture: one PortAudio stream per source, one writer thread per track.

Design notes
------------
* The PortAudio callback only copies bytes into a small deque and updates the
  level meters.  It never touches a file, the GUI, or a lock held by anything
  slow, so a stalled disk cannot cause an input overflow.
* Each track has its own writer thread that owns its WAV file.  No two threads
  ever write the same file, so the two sources cannot interfere with each other.
* WASAPI loopback simply *stops delivering packets* when nothing is playing on
  the endpoint (or the endpoint is idle).  The writer therefore pads the track
  with silence up to the shared session clock, so both tracks always describe
  the same wall-clock interval and stay in sync.
"""

from __future__ import annotations

import collections
import dataclasses
import os
import shutil
import sys
import tempfile
import threading
import time
import typing as t
import uuid
import wave

import pyaudiowpatch as pyaudio

import audio_devices as devices
import mixing

DATA_ENV = "WASAPI_RECORDER_DATA"
MODES = ("system", "mic", "mix")
MODE_LABELS = {"system": "仅系统声音", "mic": "仅麦克风", "mix": "混合（系统 + 麦克风）"}


class RecorderError(RuntimeError):
    pass


class Clock:
    """Shared session clock; both tracks pad against the same t0."""

    __slots__ = ("t0",)

    def __init__(self) -> None:
        self.t0 = time.perf_counter()


@dataclasses.dataclass(frozen=True)
class TrackInfo:
    key: str
    label: str
    path: str
    rate: int
    channels: int
    frames: int

    @property
    def seconds(self) -> float:
        return self.frames / float(self.rate or 1)


@dataclasses.dataclass(frozen=True)
class Session:
    mode: str
    rate: int
    frames: int
    directory: str
    tracks: t.Tuple[TrackInfo, ...]
    created: float

    @property
    def duration(self) -> float:
        return self.frames / float(self.rate or 1)

    def track(self, key: str) -> TrackInfo | None:
        for item in self.tracks:
            if item.key == key:
                return item
        return None


def data_root() -> str:
    override = os.environ.get(DATA_ENV)
    base = override or os.path.join(
        os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "WasapiRecorder"
    )
    os.makedirs(base, exist_ok=True)
    return base


def log(message: str) -> None:
    """Append one line to recorder.log (rotating at 512 KB); never raises."""

    try:
        path = os.path.join(data_root(), "recorder.log")
        if os.path.exists(path) and os.path.getsize(path) > 512 * 1024:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:
        pass


def _is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _new_session_dir() -> str:
    name = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    path = os.path.join(data_root(), name)
    os.makedirs(path, exist_ok=False)
    return path


class TrackWriter(threading.Thread):
    """Owns one WAV file; pads silence so the track follows the session clock."""

    def __init__(
        self,
        key: str,
        label: str,
        path: str,
        rate: int,
        channels: int,
        clock: Clock,
        block_frames: int,
        guard_seconds: float = 0.10,
    ) -> None:
        super().__init__(name="track-" + key, daemon=True)
        self.key = key
        self.label = label
        self.path = path
        self.rate = rate
        self.channels = channels
        self.clock = clock
        self.block_frames = block_frames
        self._guard_frames = int(guard_seconds * rate)
        self._frame_bytes = channels * mixing.SAMPLE_WIDTH
        self._wav = wave.open(path, "wb")
        self._wav.setnchannels(channels)
        self._wav.setsampwidth(mixing.SAMPLE_WIDTH)
        self._wav.setframerate(rate)
        self._buf: t.Deque[bytes] = collections.deque()
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._stop_time: float | None = None
        self._closed = False

        self.frames = 0
        self.padded_frames = 0
        self.overflows = 0
        self.dropped = 0
        self.peak = 0.0
        self.rms = 0.0

    # -- called from the PortAudio callback (must stay cheap) -----------------
    def push(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            if len(self._buf) < 500:
                self._buf.append(data)
            else:
                self.dropped += 1
        self.peak = mixing.peak_of(data)
        self.rms = mixing.rms_of(data)

    # -- writer thread -------------------------------------------------------
    def run(self) -> None:
        try:
            while not self._stop_evt.is_set():
                self._flush()
                self._pad_to_clock()
                self._stop_evt.wait(0.012)
            self._flush()
            end = self._stop_time if self._stop_time is not None else time.perf_counter()
            target = int((end - self.clock.t0) * self.rate)
            if target > self.frames:
                self._write_silence(target - self.frames)
        finally:
            self._close()

    def _flush(self) -> None:
        while True:
            with self._lock:
                if not self._buf:
                    return
                data = self._buf.popleft()
            if self._closed:
                return
            self._wav.writeframes(data)
            self.frames += len(data) // self._frame_bytes

    def _pad_to_clock(self) -> None:
        if self._closed:
            return
        target = int((time.perf_counter() - self.clock.t0) * self.rate) - self._guard_frames
        deficit = target - self.frames
        if deficit > 0:
            # Cap each tick so a long stall cannot allocate a giant buffer;
            # the next tick continues where this one stopped.
            self._write_silence(min(deficit, self.rate))

    def _write_silence(self, frames: int) -> None:
        remaining = int(frames)
        while remaining > 0:
            step = min(remaining, self.rate)
            self._wav.writeframes(b"\x00" * (step * self._frame_bytes))
            self.frames += step
            self.padded_frames += step
            remaining -= step

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._wav.close()
        except Exception:
            pass

    # -- control -------------------------------------------------------------
    def finish(self, stop_time: float) -> None:
        self._stop_time = stop_time
        self._stop_evt.set()

    def abort(self) -> None:
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout=2.0)
        self._close()
        try:
            os.remove(self.path)
        except OSError:
            pass


class Recorder:
    """Opens the requested sources, records to separate WAV files, returns a Session."""

    def __init__(self, pa: "pyaudio.PyAudio") -> None:
        self.pa = pa
        self._writers: t.Dict[str, TrackWriter] = {}
        self._streams: t.List[t.Any] = []
        self._keepalive: t.Any = None
        self._clock = Clock()
        self._directory = ""
        self._mode = "mix"
        self._rate = 0
        self._recording = False
        self.devices: dict = {}

    # -- state ---------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def rate(self) -> int:
        return self._rate

    def writer(self, key: str) -> TrackWriter | None:
        return self._writers.get(key)

    def elapsed(self) -> float:
        if not self._recording:
            return 0.0
        return max(0.0, time.perf_counter() - self._clock.t0)

    # -- start ---------------------------------------------------------------
    def start(self, mode: str) -> None:
        if mode not in MODES:
            raise RecorderError("未知的录制模式: %s" % mode)
        if self._recording:
            raise RecorderError("已经在录音中")

        system = self.devices.get("system") if mode in ("system", "mix") else None
        mic = self.devices.get("mic") if mode in ("mic", "mix") else None
        if mode in ("system", "mix") and system is None:
            raise RecorderError(self.devices.get("errors", {}).get("system", "没有可用的回环设备"))
        if mode in ("mic", "mix") and mic is None:
            raise RecorderError(self.devices.get("errors", {}).get("mic", "没有可用的麦克风"))

        rate = int(system.rate) if system is not None else int(mic.rate)
        block = max(256, int(rate * 0.02))  # 20 ms

        self._directory = _new_session_dir()
        self._mode = mode
        self._rate = rate
        self._clock = Clock()
        self._writers = {}
        self._streams = []

        try:
            log("start mode=%s rate=%d block=%d system=%r mic=%r frozen=%s admin=%s" % (
                mode, rate, block,
                (system.short_name, system.index, system.channels, system.rate) if system else None,
                (mic.short_name, mic.index, mic.channels, mic.rate) if mic else None,
                bool(getattr(sys, "frozen", False)),
                _is_admin(),
            ))
            # WASAPI loopback needs a running render stream on the endpoint.
            # Opening it lazily (only after a failure) keeps the normal start
            # fast; _open_track_with_fallback starts it when the endpoint is
            # asleep and refuses the loopback client.
            if system is not None:
                self._open_track_with_fallback(
                    "system", "系统声音", system, rate, block, retry_with_keepalive=True
                )
            if mic is not None:
                self._open_track_with_fallback("mic", "麦克风", mic, rate, block)

            self._rate = (self._writers["system"].rate if "system" in self._writers
                          else self._writers["mic"].rate)
            self._clock.t0 = time.perf_counter() + 0.03
            for writer in self._writers.values():
                writer.start()
            for stream in self._streams:
                stream.start_stream()
            self._recording = True
            log("recording started rate=%d tracks=%s" % (self._rate, list(self._writers)))
        except Exception as exc:
            log("start failed: %r" % (exc,))
            self._teardown()
            raise

    def _open_track(
        self,
        key: str,
        label: str,
        device_index: int,
        channels: int,
        rate: int,
        block: int,
    ) -> None:
        path = os.path.join(self._directory, key + ".wav")
        writer = TrackWriter(key, label, path, rate, channels, self._clock, block)
        callback = self._make_callback(writer)
        try:
            stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=block,
                start=False,
                stream_callback=callback,
            )
        except Exception:
            writer._close()
            raise
        self._writers[key] = writer
        self._streams.append(stream)

    def _open_track_with_fallback(
        self, key: str, label: str, device, rate: int, block: int,
        retry_with_keepalive: bool = False,
    ) -> None:
        """Try the requested format, then simpler ones the endpoint surely supports.

        If every combination fails for the loopback, wake the endpoint with a
        silent render stream and try once more: an idle USB headset can refuse a
        loopback client (PortAudio reports this as ``-9992``).
        """

        combos: t.List[t.Tuple[int, int, int]] = []

        def add(channels: int, sample_rate: int, frames: int) -> None:
            item = (channels, sample_rate, max(0, int(frames)))
            if item not in combos:
                combos.append(item)

        add(device.channels, rate, block)
        add(device.channels, device.rate, block)
        add(device.channels, device.rate, 0)
        if not device.loopback and device.channels > 1:
            add(1, rate, block)
            add(1, device.rate, block)
        add(2, rate, 0)

        def attempt() -> Exception | None:
            last: Exception | None = None
            for channels, sample_rate, frames in combos:
                try:
                    self._open_track(key, label, device.index, channels, sample_rate, frames)
                    log("opened %s: ch=%d rate=%d block=%d" % (key, channels, sample_rate, frames))
                    return None
                except Exception as exc:
                    last = exc
                    log("open %s failed (ch=%d rate=%d block=%d): %r"
                        % (key, channels, sample_rate, frames, exc))
            return last

        error = attempt()
        if error is not None and retry_with_keepalive:
            log("retrying %s with keepalive after: %r" % (key, error))
            self._start_keepalive(rate, block)
            error = attempt()
        if error is not None:
            raise RecorderError(
                "无法打开%s设备「%s」：%s" % (label, device.short_name, error)
            ) from error

    def _start_keepalive(self, rate: int, block: int) -> None:
        """Render silence on the default output so the endpoint never idles."""

        try:
            host = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            index = int(host.get("defaultOutputDevice", -1))
        except Exception:
            return
        if index < 0:
            return

        def callback(in_data, frame_count, time_info, status):  # noqa: ANN001
            return (b"\x00" * (frame_count * 4), pyaudio.paContinue)

        for device in (index, None):
            try:
                stream = self.pa.open(
                    format=pyaudio.paInt16,
                    channels=2,
                    rate=rate,
                    output=True,
                    output_device_index=device,
                    frames_per_buffer=max(256, int(rate * 0.02)) if block <= 0 else block,
                    start=False,
                    stream_callback=callback,
                )
                stream.start_stream()
                self._keepalive = stream
                log("keepalive render started on device %r rate=%d" % (device, rate))
                return
            except Exception as exc:
                log("keepalive failed on device %r: %r" % (device, exc))

    def _stop_keepalive(self) -> None:
        stream, self._keepalive = self._keepalive, None
        if stream is None:
            return
        try:
            stream.stop_stream()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass
        log("keepalive stopped")

    def _make_callback(self, writer: TrackWriter):
        def callback(in_data, frame_count, time_info, status):  # noqa: ANN001
            if status:
                writer.overflows += 1
            writer.push(in_data)
            return (None, pyaudio.paContinue)

        return callback

    # -- stop ----------------------------------------------------------------
    def stop(self) -> Session:
        if not self._recording:
            raise RecorderError("当前没有在录音")
        self._recording = False
        stop_time = time.perf_counter()

        for stream in self._streams:
            try:
                stream.stop_stream()
            except Exception:
                pass
        for stream in self._streams:
            try:
                stream.close()
            except Exception:
                pass
        self._streams = []
        self._stop_keepalive()

        writers = list(self._writers.values())
        for writer in writers:
            writer.finish(stop_time)
        for writer in writers:
            writer.join(timeout=3.0)

        frames = min((w.frames for w in writers), default=0)
        tracks = tuple(
            TrackInfo(
                key=w.key,
                label=w.label,
                path=w.path,
                rate=w.rate,
                channels=w.channels,
                frames=frames,
            )
            for w in writers
        )
        session = Session(
            mode=self._mode,
            rate=self._rate,
            frames=frames,
            directory=self._directory,
            tracks=tracks,
            created=time.time(),
        )
        self._writers = {}
        return session

    def cancel(self) -> None:
        self._recording = False
        self._stop_keepalive()
        for stream in self._streams:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self._streams = []
        for writer in list(self._writers.values()):
            writer.abort()
        self._writers = {}
        if self._directory and os.path.isdir(self._directory):
            shutil.rmtree(self._directory, ignore_errors=True)
        self._directory = ""

    def _teardown(self) -> None:
        self._stop_keepalive()
        for stream in self._streams:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self._streams = []
        for writer in list(self._writers.values()):
            writer.abort()
        self._writers = {}
        if self._directory and os.path.isdir(self._directory):
            shutil.rmtree(self._directory, ignore_errors=True)


def cleanup_session(session: Session | None) -> None:
    if session and session.directory and os.path.isdir(session.directory):
        shutil.rmtree(session.directory, ignore_errors=True)
