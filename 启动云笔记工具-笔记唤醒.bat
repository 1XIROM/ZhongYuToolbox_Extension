@echo off
setlocal
title Toolbox Launcher (standalone + note wake)

rem ============================================================
rem  One-click start for five background tasks:
rem    1) static server http://127.0.0.1:8088  (serve.js in this folder)
rem    2) Chrome debug instance on port 9222
rem    3) cloud-note watch (html2note.js --watch --check-cloud)
rem    4) note detector   (scripts\wake_on_note.py --loop 1)
rem    5) wake executor   (scripts\wake_workbuddy.py, called by 4)
rem  Already-running tasks are skipped (no duplicates).
rem
rem  --- the note-wake pair (this copy) --------------------------
rem  detector : watches input\ for a NEW folder - which is exactly
rem             what html2note creates after downloading a note
rem             whose title contains "ds". On a new folder it calls
rem             the executor with a wake-up message.
rem  executor : always runs E:\Program Files\WorkBuddy\WorkBuddy.exe
rem             once first - single-instance, so it brings the
rem             existing window to the front. Then clicks the input
rem             box, pastes the message and presses Enter.
rem
rem  Every new note wakes the assistant immediately, so the old input
rem  sentinel (pipeline.py watch --exit-on-new) is no longer needed:
rem  this detector takes its place.
rem
rem  Options:
rem    /dry     print the actions, start nothing
rem    /probe   also run the executor self-test once (locate the input
rem             box and save a screenshot to %TEMP%\wake_probe.png)
rem
rem  ASCII only on purpose: cmd reads .bat with the OEM code page
rem  (936 here), so non-ASCII literals would be mangled. Angle
rem  brackets are avoided too: cmd reads them as redirection even
rem  inside rem lines.
rem ============================================================

set "REPO_DIR=%~dp0"
rem  strip the trailing backslash so /D "path" stays well formed
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"
set "SERVE_SCRIPT=%REPO_DIR%\web-toolbox\serve.js"

rem  Real HTML-to-note pipeline. Auto-detected: the first folder under
rem  PIPELINE_ROOT that contains html2note.js. Edit it if yours is elsewhere.
set "PIPELINE_ROOT=E:\WorkBuddy"
set "WATCH_DIR="
for /d %%D in ("%PIPELINE_ROOT%\*") do if not defined WATCH_DIR if exist "%%~fD\html2note.js" set "WATCH_DIR=%%~fD"

rem  Fallback: no external pipeline found -- use the copy shipped in this repo.
if not defined WATCH_DIR set "WATCH_DIR=%REPO_DIR%\pipeline"

rem  The two wake modules live in the pipeline folder's scripts\ dir.
set "SCRIPTS=%WATCH_DIR%\scripts"
rem  Fallback: a fresh clone keeps them under pipeline\scripts.
if not exist "%SCRIPTS%\wake_on_note.py" set "SCRIPTS=%REPO_DIR%\pipeline\scripts"
set "NOTE=%SCRIPTS%\wake_on_note.py"
set "WAKE=%SCRIPTS%\wake_workbuddy.py"
set "STATE=%WATCH_DIR%\.workbuddy\wake_note_seen.json"

rem  Python: PATH first, then the interpreter bundled with this session.
set "PY=python"
where python >nul 2>&1
if errorlevel 1 set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"

set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
set "PROFILE=%TEMP%\zhongyu-toolbox-chrome-prof"
set "DRY="
set "PROBE="
if /I "%~1"=="/dry" set "DRY=1"
if /I "%~1"=="/probe" set "PROBE=1"

echo ============================================================
echo   Toolbox launcher (standalone + note wake)
echo   1) static server 127.0.0.1:8088  -  this folder
echo   2) Chrome debug 127.0.0.1:9222
echo   3) cloud-note watch (--watch --check-cloud)
echo   4) note detector (wake_on_note.py --loop 1)
echo   5) wake executor (wake_workbuddy.py, on demand)
echo ============================================================
echo.

