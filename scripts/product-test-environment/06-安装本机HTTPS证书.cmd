@echo off
setlocal
call "%~dp0manage.cmd" start
if errorlevel 1 pause & exit /b 1
set "CERT=%~dp0data\tls\ca.crt"
if not exist "%CERT%" (
  echo Local HTTPS CA certificate was not generated.
  pause
  exit /b 1
)
certutil.exe -user -addstore -f Root "%CERT%"
if errorlevel 1 (
  echo Failed to trust the local HTTPS CA certificate.
  pause
  exit /b 1
)
echo The Nexus local HTTPS CA is now trusted for this Windows user.
echo Close and reopen the browser before testing HTTPS.
pause
