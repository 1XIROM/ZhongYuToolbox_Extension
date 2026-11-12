@echo off
chcp 65001 >nul
setlocal
title Toolbox Launcher

rem ============================================================
rem  One-click start for the three background tasks:
rem    1) static server http://127.0.0.1:8088  (web-toolbox\serve.js)
rem    2) Chrome debug instance (port 9222)
rem    3) cloud-note watch (html2note.js --watch --check-cloud)
rem  Anything already running is skipped (no duplicates).
rem  Usage: 启动云笔记工具.bat          normal start
rem         启动云笔记工具.bat /dry     只打印将要执行的动作
rem
rem  所有路径都从本脚本所在目录推导，换机器时只改下面 3 个变量即可。
rem  如需给某个 OSS 域名指定 IP，可自行给 Chrome 追加：
rem    --host-resolver-rules="MAP <oss-host> <ip>"
rem ============================================================

set "REPO_DIR=%~dp0"
set "WATCH_DIR=%REPO_DIR%pipeline"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
set "PROFILE=%TEMP%\zhongyu-toolbox-chrome-prof"
set "DRY="
if /I "%~1"=="/dry" set "DRY=1"

echo ============================================================
echo   Toolbox launcher
echo   1) static server 127.0.0.1:8088
echo   2) Chrome debug 127.0.0.1:9222
echo   3) cloud-note watch (--watch --check-cloud)
echo ============================================================
echo.

if not exist "%CHROME%" echo [warn ] Chrome not found: "%CHROME%"  -- edit CHROME above

rem ---------- 1. static server ----------
netstat -ano | findstr /R /C:"127.0.0.1:8088 .*LISTENING" >nul
if errorlevel 1 (
    echo [start] static server ...
    if not defined DRY start "Toolbox serve" /D "%REPO_DIR%" cmd /k node web-toolbox\serve.js 8088
    if defined DRY echo        DRY: node web-toolbox\serve.js 8088
    ping -n 3 127.0.0.1 >nul
) else (
    echo [skip ] 8088 already listening
)

rem ---------- 2. chrome debug ----------
netstat -ano | findstr /R /C:"127.0.0.1:9222 .*LISTENING" >nul
if errorlevel 1 (
    echo [start] chrome debug instance ...
    if not defined DRY start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check --window-size=1440,960 http://127.0.0.1:8088/
    if defined DRY echo        DRY: chrome --remote-debugging-port=9222 --user-data-dir="%PROFILE%"
    ping -n 6 127.0.0.1 >nul
) else (
    echo [skip ] 9222 already listening
)

rem ---------- 3. watch ----------
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like '*html2note.js*' }) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo [skip ] watch already running
) else (
    echo [start] cloud-note watch ...
    if not defined DRY start "CloudNote watch" /D "%WATCH_DIR%" cmd /k node html2note.js --watch --check-cloud
    if defined DRY echo        DRY: node html2note.js --watch --check-cloud
)

echo.
echo ------------------------------------------------------------
echo server  : http://127.0.0.1:8088
echo log     : pipeline\logs\watch-YYYY-MM-DD.log
echo watch   : pipeline\output  (new html =^> pdf =^> cloud note)
echo download: pipeline\input
echo ------------------------------------------------------------
echo.
echo You can close this window; each task runs in its own window.
ping -n 6 127.0.0.1 >nul
exit /b 0
