# -*- coding: utf-8 -*-
r"""积分台 · 可执行版启动器（也是 PyInstaller 的打包入口）
============================================================
双击 exe = 起本地服务 + 打开浏览器。源码里 `python viewer/launcher.py` 行为一致。

它只做四件事，HTTP 逻辑全部复用 viewer/server.py，不重复实现：

  1. 定「数据目录」：exe 版 = exe 所在目录，源码版 = 仓库根。
     并通过 WB_DATA_DIR 传给子进程 / server —— **数据文件必须落在用户看得见的地方**，
     不能跟着 PyInstaller 的临时解包目录一起消失。
  2. 把数据目录塞进 sys.path，这样放在 exe 旁边的自备模块
     （credits_auth.py / trae_crypto_table.py）能被正常 import。
  3. 首次启动发现三份数据都缺时，先跑一遍采集再起服务，避免一打开就是空页面。
  4. 隐藏参数 `--run <模块>`：exe 里没有 .py 文件了，server 刷新数据时
     用「exe 自己再跑一遍 + --run」把抓取脚本当成独立子进程执行，
     保持「一个脚本崩了不拖垮服务」的原有特性。
"""
import argparse
import importlib
import os
import subprocess
import sys
import traceback

APP_NAME = "积分台"
APP_VERSION = "1.0.0"

# 与 server.py 的 SCRIPTS 一一对应（模块名 = 脚本名去掉 .py）
MODULES = ("workbuddy_fetch", "trae_fetch", "credits_alert")
DATA_FILES = ("usage-data.js", "trae-data.js", "credits-data.js")


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """exe 版 = exe 所在目录；源码版 = viewer/ 的上级（仓库根）。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def self_cmd(*args):
    """拼一条「再跑一遍自己」的命令行。

    exe 版直接执行自己；源码版要带上 launcher.py 的路径，否则参数会被当成 Python 的参数。
    """
    if is_frozen():
        return [sys.executable] + list(args)
    return [sys.executable, os.path.abspath(__file__)] + list(args)


def prepare(data_dir):
    """确定数据目录、注入环境变量与 sys.path，返回绝对路径。"""
    d = os.path.abspath(data_dir or app_dir())
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    os.environ["WB_DATA_DIR"] = d
    # 自备模块优先从数据目录找：用户把 credits_auth.py 丢在 exe 旁边就能用
    for p in (d, os.path.dirname(os.path.abspath(__file__))):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    return d


def run_module(name, data_dir):
    """在当前进程里跑一个抓取脚本，返回退出码。

    workbuddy_fetch.py 是「脚本式」的（模块级顺序执行、没有 main），
    所以 import 本身就是运行；另外两个是 main() 式，import 后显式调用。
    统一成「有 main 就调，没有就认为 import 已经跑完」，两种都能覆盖。
    """
    if name not in MODULES:
        print("[ERR] 未知模块 %r，可选：%s" % (name, "、".join(MODULES)))
        return 2
    os.chdir(data_dir)                    # 脚本里有相对路径的写法都按数据目录解析
    sys.argv = [name + ".py"]             # argparse 只看 sys.argv[0] 之后的内容，给它一个干净的名字
    try:
        mod = importlib.import_module(name)
        fn = getattr(mod, "main", None)
        rc = fn() if callable(fn) else 0
        return rc if isinstance(rc, int) else 0
    except SystemExit as e:               # 脚本自己退了：沿用它的退出码
        if isinstance(e.code, int):
            return e.code
        return 0 if e.code is None else 1
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass


def set_console_title(text):
    """Windows 控制台标题，方便在任务栏里认出这个窗口（失败就算了）。"""
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(text)
    except Exception:
        pass


def missing_data(data_dir):
    return [f for f in DATA_FILES if not os.path.isfile(os.path.join(data_dir, f))]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog=APP_NAME,
        description="积分台 · WorkBuddy & Trae 的积分消耗与到期预警",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="双击 exe 即可；命令行下 --port 换端口、--data-dir 换数据目录。",
    )
    ap.add_argument("--run", metavar="MODULE", help=argparse.SUPPRESS)   # 内部用，不展示
    ap.add_argument("--data-dir", metavar="DIR", help="数据目录（默认 exe / 仓库所在目录）")
    ap.add_argument("--port", type=int, default=None, help="端口（默认 18790，占用则顺延）")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--no-refresh", action="store_true", help="禁用刷新，且首次启动不自动采集")
    ap.add_argument("--version", action="version", version="%s %s" % (APP_NAME, APP_VERSION))
    args, _extra = ap.parse_known_args(argv)

    data_dir = prepare(args.data_dir)

    # ---- 内部模式：跑单个抓取脚本（由 server 的刷新逻辑调起）
    if args.run:
        return run_module(args.run, data_dir)

    set_console_title("%s · %s" % (APP_NAME, APP_VERSION))

    # ---- 首次启动：三份数据都缺就先采集一次，别让人对着空页面猜
    lack = missing_data(data_dir)
    if lack and not args.no_refresh:
        print("=" * 62)
        print(" 首次启动：%s 下还没有数据，先采集一次（可能要十几秒）" % data_dir)
        print(" 缺少：%s" % "、".join(lack))
        print("=" * 62)
        sys.stdout.flush()
        env = dict(os.environ, WB_DATA_DIR=data_dir, PYTHONIOENCODING="utf-8")
        for m in MODULES:
            print("\n--- 采集 %s ---" % m)
            sys.stdout.flush()
            try:
                subprocess.run(self_cmd("--run", m), cwd=data_dir, env=env, timeout=180)
            except subprocess.TimeoutExpired:
                print("[WARN] %s 超时（180 秒），跳过" % m)
            except Exception as e:
                print("[WARN] %s 启动失败：%s" % (m, e))
        left = missing_data(data_dir)
        if left:
            print("\n[WARN] 仍有 %s 没生成，页面会显示空态；可在页面右上角手动「刷新数据」。"
                  % "、".join(left))

    # ---- 起服务（延迟导入：--run 分支没必要加载 HTTP 那套东西）
    import server
    srv_argv = []
    if args.port:
        srv_argv += ["--port", str(args.port)]
    if args.no_open:
        srv_argv.append("--no-open")
    if args.no_refresh:
        srv_argv.append("--no-refresh")
    sys.argv = ["server.py"] + srv_argv
    return server.main()


if __name__ == "__main__":
    sys.exit(main())
