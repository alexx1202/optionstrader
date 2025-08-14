@echo off
REM Run optionstrader.py without a trade_config.json file
REM Prompt for API credentials only if they are not already set

pushd %~dp0

if "%BYBIT_API_KEY%"=="" set /p BYBIT_API_KEY=Enter your Bybit API key: 
if "%BYBIT_API_SECRET%"=="" set /p BYBIT_API_SECRET=Enter your Bybit API secret: 

python optionstrader.py
pause

