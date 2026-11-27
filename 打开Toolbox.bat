@echo off
setlocal
title Toolbox viewer

rem ============================================================
rem  Open the web toolbox in a browser - and nothing else.
rem    1) static server http://127.0.0.1:8088  (web-toolbox\serve.js)
rem    2) a normal browser window on that address
rem
rem  The cloud-note upload/download pipeline is NOT started here:
rem  no chrome debug port, no html2note.js --watch.  For that use
rem  the other launcher script next to this file.
rem
rem  An already-running 8088 server is reused (no duplicates).
rem  Run with /dry to print the actions without starting anything.
rem
rem  Paths are derived from this script's own folder, so keep the
rem  file in the repository root.
rem  This file is deliberately ASCII-only: cmd parses batch files in
rem  the system code page, and non-ASCII comments get mis-decoded.
rem ============================================================

set "REPO_DIR=%~dp0"
set "SERVE_SCRIPT=%REPO_DIR%web-toolbox\serve.js"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
set "URL=http://127.0.0.1:8088/"
set "DRY="
if /I "%~1"=="/dry" set "DRY=1"

echo ============================================================
echo   Toolbox viewer  (browser only)
echo   1) static server 127.0.0.1:8088
echo   2) open %URL%
echo   cloud-note pipeline: separate script, not started here
echo ============================================================
echo.

if not exist "%SERVE_SCRIPT%" (
    echo [error] serve.js not found: "%SERVE_SCRIPT%"
    echo         put this .bat back into the repository root
    goto :fail
)

rem ---------- 1. static server ----------
netstat -ano | findstr /R /C:"127.0.0.1:8088 .*LISTENING" >nul
if errorlevel 1 (
    where node >nul 2>nul
    if errorlevel 1 goto :nonode
    echo [start] static server ...
    if not defined DRY start "Toolbox serve" /D "%REPO_DIR%" cmd /k node web-toolbox\serve.js 8088
    if defined DRY echo        DRY: node web-toolbox\serve.js 8088
) else (
    echo [skip ] 8088 already listening
)

rem ---------- wait until the port answers ----------
if not defined DRY (
    set "READY="
    for /l %%i in (1,1,12) do (
        if not defined READY (
            netstat -ano | findstr /R /C:"127.0.0.1:8088 .*LISTENING" >nul
            if not errorlevel 1 set "READY=1"
            if not defined READY ping -n 2 127.0.0.1 >nul
        )
    )
    if not defined READY echo [warn ] 8088 still not listening, opening anyway
)

rem ---------- 2. browser ----------
echo [open ] %URL%
if not defined DRY (
    if exist "%CHROME%" (
        start "" "%CHROME%" "%URL%"
    ) else (
        start "" "%URL%"
    )
)

echo.
echo ------------------------------------------------------------
echo server : %URL%
echo files  : %REPO_DIR%web-toolbox
echo note   : upload/download pipeline is NOT running here
echo          run the cloud-note launcher when you need it
echo ------------------------------------------------------------
echo.
echo You can close this window; the server keeps its own window.
ping -n 5 127.0.0.1 >nul
exit /b 0

:nonode
echo [error] node.exe not found in PATH
echo         install Node.js 21 or newer: https://nodejs.org/
goto :fail

:fail
echo.
echo Nothing started.
ping -n 5 127.0.0.1 >nul
exit /b 1
