"""Device discovery helpers built on top of PyAudioWPatch (WASAPI)."""

from __future__ import annotations

import dataclasses
import typing as t

import pyaudiowpatch as pyaudio

__all__ = ["Device", "AudioDeviceError", "default_loopback", "default_microphone", "discover"]


class AudioDeviceError(RuntimeError):
    """Raised when the requested WASAPI endpoint cannot be resolved."""


@dataclasses.dataclass(frozen=True)
class Device:
    index: int
    name: str
    rate: int
    channels: int
    loopback: bool = False

    @property
    def short_name(self) -> str:
        return " ".join(self.name.replace("\n", " ").split())


def _wasapi_host(pa: "pyaudio.PyAudio") -> dict:
    try:
        return pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError as exc:  # pragma: no cover - depends on the machine
        raise AudioDeviceError("系统没有可用的 WASAPI 主机接口") from exc


def _as_device(info: dict, loopback: bool) -> Device:
    channels = int(info.get("maxInputChannels") or 0)
    if channels <= 0 and loopback:
        channels = 2
    return Device(
        index=int(info["index"]),
        name=str(info["name"]),
        rate=int(round(float(info.get("defaultSampleRate") or 48000))),
        channels=max(1, min(2, channels)),
        loopback=loopback,
    )


def default_loopback(pa: "pyaudio.PyAudio") -> Device:
    """Return the loopback capture endpoint of the *default* output device.

    This is what "record what you hear" means: the loopback device mirrors the
    active render endpoint, so it follows the speakers/headphones that Windows
    currently uses.  Switching from speakers to headphones changes the default
    endpoint, and the next recording picks up the new one.
    """

    host = _wasapi_host(pa)
    out_index = host.get("defaultOutputDevice", -1)
    info = None
    if out_index is not None and int(out_index) >= 0:
        try:
            info = pa.get_wasapi_loopback_analogue_by_index(int(out_index))
        except OSError:
            info = None
    if info is None:
        try:
            info = pa.get_default_wasapi_loopback()
        except OSError:
            info = None
    if info is None:
        try:
            info = next(iter(pa.get_loopback_device_info_generator()))
        except (StopIteration, OSError):
            info = None
    if info is None:
        raise AudioDeviceError("没有找到扬声器 / 耳机回环设备，请确认已连接播放设备")
    return _as_device(info, loopback=True)


def default_microphone(pa: "pyaudio.PyAudio") -> Device:
    """Return the default WASAPI capture endpoint (a real microphone)."""

    host = _wasapi_host(pa)
    candidates: t.List[dict] = []
    in_index = host.get("defaultInputDevice", -1)
    if in_index is not None and int(in_index) >= 0:
        try:
            candidates.append(pa.get_device_info_by_index(int(in_index)))
        except OSError:
            pass
    try:
        candidates.append(pa.get_default_input_device_info())
    except OSError:
        pass

    for info in candidates:
        if int(info.get("maxInputChannels") or 0) <= 0:
            continue
        if str(info.get("name", "")).endswith("[Loopback]"):
            continue
        if int(info.get("hostApi", -1)) != int(host["index"]):
            continue
        return _as_device(info, loopback=False)

    for info in pa.get_device_info_generator_by_host_api(int(host["index"])):
        if int(info.get("maxInputChannels") or 0) > 0 and not str(info["name"]).endswith("[Loopback]"):
            return _as_device(info, loopback=False)
    raise AudioDeviceError("没有找到可用的麦克风设备")


def discover(pa: "pyaudio.PyAudio") -> dict:
    """Resolve both endpoints, reporting failures instead of raising."""

    result: dict = {"system": None, "mic": None, "errors": {}}
    try:
        result["system"] = default_loopback(pa)
    except AudioDeviceError as exc:
        result["errors"]["system"] = str(exc)
    try:
        result["mic"] = default_microphone(pa)
    except AudioDeviceError as exc:
        result["errors"]["mic"] = str(exc)
    return result
