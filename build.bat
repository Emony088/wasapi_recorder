@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

echo [1/3] 安装运行依赖 ...
python -m pip install -r requirements.txt -i %MIRROR%
if errorlevel 1 goto :fail

echo [2/3] 安装打包工具 PyInstaller ...
python -m pip install pyinstaller -i %MIRROR%
if errorlevel 1 goto :fail

echo [3/3] 打包 ...
python build.py
if errorlevel 1 goto :fail

echo.
echo 完成：dist\WasapiRecorder.exe
pause
exit /b 0

:fail
echo.
echo 构建失败，请查看上面的错误信息。
pause
exit /b 1
