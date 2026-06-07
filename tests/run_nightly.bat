@echo off
cd /d c:\Users\cmyer\Documents\PulleyWebApp
taskkill /F /IM python.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul
set PULLEY_NIGHTLY=1
.\.venv312\Scripts\python.exe tests\run_tests.py --flask-port 5099 --dash-port 5098 --no-browser --exit-when-done >> "%LOCALAPPDATA%\Temp\pulley_test_run.log" 2>&1
