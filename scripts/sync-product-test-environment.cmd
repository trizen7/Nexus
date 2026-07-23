@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync-product-test-environment.ps1" %*
exit /b %ERRORLEVEL%
