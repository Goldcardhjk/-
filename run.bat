@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Jinbu Yihao Agent (v0.69)...

rem 优先用项目自带的 .venv（Pillow + segno 齐全，Vibe Pay 二维码可用）；
rem venv 不在时退回系统 Python（程序仍可运行，扫码窗退化为显示链接文本）。
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    "D:\Programs\Python\python.exe" main.py
)
if errorlevel 1 echo [ERROR] python exited with error - see message above.
pause
