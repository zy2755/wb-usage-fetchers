@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
title 打包 积分台 exe

echo ============================================================
echo   打包「积分台」为单文件 exe（PyInstaller）
echo ============================================================
echo.

rem 想指定解释器（比如某个已装 PyInstaller 的 Python）：
rem   set PYTHON=D:\Python3.13.15\python.exe
if not defined PYTHON set "PYTHON=python"

%PYTHON% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 goto :nopi

echo [1/2] 清理旧产物 ...
if exist build rmdir /s /q build
if exist "dist\积分台.exe" del /q "dist\积分台.exe" >nul 2>nul

echo [2/2] 开始打包（首次约 30~60 秒）...
%PYTHON% -m PyInstaller --noconfirm --clean credits_viewer.spec
if errorlevel 1 goto :failed

echo.
echo [OK] 产物：dist\积分台.exe
for %%f in ("dist\积分台.exe") do echo      大小：%%~zf 字节
echo.
echo 用法：把 exe 拷到任意目录双击即可，数据文件（*-data.js）会生成在 exe 旁边。
echo 想直接沿用现有数据：把 usage-data.js / trae-data.js / credits-data.js
echo 一起拷到 exe 旁边即可。
goto :eof

:nopi
echo [!] 当前 Python 里没有 PyInstaller。
echo     先装：%PYTHON% -m pip install pyinstaller
echo     或指定别的解释器：set PYTHON=路径\python.exe   然后重新运行本脚本
exit /b 1

:failed
echo [x] 打包失败，看上面 PyInstaller 的输出。
exit /b 1
