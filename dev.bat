@echo off
setlocal

REM BibexPy v2 development reset launcher.
REM Stops old local servers, clears generated caches/build outputs
REM except apps\api\storage, then starts backend and frontend.

set "ROOT=%~dp0"
set "SCRIPT=%ROOT%scripts\dev-reset.ps1"

if not exist "%SCRIPT%" (
  echo Missing script: %SCRIPT%
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo BibexPy dev reset failed with exit code %EXIT_CODE%.
  pause
)

exit /b %EXIT_CODE%
