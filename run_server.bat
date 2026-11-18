@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not defined JINBU_SERVER_ALLOW_MOCK set JINBU_SERVER_ALLOW_MOCK=1
if not defined JINBU_SERVER_HOST set JINBU_SERVER_HOST=127.0.0.1
if not defined JINBU_SERVER_PORT set JINBU_SERVER_PORT=8765
echo Starting Jinbu Yihao Cloud Server on %JINBU_SERVER_HOST%:%JINBU_SERVER_PORT%...
echo Model API is read from server environment or llm_config.json.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -B cloud_server.py --host %JINBU_SERVER_HOST% --port %JINBU_SERVER_PORT%
) else (
    "D:\Programs\Python\python.exe" -B cloud_server.py --host %JINBU_SERVER_HOST% --port %JINBU_SERVER_PORT%
)
if errorlevel 1 echo [ERROR] cloud server exited with error.
pause