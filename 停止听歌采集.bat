@echo off
cd /d "%~dp0"
set "PIDFILE=%~dp0local-data\collector.pid"
if not exist "%PIDFILE%" (
  echo 采集器当前没有在运行。
  ping -n 4 127.0.0.1 >nul
  exit /b 0
)
set /p PID=<"%PIDFILE%"
echo 正在停止，最多等 8 秒让它把数据收尾…
type nul > "%~dp0local-data\stop.flag"
ping -n 9 127.0.0.1 >nul
taskkill /PID %PID% /F >nul 2>&1
del "%~dp0local-data\stop.flag" >nul 2>&1
del "%PIDFILE%" >nul 2>&1
echo 已停止。
ping -n 3 127.0.0.1 >nul