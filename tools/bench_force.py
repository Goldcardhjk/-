# -*- coding: utf-8 -*-
"""v0.21：测 _force_positions 迭代收敛情况（早停是否生效）"""
import os
import sys
import time
import shutil
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

DB = os.path.join("data", "agent.db")
BAK = os.path.join("data", "_bench_bak.db")
shutil.copy2(DB, BAK)

import db

db.init_db()
import math


def bench_force(n):
    """直接构造合成 items 测 _force_positions"""
    from ui import App
    import ui as uimod
    app = App()
    app.geometry("1600x920+4000+4000")
    app.update()

    items = [{"pid": 1000 + i, "name": "N%d" % i, "score": 90 - i % 60,
              "tier": "重要", "avatar": None, "intro_by": None,
              "pos_x": None, "pos_y": None, "_p": {}} for i in range(n)]
    links = [{"person_a": 1000 + i, "person_b": 1000 + ((i * 7) % n),
              "score": 50 + i % 40} for i in range(n // 2)]
    ring_pos = {it["pid"]: (800 + 300 * math.cos(i * 0.7),
                            460 + 250 * math.sin(i * 0.7))
                for i, it in enumerate(items)}
    W, H, cx, cy = 1560, 800, 780, 400
    t0 = time.perf_counter()
    pos = app._force_positions(items, links, {}, ring_pos, W, H, cx, cy)
    dt = (time.perf_counter() - t0) * 1000
    # 位置分散度（布局质量粗检）
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    spread = (max(xs) - min(xs)) * (max(ys) - min(ys))
    print("[force] n=%3d  %8.1f ms  bbox=%dx%d"
          % (n, dt, max(xs) - min(xs), max(ys) - min(ys)), flush=True)
    app.destroy()


for n in (50, 100, 200, 300):
    bench_force(n)

shutil.copy2(BAK, DB)
os.remove(BAK)
print("done", flush=True)
