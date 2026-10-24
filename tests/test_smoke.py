"""Headless checks for the capture layer (real devices, short clips).

Run:  python tests/test_smoke.py
It plays a short 880 Hz tone, so keep the system volume moderate.
"""

from __future__ import annotations

import array
import math
import os
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("WASAPI_RECORDER_DATA", os.path.join(ROOT, "_records_test"))

import pyaudiowpatch as pyaudio  # noqa: E402

import audio_devices  # noqa: E402
import mixing  # noqa: E402
import recorder as rec_mod  # noqa: E402


def tone(rate: int, seconds: float, freq: float = 880.0, amp: float = 0.35) -> bytes:
    samples = array.array("h")
    for i in range(int(rate * seconds)):
        value = int(math.sin(2 * math.pi * freq * i / rate) * amp * 32767)
        samples.append(value)
        samples.append(value)
    return samples.tobytes()


def play(pa, data: bytes, rate: int, device_index: int | None) -> None:
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=2,
        rate=rate,
        output=True,
        output_device_index=device_index,
        frames_per_buffer=1024,
    )
    try:
        step = 4096 * 4
        for i in range(0, len(data), step):
            stream.write(data[i:i + step])
    finally:
        stream.stop_stream()
        stream.close()


def wav_peak(path: str, start: float, end: float) -> float:
    with wave.open(path, "rb") as wav:
        rate = wav.getframerate()
        wav.setpos(max(0, int(start * rate)))
        data = wav.readframes(max(0, int((end - start) * rate)))
    return mixing.peak_of(data)


def main() -> int:
    pa = pyaudio.PyAudio()
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(("PASS " if ok else "FAIL ") + name + ("  " + detail if detail else ""))

    records: list[rec_mod.Session] = []
    try:
        found = audio_devices.discover(pa)
        system = found["system"]
        mic = found["mic"]
        check("loopback device", system is not None,
              "%s @%dHz %dch" % (system.short_name, system.rate, system.channels) if system else str(found["errors"]))
        check("microphone device", mic is not None,
              "%s @%dHz %dch" % (mic.short_name, mic.rate, mic.channels) if mic else str(found["errors"]))
        if system is None:
            return 1

        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        out_index = int(wasapi["defaultOutputDevice"])
        rate = system.rate

        # ---- 1. no playback at all: the file must still cover the wall time --
        rec = rec_mod.Recorder(pa)
        rec.devices = found
        rec.start("system")
        writer = rec.writer("system")
        started = time.perf_counter()
        time.sleep(1.5)
        sess = rec.stop()
        wall = time.perf_counter() - started
        records.append(sess)
        check("silent session keeps full length", abs(sess.duration - wall) < 0.35,
              "audio %.3fs vs wall %.3fs" % (sess.duration, wall))
        print("     padded %.0f%% of frames" % (100.0 * writer.padded_frames / max(1, sess.frames)))
        check("silent session has no signal", wav_peak(sess.track("system").path, 0.2, 1.4) < 0.01,
              "peak=%.4f" % wav_peak(sess.track("system").path, 0.2, 1.4))

        # ---- 2. loopback captures a tone, then 1 s of silence ---------------
        rec = rec_mod.Recorder(pa)
        rec.devices = found
        rec.start("system")
        writer = rec.writer("system")
        started = time.perf_counter()
        time.sleep(0.2)
        play(pa, tone(rate, 1.0), rate, out_index)
        time.sleep(1.0)
        sess = rec.stop()
        wall = time.perf_counter() - started
        records.append(sess)
        peak = wav_peak(sess.track("system").path, 0.0, min(2.5, sess.duration))
        check("system captured the tone", peak > 0.05, "peak=%.3f" % peak)
        check("system length matches wall time", abs(sess.duration - wall) < 0.35,
              "audio %.3fs vs wall %.3fs" % (sess.duration, wall))
        check("loopback gap was padded", writer.padded_frames > 0,
              "%d frames (%.2fs)" % (writer.padded_frames, writer.padded_frames / sess.rate))
        check("no dropped callbacks", writer.dropped == 0, "%d" % writer.dropped)

        # ---- 3. microphone ---------------------------------------------------
        rec2 = rec_mod.Recorder(pa)
        rec2.devices = found
        rec2.start("mic")
        time.sleep(1.5)
        sess2 = rec2.stop()
        records.append(sess2)
        check("mic duration", 1.4 <= sess2.duration <= 2.4, "%.3fs" % sess2.duration)

        # ---- 4. export / mixing ---------------------------------------------
        out_dir = os.path.join(ROOT, "_records_test")
        os.makedirs(out_dir, exist_ok=True)
        mixed_path = os.path.join(out_dir, "mixed.wav")
        progress: list[float] = []
        mixing.export_mixed(
            mixed_path,
            [(sess.track("system").path, "system")],
            sess.frames,
            sess.rate,
            {"system": 0.5},
            progress=progress.append,
        )
        info = mixing.probe_wav(mixed_path)
        check("export is 16-bit stereo", info["channels"] == 2 and info["rate"] == sess.rate, str(info))
        check("export length matches", abs(info["seconds"] - sess.duration) < 0.05,
              "%.3f vs %.3f" % (info["seconds"], sess.duration))
        check("export progress reported", bool(progress) and progress[-1] > 0.99, str(progress[-1:]))

        # ---- 5. both tracks in one session, mixed with both gains -----------
        rec3 = rec_mod.Recorder(pa)
        rec3.devices = found
        rec3.start("mix")
        time.sleep(0.2)
        play(pa, tone(rate, 0.8, 660.0), rate, out_index)
        time.sleep(0.5)
        sess3 = rec3.stop()
        records.append(sess3)
        check("mix has both tracks", {t.key for t in sess3.tracks} == {"system", "mic"},
              str([t.key for t in sess3.tracks]))
        check("mix tracks equal length", len({t.frames for t in sess3.tracks}) == 1,
              str([t.frames for t in sess3.tracks]))

        mix_path = os.path.join(out_dir, "mix_full.wav")
        mixing.export_mixed(
            mix_path,
            [(t.path, t.key) for t in sess3.tracks],
            sess3.frames,
            sess3.rate,
            {"system": 1.0, "mic": 1.0},
        )
        info3 = mixing.probe_wav(mix_path)
        check("mix export ok", info3["channels"] == 2 and abs(info3["seconds"] - sess3.duration) < 0.05,
              str(info3))

        # gain 0 must silence a track without changing its length
        sys_only = os.path.join(out_dir, "sys_only.wav")
        mixing.export_mixed(sys_only, [(t.path, t.key) for t in sess3.tracks],
                            sess3.frames, sess3.rate, {"system": 1.0, "mic": 0.0})
        check("zero gain keeps length", abs(mixing.probe_wav(sys_only)["seconds"] - sess3.duration) < 0.05)
    finally:
        for session in records:
            rec_mod.cleanup_session(session)
        pa.terminate()

    failed = [c for c in checks if not c[1]]
    print("\n%d checks, %d failed" % (len(checks), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
