@echo off
setlocal
set "REPO_ROOT=%~dp0.."
set "PYTHON=%REPO_ROOT%\.local-test\venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" "%~dp0local_test.py" %*
exit /b %ERRORLEVEL%
