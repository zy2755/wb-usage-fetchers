# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把积分台打成单文件 exe。

用法（在仓库根执行）：
    python -m PyInstaller --noconfirm --clean credits_viewer.spec
或直接双击 build-exe.bat。

产物：dist/积分台.exe

打包内容的取舍：
  - 入口是 viewer/launcher.py，不是 server.py —— 启动器还要处理
    「首次启动自动采集」「--run 自我重跑抓取脚本」这些事；
  - viewer/*.html/css/js 作为资源打进去（datas），运行时从 sys._MEIPASS 读；
  - 三个抓取脚本作为**模块**打进去（hiddenimports），exe 里没有 .py 文件，
    靠 launcher 的 --run 分支 import 后执行；
  - **不打包任何数据**：*-data.js 是你的真实用量，必须留在 exe 旁边由用户自己生成。
"""
import os

ROOT = os.path.abspath(SPECPATH)

# 页面资源：新增 viewer 下的静态文件时记得加到这里
VIEWER_ASSETS = ["index.html", "style.css", "app.js", "report.js"]

a = Analysis(
    [os.path.join(ROOT, "viewer", "launcher.py")],
    pathex=[ROOT, os.path.join(ROOT, "viewer")],
    binaries=[],
    datas=[(os.path.join(ROOT, "viewer", name), "viewer") for name in VIEWER_ASSETS],
    # 三个抓取脚本是「按模块 import」执行的，静态分析看不到，必须显式声明
    hiddenimports=["workbuddy_fetch", "trae_fetch", "credits_alert", "server"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 只用标准库，把明显用不到的大件排掉，能少几 MB 也少点误报
    excludes=[
        "tkinter", "unittest", "doctest", "pydoc", "pydoc_data",
        "test", "distutils", "lib2to3", "setuptools", "pip",
        "numpy", "pandas", "PIL", "matplotlib",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="积分台",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 不压缩（UPX 容易被安全软件误报，体积换省心）
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # 保留控制台窗口：能看到采集进度和报错，也方便「关窗口即退出」
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=os.path.join(ROOT, "version_info.txt"),
)
