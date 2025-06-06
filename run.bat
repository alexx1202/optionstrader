@echo off
pushd "%~dp0"
REM Load API credentials from config if present and export them
for /f "delims=" %%A in ('python -c "import json,sys;cfg=json.load(open(\"trade_config.json\"));print(cfg.get(\"api_key\",\"\"))"') do set "BYBIT_API_KEY=%%A"
for /f "delims=" %%A in ('python -c "import json,sys;cfg=json.load(open(\"trade_config.json\"));print(cfg.get(\"api_secret\",\"\"))"') do set "BYBIT_API_SECRET=%%A"
REM Call the script with the config file located next to this batch file
python optionstrader.py trade_config.json
pause