if not exist "%CHROME%" echo [warn ] Chrome not found: "%CHROME%" -- edit CHROME above
if not exist "%SERVE_SCRIPT%" echo [warn ] serve.js not found: "%SERVE_SCRIPT%"
if not defined WATCH_DIR echo [warn ] no html2note.js under "%PIPELINE_ROOT%" -- edit PIPELINE_ROOT above
if defined WATCH_DIR if not exist "%NOTE%" echo [warn ] note detector not found: "%NOTE%"
if defined WATCH_DIR if not exist "%WAKE%" echo [warn ] executor not found: "%WAKE%"

rem ---------- 1. static server ----------
netstat -ano | findstr /R /C:"127.0.0.1:8088 .*LISTENING" >nul
if errorlevel 1 (
    echo [start] static server ...
    if not defined DRY start "Toolbox serve" /D "%REPO_DIR%" cmd /k node serve.js 8088
    if defined DRY echo        DRY: node serve.js 8088  in %REPO_DIR%
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
    if not defined WATCH_DIR (
        echo [skip ] no pipeline folder found under "%PIPELINE_ROOT%"
    ) else (
        if not exist "%WATCH_DIR%\html2note.js" (
            echo [skip ] html2note.js not found under "%WATCH_DIR%"
        ) else (
            echo [start] cloud-note watch ...
            if not defined DRY start "CloudNote watch" /D "%WATCH_DIR%" cmd /k node html2note.js --watch --check-cloud
            if defined DRY echo        DRY: node html2note.js --watch --check-cloud  in %WATCH_DIR%
        )
    )
)

rem ---------- 4. note detector ----------
if not defined WATCH_DIR goto :after_detect
if not exist "%NOTE%" (
    echo [skip ] note detector missing: "%NOTE%"
    goto :after_detect
)
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*wake_on_note.py*' }) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo [skip ] note detector already running
) else (
    echo [start] note detector ...
    if not defined DRY start "Note wake" /D "%WATCH_DIR%" cmd /k ""%PY%" "%NOTE%" --loop 1"
    if defined DRY echo        DRY: "%PY%" "%NOTE%" --loop 1  in %WATCH_DIR%
)
:after_detect

rem ---------- 5. wake executor ----------
rem  The executor is not a daemon: the detector calls it for every new note.
rem  /probe runs its self-test once, so the click position can be checked
rem  before a real note arrives (WorkBuddy must be open for that).
if defined PROBE (
    if not exist "%WAKE%" (
        echo [skip ] executor missing: "%WAKE%"
        goto :after_probe
    )
    rem  no parentheses in this echo: cmd would close the block early
    echo [test ] wake executor self-test, locate the input box ...
    if defined DRY (
        echo        DRY: "%PY%" "%WAKE%" --probe
    ) else (
        "%PY%" "%WAKE%" --probe
    )
)
:after_probe

echo.
echo ------------------------------------------------------------
echo server  : http://127.0.0.1:8088  -  root %REPO_DIR%
if defined WATCH_DIR echo pipeline: %WATCH_DIR%   (html2note.js + config.json)
if defined WATCH_DIR echo watch   : %WATCH_DIR%\output   new html becomes pdf, then uploaded
if defined WATCH_DIR echo pdf     : %WATCH_DIR%\pdf
if defined WATCH_DIR echo log     : %WATCH_DIR%\logs
if defined WATCH_DIR echo detect  : %NOTE%
if defined WATCH_DIR echo executor: %WAKE%
if defined WATCH_DIR echo state   : %STATE%
echo ------------------------------------------------------------
echo.
echo First run records what is already in input\ as a baseline and
echo sends nothing; only folders created afterwards wake the assistant.
echo A new folder appears when html2note downloads a note whose title
echo contains "ds", so cloud notes drive this launcher end to end.
echo.
echo You can close this window; each task runs in its own window.
ping -n 6 127.0.0.1 >nul
exit /b 0
