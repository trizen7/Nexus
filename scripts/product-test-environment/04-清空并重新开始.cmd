@echo off
call "%~dp0manage.cmd" reset
if errorlevel 1 pause & exit /b 1
start "" "https://127.0.0.1:18788"
