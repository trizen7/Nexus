@echo off
call "%~dp0manage.cmd" start
if errorlevel 1 pause & exit /b 1
start "" "http://127.0.0.1:18787"
