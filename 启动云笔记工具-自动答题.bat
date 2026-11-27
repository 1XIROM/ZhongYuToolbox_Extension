@echo off
rem ---------------------------------------------------------------
rem  ASCII-only wrapper on purpose: cmd reads .bat with the OEM code
rem  page (936 here) and would mangle non-ASCII literals, so the real
rem  logic lives in launch-auto.ps1 (UTF-8 with BOM, Unicode paths OK).
rem  Double-click to start; run with /dry to preview without starting.
rem ---------------------------------------------------------------
if /I "%~1"=="/dry" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-auto.ps1" -Dry
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-auto.ps1"
)
exit /b %errorlevel%