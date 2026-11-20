# -*- coding: utf-8 -*-
"""签到控制台 · 桌面版入口
pywebview 原生窗口 + 内置本地服务，双击即用，无浏览器痕迹。
运行:  python app_desktop.py     （需 pip install pywebview）
打包:  见 build_exe.bat

窗口起不来时（缺 WebView2 运行时等）自动回退到默认浏览器，服务保持常驻，
不再出现"双击闪一下就没"的情况；异常会写进 gui_data/startup.log。
"""
import os
import sys
import time
import traceback
from pathlib import Path


def _base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _log(msg):
    try:
        d = _base_dir() / "gui_data"
        d.mkdir(exist_ok=True)
        with open(d / "startup.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _fail(msg):
    """无窗口环境下的错误提示（Windows 弹原生对话框，否则打印）"""
    _log("FAIL: " + msg)
    print(msg)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "签到控制台", 0x10)
    except Exception:
        pass


def main():
    _log("启动")
    try:
        import dashboard  # 同目录的 Web 控制台模块
    except Exception as e:
        _fail(f"找不到 dashboard.py，请把本程序放在项目根目录（与 cmcc_sign.py 同级）。\n\n{e}")
        return

    # exe 只是外壳，任务由系统 Python 跑：缺 Python / 缺依赖时先说人话，
    # 而不是等用户点了签到才在日志里吐一个 WinError 2。
    ok, msg = dashboard.preflight(force=True)
    if not ok:
        _fail(msg)

    srv, err = dashboard.make_server()
    if srv is None:
        _fail(err)
        return

    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    _log(f"服务已监听 {url}")

    try:
        import webview
    except ImportError:
        webview = None

    if webview is not None:
        try:
            icon = os.path.join(str(_base_dir()), "app.ico")
            webview.create_window(
                "签到控制台", url, width=1440, height=920,
                min_size=(1000, 680), background_color="#0f1115",
            )
            t0 = time.time()
            try:
                webview.start(icon=icon if os.path.exists(icon) else None)
            except TypeError:
                webview.start()
            spent = time.time() - t0
            _log(f"webview.start 返回，耗时 {spent:.1f}s")
            # 正常使用下 start() 会一直阻塞到窗口关闭；几秒内就返回说明窗口没起来
            if spent > 3:
                _log("窗口正常关闭，退出")
                os._exit(0)
            _log("窗口疑似未创建，回退浏览器模式")
        except Exception as e:
            _log("webview 异常：" + traceback.format_exc())
            print(f"webview 启动失败：{e}")

    # 回退：默认浏览器打开，服务常驻
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    _log(f"回退浏览器模式，服务常驻 {url}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _fail("未捕获异常：\n" + traceback.format_exc())
