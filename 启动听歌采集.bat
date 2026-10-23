@echo off
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not exist "%PY%" (
  echo 找不到 Python 程序，路径不对。
  echo 请用记事本打开本文件，把 PY= 后面的路径改成 pythonw.exe 的真实位置。
  pause
  exit /b 1
)
"%PY%" "%~dp0local\collector.py" --running
if not errorlevel 1 (
  echo 采集器已经在运行了，不用重复启动。
  echo 如果没有记录，先双击“停止听歌采集.bat”再启动。
  ping -n 6 127.0.0.1 >nul
  exit /b 0
)
del "%~dp0local-data\stop.flag" >nul 2>&1
start "" "%PY%" "%~dp0local\collector.py" >nul 2>&1
echo.
echo QQ 音乐听歌采集器已在后台启动（不弹窗口）。
echo 日志文件：local-data\logs\collector.log
echo 想停止，双击“停止听歌采集.bat”。
ping -n 5 127.0.0.1 >nul