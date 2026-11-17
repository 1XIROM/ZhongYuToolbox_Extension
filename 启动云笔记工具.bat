@echo off
setlocal
title Toolbox Launcher

rem ============================================================
rem  One-click start for the three background tasks:
rem    1) static server http://127.0.0.1:8088  (web-toolbox\serve.js)
rem    2) Chrome debug instance on port 9222
rem    3) cloud-note watch (html2note.js --watch --check-cloud)
rem  Already-running tasks are skipped (no duplicates).
rem  Run with no argument for a normal start, or with /dry to
rem  print the actions without starting anything.
rem
rem  All paths are derived from this script's own folder.
rem  To pin an OSS host to a fixed IP, add a Chrome flag such as
rem    --host-resolver-rules="MAP oss-host ip-address"
rem  Angle brackets are avoided on purpose: cmd reads them as
rem  redirection even inside rem lines.
rem ============================================================

set "REPO_DIR=%~dp0"
set "SERVE_SCRIPT=%REPO_DIR%web-toolbox\serve.js"
rem  Real HTML-to-note pipeline (it keeps its own config.json + input/output).
rem  Auto-detected: the first folder under PIPELINE_ROOT that contains
rem  html2note.js. Edit PIPELINE_ROOT if your pipeline lives elsewhere.
set "PIPELINE_ROOT=E:\WorkBuddy"
set "WATCH_DIR="
for /d %%D in ("%PIPELINE_ROOT%\*") do if not defined WATCH_DIR if exist "%%~fD\html2note.js" set "WATCH_DIR=%%~fD"
if not defined WATCH_DIR set "WATCH_DIR=%REPO_DIR%pipeline"
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

if not exist "%CHROME%" echo [warn ] Chrome not found: "%CHROME%" -- edit CHROME above
if not exist "%SERVE_SCRIPT%" echo [warn ] serve.js not found: "%SERVE_SCRIPT%"
if not exist "%WATCH_DIR%\html2note.js" echo [warn ] html2note.js not found: "%WATCH_DIR%"

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
    rem  --no-proxy-server is required: if a leftover system proxy is configured
    rem  (e.g. Clash on 127.0.0.1:7890) and the proxy is not running, every
    rem  outbound request inside the page fails with "Failed to fetch", while
    rem  the 127.* bypass rules keep localhost working - looking like only the
    rem  browser is broken. Forcing a direct connection avoids that trap.
    if not defined DRY start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%PROFILE%" --no-proxy-server --no-first-run --no-default-browser-check --window-size=1440,960 http://127.0.0.1:8088/
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
    if not exist "%WATCH_DIR%\html2note.js" (
        echo [skip ] html2note.js not found under "%WATCH_DIR%"
    ) else (
        echo [start] cloud-note watch ...
        if not defined DRY start "CloudNote watch" /D "%WATCH_DIR%" cmd /k node html2note.js --watch --check-cloud
        if defined DRY echo        DRY: node html2note.js --watch --check-cloud
    )
)

echo.
echo ------------------------------------------------------------
echo server  : http://127.0.0.1:8088
echo pipeline: %WATCH_DIR%   (html2note.js + config.json)
echo watch   : %WATCH_DIR%\output   new html becomes pdf, then uploaded
echo pdf     : %WATCH_DIR%\pdf
echo log     : %WATCH_DIR%\logs
echo ------------------------------------------------------------
echo.
echo You can close this window; each task runs in its own window.
ping -n 6 127.0.0.1 >nul
exit /b 0
