@echo off
rem ===========================================================================
rem  Cloud note  ->  input  ->  DeepSeek  ->  output
rem
rem  What it does (one run):
rem    1) scan the cloud note tool and download new notes into input\
rem    2) read every note in input\, send it to DeepSeek, print everything
rem    3) write one answer file per note into output\
rem
rem  This file is a companion of the toolbox "wake on note" launcher and never
rem  modifies it: the original launcher, its folders and its config stay
rem  untouched. Here we only read the same ports (8088/9222) and call the
rem  same html2note.js --cloud entry the toolbox already uses.
rem
rem  ASCII only on purpose: cmd reads a .bat with the OEM codepage (936 on
rem  Chinese Windows), so Chinese bytes in this file would be garbled before
rem  "chcp 65001" takes effect. All Chinese output comes from note_answer.py.
rem
rem  Options (forwarded to the python script):
rem    /all              answer every note that has no answer yet
rem    /limit N          answer at most N notes this run (default 3)
rem    /engine harness   use local dsh headless CLI (default)
rem    /engine api       use an OpenAI-compatible HTTP endpoint
rem    /force            re-answer notes that already have an answer file
rem    /no-cloud         skip the cloud scan, only use what is in input\
rem    /cloud-all        full backfill on the cloud side (first run)
rem    /dry              print the whole plan, call no model, write nothing
rem    /no-start         do not auto-start the 8088/9222 stack
rem    /help             show the full option list
rem ===========================================================================
setlocal EnableExtensions
chcp 65001 >nul
title Cloud note - answer pipeline
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PYTHONIOENCODING=utf-8"
set "ORCH=%ROOT%\pipeline\scripts\note_answer.py"

echo ===========================================================================
echo   Cloud note  --^>  input  --^>  DeepSeek  --^>  output
echo ===========================================================================
echo   root      : %ROOT%
echo   script    : %ORCH%
echo   key from  : DEEPSEEK_API_KEY  /  .workbuddy\deepseek.key  /  config.api.key
echo   args      : %*
echo ===========================================================================
echo.

if not exist "%ORCH%" goto :no_orch

rem --- locate python: PATH first, then the portable copy the toolbox ships ---
set "PY="
where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
  )
)
if not defined PY goto :no_python

echo python   : %PY%
echo run      : note_answer.py %*
echo log      : %ROOT%\.workbuddy\note_answer.log
echo.

"%PY%" "%ORCH%" %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo [DONE] pipeline finished, exit code 0.
) else (
  echo [FAIL] exit code %RC% - read the messages above, nothing failed silently.
)
echo        answers  : %ROOT%\output\  (one file per note, paths printed above)
echo        full log : %ROOT%\.workbuddy\note_answer.log
echo        harness  : every note is one DSH session; its title carries the note
echo                   name, and opening it shows the request, the tool calls and
echo                   the model reply for that note, turn by turn.
echo.

rem keep the window open when the user double-clicks the file
if "%~1"=="" pause
exit /b %RC%

:no_orch
echo [ERROR] missing script: "%ORCH%"
echo         Put note_answer.py back under %ROOT%\pipeline\scripts\ or restore this bat
echo         next to the note repository.
echo.
pause
exit /b 2

:no_python
echo [ERROR] Python 3 was not found.
echo         Either install Python 3.10+ and add it to PATH, or put the portable
echo         interpreter at:
echo           %%USERPROFILE%%\.workbuddy\binaries\python\versions\3.13.12\python.exe
echo.
pause
exit /b 2
