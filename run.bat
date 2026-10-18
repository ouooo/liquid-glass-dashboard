@echo off
REM 一键启动（Windows）——无 Docker 时直接跑 Python
cd /d "%~dp0"
if not defined DASH_DATA_DIR set DASH_DATA_DIR=.\data
if not exist "%DASH_DATA_DIR%" mkdir "%DASH_DATA_DIR%"
echo Starting nexus-dashboard on port 28080...
python api.py
pause
