@echo off
chcp 936 >nul
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"
echo ===== 采集器自检 =====
"%PY%" "%~dp0local\collector.py" --check
echo.
echo ===== 自检结束，把上面的内容截图发我即可 =====
pause