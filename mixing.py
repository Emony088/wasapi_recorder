"""PCM helpers: gain, stereo conversion, mixing, WAV export.

All functions operate on little-endian signed 16-bit PCM bytes because that is
what the recorder produces; keeping one format everywhere avoids conversions in
the audio callback.
"""

from __future__ import annotations

import array
import os
import typing as t
import wave

try:  # audioop is C-speed and present on CPython 3.12 (deprecated, gone in 3.13)
    import audioop  # type: ignore
except Exception:  # pragma: no cover
    audioop = None

SAMPLE_WIDTH = 2
STEREO_FRAME_BYTES = 4


# --------------------------------------------------------------------------- primitives
def frames_in(data: bytes, channels: int) -> int:
    return len(data) // (SAMPLE_WIDTH * channels)


def to_stereo(data: bytes, channels: int) -> bytes:
    """Return interleaved stereo PCM for *data* (mono is duplicated)."""

    if channels == 2:
        return data
    if channels == 1:
        if audioop is not None:
            return audioop.tostereo(data, SAMPLE_WIDTH, 1, 1)
        out = bytearray(len(data) * 2)
        out[0::4] = data[0::2]
        out[1::4] = data[1::2]
        out[2::4] = data[0::2]
        out[3::4] = data[1::2]
        return bytes(out)
    # More than two channels: keep the first two.
    samples = array.array("h")
    samples.frombytes(data)
    out = array.array("h", bytes(len(samples) // channels * 4))
    for i in range(len(samples) // channels):
        base = i * channels
        out[i * 2] = samples[base]
        out[i * 2 + 1] = samples[base + 1]
    return out.tobytes()


def _scale_fallback(data: bytes, gain: float) -> bytes:
    samples = array.array("h")
    samples.frombytes(data)
    for i, value in enumerate(samples):
        scaled = int(value * gain)
        samples[i] = 32767 if scaled > 32767 else (-32768 if scaled < -32768 else scaled)
    return samples.tobytes()


def apply_gain(data: bytes, channels: int, gain: float) -> bytes:
    if not data:
        return b""
    if audioop is not None:
        if gain != 1.0:
            data = audioop.mul(data, SAMPLE_WIDTH, gain)
        return to_stereo(data, channels)
    return to_stereo(_scale_fallback(data, gain), channels)


def mix(a: bytes, b: bytes) -> bytes:
    """Add two stereo PCM buffers with saturation."""

    if not a:
        return b
    if not b:
        return a
    if audioop is not None:
        return audioop.add(a, b, SAMPLE_WIDTH)
    aa = array.array("h")
    aa.frombytes(a)
    bb = array.array("h")
    bb.frombytes(b)
    n = min(len(aa), len(bb))
    out = array.array("h", bytes(n * 2))
    for i in range(n):
        value = aa[i] + bb[i]
        out[i] = 32767 if value > 32767 else (-32768 if value < -32768 else value)
    return out.tobytes()


def silence(frames: int, channels: int = 2) -> bytes:
    return b"\x00" * max(0, frames) * SAMPLE_WIDTH * channels


def peak_of(data: bytes) -> float:
    """Peak level of interleaved PCM as 0..1."""

    if not data:
        return 0.0
    samples = array.array("h")
    samples.frombytes(data)
    if not samples:
        return 0.0
    return max(abs(max(samples)), abs(min(samples))) / 32768.0


def rms_of(data: bytes) -> float:
    if not data:
        return 0.0
    if audioop is not None:
        return audioop.rms(data, SAMPLE_WIDTH) / 32768.0
    samples = array.array("h")
    samples.frombytes(data)
    if not samples:
        return 0.0
    stride = samples[::4]
    total = 0
    for value in stride:
        total += value * value
    return (total / max(1, len(stride))) ** 0.5 / 32768.0


# --------------------------------------------------------------------------- sources
class WavSource:
    """Sequential reader for one recorded track, with optional rate conversion."""

    def __init__(self, path: str, target_rate: int | None = None) -> None:
        self.path = path
        self._wav = wave.open(path, "rb")
        self.channels = self._wav.getnchannels()
        self.rate = self._wav.getframerate()
        self.total_frames = self._wav.getnframes()
        self.out_rate = int(target_rate or self.rate)
        self._needs_resample = self.rate != self.out_rate
        self._state: t.Any = None

    def seek(self, frame: int) -> None:
        self._wav.setpos(max(0, min(int(frame), self.total_frames)))
        self._state = None

    def read(self, frames: int) -> bytes:
        data = self._wav.readframes(max(0, frames))
        if data and self._needs_resample and audioop is not None:
            data, self._state = audioop.ratecv(
                data, SAMPLE_WIDTH, self.channels, self.rate, self.out_rate, self._state
            )
        return data

    def close(self) -> None:
        try:
            self._wav.close()
        except Exception:
            pass

    def __enter__(self) -> "WavSource":
        return self

    def __exit__(self, *exc: t.Any) -> None:
        self.close()


class MixSession:
    """Reads several tracks in lock-step and returns a mixed stereo block.

    Gains are supplied per call so the preview slider can change the balance
    while playback is running; the mixer thread reads them once per block.
    """

    def __init__(self, sources: t.Sequence[t.Tuple[WavSource, str]], total_frames: int) -> None:
        self._sources = list(sources)
        self.total_frames = int(total_frames)

    def close(self) -> None:
        for src, _key in self._sources:
            src.close()

    def read(self, frames: int, gains: t.Mapping[str, float]) -> t.Tuple[bytes, bool]:
        """Return (mixed stereo bytes, reached_end)."""

        parts: t.List[t.Tuple[bytes, int, float]] = []
        longest = 0
        for src, key in self._sources:
            data = src.read(frames)
            longest = max(longest, frames_in(data, src.channels))
            parts.append((data, src.channels, float(gains.get(key, 1.0))))
        if longest == 0:
            return b"", True

        mixed = silence(longest, 2)
        for data, channels, gain in parts:
            if not data or gain <= 0.0:
                continue
            have = frames_in(data, channels)
            if have < longest:
                data += silence(longest - have, channels)
            mixed = mix(mixed, apply_gain(data, channels, gain))
        return mixed, longest < frames


# --------------------------------------------------------------------------- export
def export_mixed(
    out_path: str,
    sources: t.Sequence[t.Tuple[str, str]],
    total_frames: int,
    rate: int,
    gains: t.Mapping[str, float],
    progress: t.Callable[[float], None] | None = None,
    block_frames: int | None = None,
) -> None:
    """Write a mixed stereo WAV from ``(path, gain_key)`` sources."""

    block = int(block_frames or rate // 2)
    readers = [(WavSource(path, rate), key) for path, key in sources]
    session = MixSession(readers, total_frames)
    tmp_path = out_path + ".part"
    try:
        with wave.open(tmp_path, "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(SAMPLE_WIDTH)
            out.setframerate(rate)
            written = 0
            while written < total_frames:
                want = min(block, total_frames - written)
                data, _end = session.read(want, gains)
                if not data:
                    break
                out.writeframes(data)
                written += frames_in(data, 2)
                if progress is not None:
                    progress(min(1.0, written / max(1, total_frames)))
        os.replace(tmp_path, out_path)
    finally:
        session.close()
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def probe_wav(path: str) -> dict:
    with wave.open(path, "rb") as wav:
        return {
            "channels": wav.getnchannels(),
            "rate": wav.getframerate(),
            "frames": wav.getnframes(),
            "seconds": wav.getnframes() / float(wav.getframerate() or 1),
        }
