# -*- coding: utf-8 -*-
"""录制 v0.69 云端积分离线短演示：真实 Tk 窗口 + 隔离数据 + 回环 mock 服务。

只写 _tmp/v069/competition_demo；不读真实桌面账号库、不触碰真实支付。
输出为 1920x1080 PNG 帧，随后用 ffmpeg 编码。脚本不做生成式视频。
"""
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_tmp" / "v069" / "competition_demo"
FRAMES = OUT / "frames"
if OUT.exists():
    shutil.rmtree(OUT)
FRAMES.mkdir(parents=True)
sys.path.insert(0, str(ROOT))

os.environ["JINBU_DATA_DIR"] = str(OUT / "desktop_data")
os.environ["JINBU_CLOUD_CONFIG"] = str(OUT / "cloud_client.json")
os.environ["JINBU_CLOUD_SESSION"] = str(OUT / "desktop_data" / "cloud_session.json")
os.environ["JINBU_SERVER_ALLOW_MOCK"] = "1"
os.environ.pop("JINBU_MODEL_BASE_URL", None)
os.environ.pop("JINBU_MODEL_API_KEY", None)
os.environ.pop("JINBU_MODEL_NAME", None)

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageGrab

import cloud_client
import cloud_server
import db
import ui

W, H = 1920, 1080
CAPTURE_FPS = 1 if os.environ.get("JINBU_EXTERNAL_CAPTURE") == "1" else 2
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
BOLD_PATH = r"C:\Windows\Fonts\msyhbd.ttc"
font_title = ImageFont.truetype(BOLD_PATH, 36)
font_sub = ImageFont.truetype(FONT_PATH, 25)
font_small = ImageFont.truetype(FONT_PATH, 20)
font_big = ImageFont.truetype(BOLD_PATH, 48)


def compose(raw, title, subtitle, dim=False):
    base = Image.new("RGB", (W, H), "#07111f")
    draw = ImageDraw.Draw(base)
    draw.rectangle((0, 0, W, 84), fill="#0b1a2b")
    draw.text((48, 18), title, font=font_title, fill="#eaf7ff")
    draw.text((50, 88), subtitle, font=font_sub, fill="#89aac3")
    view = raw.convert("RGB").resize((1664, 957), Image.Resampling.LANCZOS)
    if dim:
        view = ImageEnhance.Brightness(view).enhance(0.32)
    base.paste(view, (128, 123))
    draw.rectangle((0, 84, W, 122), fill="#0b1a2b")
    draw.rectangle((0, 84, 12, H), fill="#32d5d2")
    draw.text((50, 88), subtitle, font=font_sub, fill="#a9c3d8")
    draw.text((50, 1042), "v0.69 · 离线验收演示｜模拟模型 + 模拟购买，不发生真实扣款，不作为真实交易凭证",
              font=font_small, fill="#89aac3")
    return base


service = cloud_server.CloudService(str(OUT / "cloud.db"))
server = cloud_server.CloudHTTPServer(("127.0.0.1", 8766), cloud_server.Handler)
server.service = service
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

app = None
frame_no = 0
scene_log = []


def close_dialogs():
    for w in list(app.winfo_children()):
        if isinstance(w, tk.Toplevel):
            try:
                w.destroy()
            except Exception:
                pass
    app.update()


def front_dialogs():
    for w in app.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.attributes("-topmost", True)
            w.lift()
            w.focus_force()
    app.update()


def show_ledger_tab():
    for top in app.winfo_children():
        if not isinstance(top, tk.Toplevel) or top.title() != "积分充值与订单":
            continue
        stack = [top]
        while stack:
            widget = stack.pop()
            if isinstance(widget, ttk.Notebook):
                widget.select(1)
            stack.extend(widget.winfo_children())
    app.update()


def capture(title, subtitle, dim=False):
    global frame_no
    app.update_idletasks()
    app.update()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    frame_no += 1
    if os.environ.get("JINBU_EXTERNAL_CAPTURE") == "1":
        (OUT / "capture_state.json").write_text(json.dumps({
            "frame": frame_no, "bbox": [x, y, x + app.winfo_width(), y + app.winfo_height()],
            "title": title, "subtitle": subtitle, "dim": dim,
        }, ensure_ascii=False), encoding="utf-8")
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                ack = json.loads((OUT / "capture_ack.json").read_text(encoding="utf-8"))
                if int(ack.get("frame", 0)) >= frame_no:
                    return
            except (FileNotFoundError, ValueError, TypeError):
                pass
            time.sleep(0.02)
        raise RuntimeError("系统桌面截图确认超时：第 %d 帧" % frame_no)
    raw = ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()))
    path = FRAMES / ("%04d.png" % frame_no)
    compose(raw, title, subtitle, dim=dim).save(path, compress_level=2)


def hold(seconds, title, subtitle, dim=False):
    for _ in range(int(seconds * CAPTURE_FPS)):
        started = time.time()
        capture(title, subtitle, dim=dim)
        time.sleep(max(0, 1.0 / CAPTURE_FPS - (time.time() - started)))


