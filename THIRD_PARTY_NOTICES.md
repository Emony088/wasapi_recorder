# 第三方组件与许可

本项目在运行时依赖以下第三方组件。发布源码时它们不会被打包进仓库；
发布 `dist/WasapiRecorder.exe` 时，PyAudioWPatch 的二进制模块与 PortAudio
会被打包进可执行文件，因此这里列出对应的许可与链接。

## PyAudioWPatch

* 用途：提供带 WASAPI 环回支持的 PortAudio 绑定
* 版本：0.2.12.9
* 许可：MIT（PyAudio 的 fork，继承 PyAudio / PortAudio 的 MIT 许可）
* 仓库：https://github.com/s0d3s/PyAudioWPatch
* 说明：`_portaudiowpatch.*.pyd` 静态链接 PortAudio 19.x

## PortAudio

* 用途：跨平台音频 I/O 库（经 PyAudioWPatch 使用）
* 许可：MIT
* 站点：https://www.portaudio.com/
* 源码：https://github.com/PortAudio/portaudio

## PyInstaller

* 用途：将程序打包为单文件 exe
* 版本：6.x
* 许可：GPL-2.0-or-later，附带 Bootloader Exception（允许将打包产物按自有许可分发）
* 站点：https://pyinstaller.org/
* 说明：仅构建期使用，不进入运行时依赖

## Python / Tk

* 用途：解释器与图形界面
* 许可：PSF License
* 站点：https://www.python.org/

---

如果你的项目需要再分发上述二进制，请保留各自仓库中的 LICENSE 文本。
