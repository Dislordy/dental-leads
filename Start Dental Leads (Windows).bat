@echo off
rem Double-click to start the Dental Leads website. Keep this window open while you use it.
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul || goto nopython
if not exist .venv (
  echo First run: setting things up, about a minute...
  %PY% -m venv .venv || goto failed
)
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt || goto failed
echo Opening Dental Leads in your browser. Close this window to stop it.
python -m leadgen.web
pause
exit /b
:nopython
echo Python is not installed. Get it from https://www.python.org/downloads/
echo During setup, tick "Add python.exe to PATH". Then double-click this file again.
pause
exit /b 1
:failed
echo Setup failed. See the messages above.
pause
exit /b 1
