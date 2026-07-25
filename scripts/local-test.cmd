@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0local-test.ps1" %*
exit /b %ERRORLEVEL%
