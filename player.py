"""Preview playback: a mixer thread feeds a ring buffer, the audio callback only copies.

Keeping file I/O out of the PortAudio callback means dragging the volume slider
or reading a slow disk can never glitch the output stream, and the callback
never blocks the mixer thread.
"""

from __future__ import annotations

import collections
import threading
import time
import typing as t

import pyaudiowpatch as pyaudio

import mixing


class PreviewPlayer:
    """Plays the recorded tracks mixed with user controlled gains."""

    def __init__(self, pa: "pyaudio.PyAudio", session, device_index: int | None = None) -> None:
        self.pa = pa
        self.session = session
        self.rate = session.rate
        self.duration_frames = max(1, session.frames)
        self._device_index = device_index

        self._gains: dict[str, float] = {t.key: 1.0 for t in session.tracks}
        self._sources: list[tuple[mixing.WavSource, str]] = []
        self._mix: mixing.MixSession | None = None

        self._ring: t.Deque[bytes] = collections.deque()
        self._ring_bytes = 0
        self._lock = threading.Lock()
        self._block = max(512, int(self.rate * 0.08))

        self._stream = None
        self._thread: threading.Thread | None = None
        self._active = False
        self._playing = False
        self._finished = False
        self.position_frames = 0
        self.underruns = 0

    # -- lifecycle -----------------------------------------------------------
    def open(self) -> None:
        if self._stream is not None:
            return
        self._build_mix()
        stream = None
        for device in (self._device_index, None):
            try:
                stream = self.pa.open(
                    format=pyaudio.paInt16,
                    channels=2,
                    rate=self.rate,
                    output=True,
                    output_device_index=device,
                    frames_per_buffer=max(512, int(self.rate * 0.04)),
                    stream_callback=self._callback,
                )
                break
            except Exception:
                stream = None
        if stream is None:
            raise RuntimeError("无法打开播放设备")
        self._stream = stream
        self._stream.start_stream()
        self._active = True
        self._thread = threading.Thread(target=self._run, name="preview-mixer", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._active = False
        self._playing = False
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        if self._stream is not None:
            try:
                self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._mix is not None:
            self._mix.close()
            self._mix = None
        with self._lock:
            self._ring.clear()
            self._ring_bytes = 0

    def _build_mix(self) -> None:
        if self._mix is not None:
            self._mix.close()
        self._sources = [
            (mixing.WavSource(track.path, self.rate), track.key) for track in self.session.tracks
        ]
        self._mix = mixing.MixSession(self._sources, self.duration_frames)

    # -- transport -----------------------------------------------------------
    def play(self) -> None:
        if self._mix is None:
            self._build_mix()
        if self.position_frames >= self.duration_frames - 1 or self._finished:
            self.rewind()
        with self._lock:
            self._ring.clear()
            self._ring_bytes = 0
        self._playing = True
        self._finished = False

    def stop(self) -> None:
        self._playing = False
        with self._lock:
            self._ring.clear()
            self._ring_bytes = 0

    def rewind(self) -> None:
        self.stop()
        for source, _key in self._sources:
            source.seek(0)
        self.position_frames = 0
        self._finished = False

    def set_gains(self, gains: t.Mapping[str, float]) -> None:
        # Replacing the dict is atomic for readers, so no lock is needed here.
        self._gains = {k: float(v) for k, v in gains.items()}

    def gains(self) -> dict:
        return dict(self._gains)

    # -- state ---------------------------------------------------------------
    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def finished(self) -> bool:
        return self._finished and self._ring_bytes == 0

    @property
    def position(self) -> float:
        return min(1.0, self.position_frames / float(self.duration_frames))

    @property
    def seconds(self) -> float:
        return self.position_frames / float(self.rate)

    @property
    def duration(self) -> float:
        return self.duration_frames / float(self.rate)

    # -- audio callback ------------------------------------------------------
    def _callback(self, in_data, frame_count, time_info, status):  # noqa: ANN001
        need = frame_count * mixing.STEREO_FRAME_BYTES
        data = self._take(need)
        if len(data) < need:
            data += b"\x00" * (need - len(data))
        return (data, pyaudio.paContinue)

    def _take(self, need: int) -> bytes:
        out = bytearray()
        with self._lock:
            while len(out) < need and self._ring:
                chunk = self._ring[0]
                remaining = need - len(out)
                if len(chunk) <= remaining:
                    out += chunk
                    self._ring.popleft()
                    self._ring_bytes -= len(chunk)
                else:
                    out += chunk[:remaining]
                    self._ring[0] = chunk[remaining:]
                    self._ring_bytes -= remaining
        if not out:
            self.underruns += 1
        return bytes(out)

    # -- mixer thread --------------------------------------------------------
    def _run(self) -> None:
        while self._active:
            if not self._playing:
                time.sleep(0.01)
                continue
            threshold = int(self.rate * 0.30) * mixing.STEREO_FRAME_BYTES
            if self._ring_bytes > threshold:
                time.sleep(0.01)
                continue
            if self._mix is None:
                time.sleep(0.01)
                continue
            data, end = self._mix.read(self._block, self._gains)
            if data:
                with self._lock:
                    self._ring.append(data)
                    self._ring_bytes += len(data)
                self.position_frames += mixing.frames_in(data, 2)
            if end:
                self._finished = True
                self._playing = False
                continue
            time.sleep(0.002)
