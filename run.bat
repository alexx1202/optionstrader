@echo off
pushd "%~dp0"
REM Call the script with the config file located next to this batch file.
python optionstrader.py trade_config.json
pause
