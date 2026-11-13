# -*- coding: utf-8 -*-
r"""
积分台 · 本地可视化服务
========================
一个只用标准库的小服务，做三件事：

  1. 把本仓库目录当静态根托管，浏览器打开 /viewer/index.html 看界面；
  2. 提供 /api/status —— 三份数据文件的存在性与采集时间；
  3. 提供 /api/refresh —— 页面上的「↻ 刷新数据」会调它，
     由服务端**依次**重跑三个抓取脚本，跑完前端自动 reload。

为什么需要它：纯 file:// 打开页面也能看数据（三个 *-data.js 用 <script> 加载），
但浏览器出于安全不允许网页直接启动本机 Python 脚本，所以「一键刷新」必须有个
本地服务转发。不用第三方框架、不用装包，标准库 http.server 就够。

安全约定：
  - 只监听 127.0.0.1，不对外网开放；
  - /api/refresh 只跑下面 SCRIPTS 里的固定脚本，不接受任何 URL 参数当命令；
  - 全程只读本机数据 / 调官方接口，不写客户端文件、不上传任何东西。

用法：
  python viewer/server.py                  # 起服务 + 自动开浏览器
  python viewer/server.py --port 18791     # 换端口
  python viewer/server.py --no-open        # 不自动开浏览器
  python viewer/server.py --no-refresh     # 只托管静态页，禁用刷新 API
  Ctrl+C 停止。
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# 资源根（页面文件）与数据根（*-data.js、自备的 credits_auth.py）要分开：
#   - 源码运行：两者都是仓库根 —— 页面在 <root>/viewer/，数据在 <root>/*-data.js；
#   - 打包成 exe：页面被 PyInstaller 放进临时解包目录（sys._MEIPASS），
#     而数据必须留在 exe 旁边 —— 那里才有用户自己的 *-data.js 和自备凭证模块。
#     若混成一个根，页面能打开但永远读不到 exe 旁边的数据。
FROZEN = bool(getattr(sys, "frozen", False))
_ENV_DATA = os.environ.get("WB_DATA_DIR")

if _ENV_DATA:
    DATA_ROOT = os.path.abspath(_ENV_DATA)
elif FROZEN:
    DATA_ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    DATA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if FROZEN:
    ASSET_ROOT = getattr(sys, "_MEIPASS",
                         os.path.dirname(os.path.abspath(sys.executable)))
else:
    ASSET_ROOT = DATA_ROOT

ROOT = DATA_ROOT          # 兼容旧变量名：状态接口与日志里显示的都是数据根
PAGE = "/viewer/index.html"

# 本机动态端口范围被改成过 1024~15000，落在里面的端口随时可能被系统
# 拿去当出站连接的临时源端口，bind 会以 WinError 10013 被拒（明明没人监听）。
# ⇒ 默认端口固定取 15000 以上，并从 18790 开始顺延找空位。
DEFAULT_PORT = 18790
PORT_TRIES = 8

SCRIPTS = [
    ("workbuddy_fetch.py", "WorkBuddy 用量", "usage-data.js"),
    ("trae_fetch.py", "Trae 用量", "trae-data.js"),
    ("credits_alert.py", "积分到期预警", "credits-data.js"),
]

# 这三个是「数据文件」，请求它们时要走 DATA_ROOT（exe 旁边），
# 其余路径都是页面资源，走 ASSET_ROOT（打包进 exe 的只读资源）。
DATA_FILES = {data for _s, _l, data in SCRIPTS}

ALLOW_REFRESH = True


def decode_bytes(raw):
    """子进程输出可能是 UTF-8，也可能是中文 Windows 的 cp936，逐个试。"""
    if not raw:
        return ""
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def summarize_output(lines, limit=600):
    """把子进程输出压成一行摘要。

    traceback 的关键信息在**最后一行**（异常类型 + 消息），前面几行只是 importlib
    的框架帧。直接按 [:limit] 截断会把最有用的那行切掉 —— 实测查 exe 里的 import
    失败时就只剩一串 "File <frozen importlib...>"，看不到到底抛了什么。
    所以超长时保留「首行 + 末行」，中间用 … 省略。
    """
    if not lines:
        return ""
    tail = lines[-6:]
    text = " | ".join(tail)
    if len(text) <= limit:
        return text
    head, last = tail[0], tail[-1]
    room = limit - len(last) - 5
    if room > 20:
        return (head[:room] + " … | " + last)[:limit]
    return last[-limit:]          # 末行自己就超长：只留它，从左边切


def status_payload():
    out = []
    for _script, label, data in SCRIPTS:
        p = os.path.join(DATA_ROOT, data)
        if os.path.isfile(p):
            out.append({
                "name": label, "file": data, "exists": True,
                "size": os.path.getsize(p),
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p))),
            })
        else:
            out.append({"name": label, "file": data, "exists": False, "size": 0, "mtime": None})
    # 环境自检：WorkBuddy 那个脚本读 ~/.workbuddy/workbuddy.db，
    # 若被沙箱 / 安全软件挡住，sqlite 只会抛一句 "unable to open database file"。
    # 把 home 和 dbExists 一起回出来，排障时一眼能看出是不是路径/权限问题。
    home = os.path.expanduser("~")
    db = os.path.join(home, ".workbuddy", "workbuddy.db")
    return {"ok": True, "root": DATA_ROOT, "frozen": FROZEN,
            "home": home, "dbExists": os.path.isfile(db), "data": out}


def script_command(script):
    """返回「怎么跑某个抓取脚本」的命令行。

    源码运行：直接执行 .py。
    exe 里没有 .py 文件，改成让 exe 自己带 --run 再跑一遍 —— 仍是**独立子进程**，
    好处跟原来一样：某个抓取脚本崩了不会拖垮服务，输出也能被捕获。
    """
    if FROZEN:
        return [sys.executable, "--run", script[:-3]]
    return [sys.executable, script]


def script_env():
    """子进程环境：把数据目录传下去，让脚本把产物写在 exe 旁边而不是临时目录。"""
    env = os.environ.copy()
    env["WB_DATA_DIR"] = DATA_ROOT
    env["PYTHONIOENCODING"] = "utf-8"     # 免得子进程按 GBK 输出、解码时打滑
    return env


def run_refresh():
    """依次重跑三个脚本。一家失败不影响另外两家，结果原样回给前端。"""
    results = []
    for script, label, data in SCRIPTS:
        t0 = time.time()
        # exe 版没有 .py 文件，存在性检查换成「模块是否已打进包里」，由启动器保证
        if not FROZEN and not os.path.isfile(os.path.join(ASSET_ROOT, script)):
            results.append({"name": label, "ok": False, "seconds": 0,
                            "message": "找不到脚本 " + script})
            continue
        try:
            # 用 sys.executable 而不是 "python"：避免 PATH 里没有解释器 / 指向别的环境。
            # capture_output 不让子进程的中文输出直接打到 GBK 控制台上炸编码。
            proc = subprocess.run(
                script_command(script), cwd=DATA_ROOT, env=script_env(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=180,
            )
            text = decode_bytes(proc.stdout).strip()
            lines = [x for x in text.splitlines() if x.strip()]
            msg = summarize_output(lines)
            if proc.returncode != 0 and "unable to open database file" in text:
                # WorkBuddy 脚本读 ~/.workbuddy/workbuddy.db。文件「看得见但打不开」
                # 常见于沙箱 / 安全软件拦截，sqlite 只会抛这一句，不说明是哪一步。
                dbp = os.path.join(os.path.expanduser("~"), ".workbuddy", "workbuddy.db")
                launcher = "积分台.exe" if FROZEN else "start-viewer.bat"
                msg += (" | [提示] 服务进程打不开 %s（存在=%s）。多半是当前运行环境"
                        "限制了读取本机 WorkBuddy 目录，请直接双击 %s 启动（别在受限"
                        "终端 / 沙箱里运行）再刷新。" % (dbp, os.path.isfile(dbp), launcher))
            results.append({
                "name": label, "ok": proc.returncode == 0,
                "seconds": round(time.time() - t0, 1),
                "message": msg,
            })
        except subprocess.TimeoutExpired:
            results.append({"name": label, "ok": False,
                            "seconds": round(time.time() - t0, 1), "message": "超时（180 秒）"})
        except Exception as e:
            results.append({"name": label, "ok": False,
                            "seconds": round(time.time() - t0, 1), "message": str(e)[:200]})
        print("  [%s] %-14s %.1fs %s" % (
            "OK" if results[-1]["ok"] else "ERR", label,
            results[-1]["seconds"], results[-1]["message"][:80]))
        sys.stdout.flush()
    return {"ok": True, "results": results}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # 静态根 = 资源根，页面在 /viewer/index.html。
        # 源码运行时资源根就是仓库根，与 file:// 打开时用的 ../usage-data.js 相对路径也对得上。
        super().__init__(*args, directory=ASSET_ROOT, **kwargs)

    def translate_path(self, path):
        # 数据文件必须从 DATA_ROOT 取（exe 版就是 exe 所在目录），
        # 否则打包后页面能打开、却永远读不到 exe 旁边的 *-data.js。
        clean = urlparse(path).path.lstrip("/")
        if clean in DATA_FILES:
            return os.path.join(DATA_ROOT, clean)
        return super().translate_path(path)

    def log_message(self, fmt, *args):
        # 静态资源请求太多，只打印 API 调用，控制台干净点。
        # ⚠️ 别用 args[0] 判断：send_error() → log_error() 传进来的第一个参数是
        #    HTTPStatus 枚举，对它做 `in` 会抛 TypeError（favicon 404 时必踩）。
        #    统一看 self.path，它永远是请求行里的路径字符串。
        if "/api/" in str(self.path or ""):
            super().log_message(fmt, *args)

    def end_headers(self):
        # 刷新后必须拿到最新的 *-data.js，别让浏览器拿缓存里的旧数
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def send_json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            return self.send_json(status_payload())
        if path == "/favicon.ico":
            # 没有站点图标，直接回 204，别让每次开页都刷一条 404
            self.send_response(204)
            self.end_headers()
            return
        if path == "/":
            self.send_response(302)
            self.send_header("Location", PAGE)
            self.end_headers()
            return
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/refresh":
            return self.send_json({"ok": False, "error": "未知接口"}, 404)
        if not ALLOW_REFRESH:
            return self.send_json({"ok": False, "error": "本服务以 --no-refresh 启动，刷新已禁用"}, 403)
        print("\n[刷新] 开始重跑三个抓取脚本 …")
        try:
            return self.send_json(run_refresh())
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)}, 500)


def main():
    global ALLOW_REFRESH
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="积分台本地可视化服务")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="端口（默认 18790）")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--no-refresh", action="store_true", help="禁用 /api/refresh")
    args = ap.parse_args()
    ALLOW_REFRESH = not args.no_refresh

    httpd = None
    port = args.port
    for p in range(args.port, args.port + PORT_TRIES):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError as e:
            print("  端口 %d 不可用（%s），试下一个 …" % (p, e.__class__.__name__))
    if httpd is None:
        print("[ERR] %d~%d 之间没有可用端口。" % (args.port, args.port + PORT_TRIES - 1))
        return 1

    url = "http://127.0.0.1:%d%s" % (port, PAGE)
    print("=" * 62)
    print(" 积分台 · WorkBuddy & Trae")
    print("=" * 62)
    print(" 页面   : " + url)
    print(" 数据根 : " + DATA_ROOT)
    if ASSET_ROOT != DATA_ROOT:
        print(" 资源根 : " + ASSET_ROOT + "（打包内置）")
    print(" 运行   : " + ("exe 单文件" if FROZEN else "Python 源码"))
    print(" 刷新   : " + ("已启用（页面右上角按钮）" if ALLOW_REFRESH else "已禁用"))
    for s in status_payload()["data"]:
        print("   - %-20s %s" % (s["file"], (s["mtime"] or "缺数据") if s["exists"] else "缺数据"))
    print(" 停止   : 按 Ctrl+C，或直接关掉这个窗口")
    print("=" * 62)
    sys.stdout.flush()

    if not args.no_open:
        # 稍等一拍，等服务器真正进入 accept 循环再开浏览器
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
