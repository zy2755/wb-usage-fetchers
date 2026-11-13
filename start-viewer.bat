@echo off
chcp 936 >nul
title 积分台 - WorkBuddy 与 Trae
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 没找到 python。请先安装 Python 3.8+ 并勾选 Add to PATH。
  echo.
  pause
  exit /b 1
)

echo [积分台] 正在启动本地服务...
echo [积分台] 浏览器会自动打开；若没有，手动访问 http://127.0.0.1:18790/viewer/index.html
echo [积分台] 首次使用建议先点页面右上角「刷新数据」，让三份数据都是最新的。
echo [积分台] 关闭本窗口即可停止服务。
echo.
python viewer\server.py
echo.
echo [积分台] 服务已停止。
pause
