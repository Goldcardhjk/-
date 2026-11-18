# -*- coding: utf-8 -*-
"""补拍弹窗类配图：资料卡 / 记录联系 / 积分窗 / 大模型判断结果。

与 shoot_manual.py 分开跑的原因：这些窗口都是 wait_window 模态，
主脚本里那套 after 看门狗容易和 d.pump 的事件泵抢节奏（拍到空白），
这里改成「只开一个窗 → 直接截 Toplevel 自身句柄 → 关掉」，一次一个。
"""
import os
import sys

ROOT = r"D:/进步壹号/进步壹号Agent"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "shots")
DEMO = os.path.join(ROOT, "进步壹号发布视频", "_v069", "demo_data")

os.environ["JINBU_DATA_DIR"] = DEMO
os.environ["JINBU_SKIP_SEED"] = "1"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import tkinter as tk                      # noqa: E402
import time                               # noqa: E402
import db                                 # noqa: E402
from shot import grab                     # noqa: E402

db.init_db()
db.bootstrap_accounts()
for a in db.list_accounts():
    if a["name"] == "工作账号":
        db.switch_account(a["slug"])
        break
if not db.get_user():
    db.save_user("工作账号")

import ui                                 # noqa: E402

ui.STARTUP_POPUP = False
os.makedirs(OUT, exist_ok=True)

TARGET = sys.argv[1]
NAME = sys.argv[2]
SCROLL = "--scroll" in sys.argv


def _scroll_deep(w):
    """把窗口里所有带 yscrollcommand 的 Canvas 滚到底。"""
    try:
        if w.winfo_class() == "Canvas" and w.cget("yscrollcommand"):
            w.yview_moveto(1.0)
    except Exception:
        pass
    for ch in w.winfo_children():
        _scroll_deep(ch)

app = ui.App()
app.geometry("1600x900+0+0")
app.update()
# 启动先亮账号入口层（ui.py:218 _need_account_gate），这时 web_canvas 之类
# 的首页控件还不存在；必须先原地进账号，主界面才建起来。
if getattr(app, "nb", None) is None:
    app.enter_account(db.current_account(), "工作账号")
    app.update()
app.refresh_network()
app.update()

pid = None
for p in db.list_persons():
    if "王教授" in (p.get("name") or ""):
        pid = p["id"]
        break
if pid is None and db.list_persons():
    pid = db.list_persons()[0]["id"]


def _top():
    for w in app.winfo_children():
        if isinstance(w, tk.Toplevel) and w.winfo_ismapped():
            return w
    return None


def _shot():
    w = _top()
    if w is None:
        app.after(250, _shot)
        return
    try:
        if SCROLL:
            # 资料卡的「社交价值拆解」「互动记录」在可滚动表单下半段
            # （dialogs.py:731 起的 sc Canvas）。这里不滚动——body 的
            # <Configure> 回调用 Frame.bbox("all")（dialogs.py:736），对
            # Frame 来说那是 grid_bbox，scrollregion 基本算不准，
            # yview_moveto(1.0) 等于空操作（实测两次都没动）。
            # 改成把窗口拉高，让表单整段自然铺开再截。
            w.geometry("520x1500+0+0")
            for _ in range(3):
                w.update_idletasks()
                w.update()
                time.sleep(0.2)
        w.update_idletasks()
        size = grab(w.winfo_id(), os.path.join(OUT, NAME + ".png"))
        print("SHOT", NAME, size, flush=True)
    except Exception as ex:                # noqa: BLE001
        print("SHOT-ERR", NAME, repr(ex), flush=True)
    DONE["ok"] = True
    app.after(150, w.destroy)


DONE = {"ok": False}
app.after(1200, _shot)
if TARGET == "profile":
    app.open_profile(pid)
elif TARGET == "contact":
    app.record_contact(pid)
elif TARGET == "points":
    app.open_points_dialog()
elif TARGET == "judge":
    app.run_llm_judge(False)
elif TARGET == "notes":
    app.open_notes_import()
elif TARGET == "llmcfg":
    app.open_llm_config()

# open_profile / open_points_dialog 这类窗口不走 wait_window（dialogs.py
# 只有 _info/_ask 系列阻塞），函数会立刻返回。所以不能直接 destroy，
# 要自己泵事件循环，等 after 截图回调真的跑完。


deadline = time.time() + 25
while not DONE["ok"] and time.time() < deadline:
    try:
        app.update()
    except tk.TclError:
        break
    time.sleep(0.02)
try:
    app.update()
    app.destroy()
except Exception:
    pass
print("DONE", NAME, DONE["ok"], flush=True)
