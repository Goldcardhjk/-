# -*- coding: utf-8 -*-
"""v0.20 流光动画验证：连续采样右栏流光 Canvas 区域，确认相位在推进。

复用 tools/shot.py 的 grab（Win32 抓图，比手搓 struct 可靠）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.chdir(ROOT)

import db
import images_util
db.init_db()
from ui import App
import shot


def region_ink(img, x0, y0, x1, y1):
    """统计区域内亮于 SURFACE 底的像素数（流光亮度代理）"""
    px = img.convert("RGB").load()
    n = 0
    for y in range(max(0, y0), min(img.height, y1)):
        for x in range(max(0, x0), min(img.width, x1)):
            r, g, b = px[x, y]
            if r + g + b > 300:
                n += 1
    return n


def main():
    app = App()
    app.geometry("1600x920+3000+3000")
    app.update()
    app.refresh_network()
    app.update()
    app.after(400)
    app.update()

    states = getattr(app, "_flow_states", [])
    print("flow states:", len(states))
    if not states:
        print("FAIL: 无流光状态注册")
        app.destroy()
        return 1

    cv0 = states[0]["cv"]
    app.update()
    wx, wy = app.winfo_rootx(), app.winfo_rooty()
    cx, cy = cv0.winfo_rootx() - wx, cv0.winfo_rooty() - wy
    cw, ch = cv0.winfo_width(), cv0.winfo_height()
    print("canvas0 at", cx, cy, cw, ch)

    frames = []
    from PIL import Image
    for i in range(4):
        app.after(300)
        app.update()
        shot.grab(app.winfo_id(), "_dbg_flow_%d.png" % i)
        img = Image.open("_dbg_flow_%d.png" % i)
        ink = region_ink(img, cx - 2, cy - 2, cx + cw + 2, cy + ch + 2)
        frames.append((ink, states[0]["phase"]))
    for ink, ph in frames:
        print("frame ink=%d phase=%.3f" % (ink, ph))
    inks = [f[0] for f in frames]
    phases = {round(f[1], 2) for f in frames}
    print("PHASE-CHANGED:", len(phases) > 1)
    print("MAX-INK:", max(inks))
    ok = len(phases) > 1 and max(inks) > 20
    print("RESULT:", "PASS" if ok else "FAIL")
    app.destroy()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