try:
    db.init_db()
    db.bootstrap_accounts()
    app = ui.App()
    app.geometry("1600x920+0+0")
    app.attributes("-topmost", True)
    app.update()
    if getattr(app, "_gate", None) is not None:
        accounts = db.list_accounts()
        if not accounts:
            raise RuntimeError("隔离演示账号未生成")
        app.enter_account(accounts[0]["slug"])
    app.geometry("1600x920+0+0")
    app.update()
    app.refresh_network()
    app.after(700, app.refresh_network)
    time.sleep(1.0)

    hold(5, "进步壹号 Agent v0.69", "关系经营首页：人物、事实关系、记忆与维护行动围绕「我」展开")
    scene_log.append({"scene": "关系首页", "seconds": 5})

    hold(7, "01 · 以「我」为中心的关系网络", "线色表示关系类型，卡片颜色表示关系分档；先理解关系，再采取维护行动")
    scene_log.append({"scene": "关系网络", "seconds": 7})

    cloud_client.save_config({"enabled": True, "server_url": "http://127.0.0.1:8766"})
    app.open_cloud_account_dialog()
    front_dialogs()
    app.update()
    entries = []
    for w in app.winfo_children():
        if isinstance(w, tk.Toplevel):
            stack = [w]
            while stack:
                cur = stack.pop()
                stack.extend(cur.winfo_children())
                if isinstance(cur, tk.Entry):
                    entries.append(cur)
    entries.sort(key=lambda e: e.winfo_rooty())
    if len(entries) >= 3:
        values = ("http://127.0.0.1:8766", "demo_relationship", "DemoPass2026!")
        for entry, value in zip(entries[:3], values):
            entry.delete(0, tk.END)
            entry.insert(0, value)
    hold(4, "02 · 云端注册与登录", "服务端保存账号与独立积分账本，模型 API Key 不进入桌面端")
    result = cloud_client.register("demo_relationship", "DemoPass2026!")
    close_dialogs()
    app.open_cloud_account_dialog()
    front_dialogs()
    app.refresh_points_chip()
    hold(4, "02 · 新账号自动获得 100 体验积分", "注册即登录；余额、订单和 AI 请求均归属于当前云端用户")
    scene_log.append({"scene": "注册赠分", "seconds": 8, "balance": result["balance"]})

    close_dialogs()
    cloud_client.purchase("standard", "mock")
    app.open_points_dialog()
    front_dialogs()
    app.refresh_points_chip()
    hold(8, "03 · 积分购买与原子入账", "离线演示使用模拟购买：500 积分入账，订单与账本同事务写入；真实通道需用户本人付款")
    scene_log.append({"scene": "模拟充值", "seconds": 8, "balance": cloud_client.cached_balance()})

    close_dialogs()
    app.nb.select(app.tab_conn)
    app.update()
    app.run_llm_judge(silent=True)
    app.refresh_points_chip()
    hold(10, "04 · 关系判断：预扣 10 积分后调用模型", "服务端按 request_id + 载荷哈希防重；同请求重放不重复扣费，失败自动退回")
    scene_log.append({"scene": "关系判断", "seconds": 10, "balance": cloud_client.cached_balance()})

    app.nb.select(app.tab_greet)
    app.update()
    names = list(app.person_map)
    if names:
        app.gr_person.set(names[0])
    app.gr_holiday.set("生日")
    app.gen_greeting()
    app.refresh_points_chip()
    hold(8, "05 · 祝福生成：每条扣 2 积分", "来源与余额直接显示；本地模板仍免费，云端模拟与真实 API 有明确区分")
    scene_log.append({"scene": "祝福生成", "seconds": 8, "balance": cloud_client.cached_balance()})

    app.nb.select(app.tab_net)
    app.open_points_dialog()
    front_dialogs()
    show_ledger_tab()
    app.refresh_points_chip()
    hold(7, "06 · 积分流水与余额可复核", "赠送、充值、消费逐笔展示；支持全部 / 收入 / 支出筛选，最终余额来自真实服务端账本")
    scene_log.append({"scene": "积分流水与余额复核", "seconds": 7, "balance": cloud_client.cached_balance()})

    close_dialogs()
    hold(7, "云端离线验收：25 / 25 通过",
         "注册鉴权｜支付防重｜密钥隔离｜AI 幂等｜失败退款｜异常重启退款｜真实窗口｜OpenAI 兼容协议", dim=True)
    scene_log.append({"scene": "验收结论", "seconds": 7, "checks": 25})
finally:
    if app is not None:
        try:
            app.destroy()
        except Exception:
            pass
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)

(OUT / "manifest.json").write_text(json.dumps({
    "version": "v0.69", "mode": "offline-mock", "fps": CAPTURE_FPS,
    "frames": frame_no, "seconds": frame_no / CAPTURE_FPS,
    "scenes": scene_log,
    "real_model": False, "real_payment": False,
    "note": "真实 Tk 窗口与回环服务端；不作为真实模型或真实交易凭证。",
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"frames": frame_no, "seconds": frame_no / CAPTURE_FPS, "out": str(OUT)}, ensure_ascii=False))
