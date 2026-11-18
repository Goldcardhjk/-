# -*- coding: utf-8 -*-
"""v0.28 截图验收：极简网络概览 + 无边框圆角确认框

1) 主窗截图 → 裁右侧「网络概览」面板
2) 触发 _ask（自动布局确认框）→ 用屏幕抓取（真实反映圆角/投影透明效果）

窗口必须留在屏幕内：离屏的 Tk 窗口会跳过绘制，PrintWindow 拿到全黑。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import tkinter as tk
import db
db.init_db()

from ui import App
from tools.shot import grab


def main():
    app = App()
    app.geometry("1600x920+60+60")
    app.nb.select(app.tab_net)
    app.update()
    app.refresh_network()
    app.update()

    def _find_dialog():
        for w in app.winfo_children():
            if isinstance(w, tk.Toplevel) and w.winfo_exists():
                return w
        return None

    def _shot_main():
        try:
            grab(app.winfo_id(), "_v028_main.png")
            print("MAIN ok", flush=True)
        except Exception as e:
            print("MAIN-ERR:", e, flush=True)

    def _shot_dialog():
        w = _find_dialog()
        if w is None:
            print("DIALOG not found", flush=True)
            return
        try:
            w.lift()
            try:
                w.attributes("-topmost", True)
            except Exception:
                pass
            w.update()
            app.update()
            # 本机 App 窗口常被其它程序盖住，屏幕抓取抓到的是别人的窗口；
            # 改用 PrintWindow 直接让目标窗口自己绘制
            grab(w.winfo_id(), "_v028_dialog.png")
            # 再来一张真实屏幕合成图：PrintWindow 会把透明区画成黑色，
            # 看不出圆角/投影在深色背景上的实际效果
            app.attributes("-topmost", True)
            app.lift()
            # 弹窗要后设 topmost，否则会被置顶的主窗盖住（截图踩坑）
            w.attributes("-topmost", True)
            w.lift()
            app.update()
            import time
            time.sleep(0.45)
            app.update()
            from PIL import ImageGrab
            full = ImageGrab.grab()
            ratio = full.width / float(app.winfo_screenwidth())
            x, y = w.winfo_rootx(), w.winfo_rooty()
            ww, hh = w.winfo_width(), w.winfo_height()
            full.crop((int(x * ratio) - 12, int(y * ratio) - 12,
                       int((x + ww) * ratio) + 12,
                       int((y + hh) * ratio) + 12)).save(
                "_v028_dialog_real.png")
            print("DIALOG real ok", flush=True)
        except Exception as e:
            print("DIALOG-ERR:", e, flush=True)
        app.after(200, w.destroy)

    def _ask_job():
        app.after(900, _shot_dialog)
        r = app._ask("自动布局", "将清空所有手动固定位置并重新放射排列，继续？")
        print("ask result:", r, flush=True)
        app.after(300, app.destroy)

    app.after(2200, _shot_main)
    app.after(2900, _ask_job)
    app.mainloop()


if __name__ == "__main__":
    main()
