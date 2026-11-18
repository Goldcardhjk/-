# -*- coding: utf-8 -*-
"""A/B 截图：同一份真实数据、同一套环序，只切换「卡位分配策略」。

  even = v0.31 均分卡位（缝只有 26px）
  flow = v0.32 按缝流量分配（线多的缝 93px、线少的 38px）

用法：python tools/shot_net_ab.py even|flow
输出：_ab_even.png / _ab_flow.png
"""
import os
import sys
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

MODE = (sys.argv[1] if len(sys.argv) > 1 else "flow").lower()

SRC = os.path.join(ROOT, "dist", "data", "agent.db")
os.makedirs(os.path.join(ROOT, "_tmp"), exist_ok=True)
TMP = tempfile.mkdtemp(prefix="shotab_", dir=os.path.join(ROOT, "_tmp"))
DATA = os.path.join(TMP, "data")
os.makedirs(DATA, exist_ok=True)
shutil.copy2(SRC, os.path.join(DATA, "agent.db"))

import db
db.BASE = TMP
db.DATA_DIR = DATA
db.DB_PATH = os.path.join(DATA, "agent.db")
db.init_db()
conn = db.get_conn()
conn.execute("UPDATE persons SET pos_x=NULL, pos_y=NULL")
conn.commit()
conn.close()

import netlayout

if MODE == "even":
    _orig = netlayout.place_uneven

    def _even(order, loads, hw, cx=0.0, cy=0.0, **_kw):
        # 全部缝等宽 26px → 自洽解出来正好是 v0.31 的均分卡位（R = 卡位弧 180）
        return _orig(order, [0] * len(order), hw, cx, cy,
                     base_gap=26.0, per_line=0.0, min_gap=26.0)
    netlayout.place_uneven = _even
    print("[mode] even（均分卡位 26px）", flush=True)
else:
    print("[mode] flow（按缝流量分配）", flush=True)

import tkinter as tk
from ui import App

OUT = "_ab_even" if MODE == "even" else "_ab_flow"
_real = tk.Misc.mainloop


def fake(self):
    def _fit():
        self.fit_view()
        self.refresh_network()
        self.update_idletasks()

    def _job():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, OUT + ".png"))
            print("shot saved", OUT, flush=True)
        except Exception as e:
            print("SHOT-ERR:", e, flush=True)
        print("[metrics] avoid =", getattr(self, "_net_avoid", None),
              "ring_r =", round(getattr(self, "_ring_r", 0), 1), flush=True)
        # 顺带打印缝宽/流量，便于核对
        gaps = getattr(self, "_slot_gaps", None)
        loads = getattr(self, "_slot_loads", None)
        if gaps and loads:
            rr = getattr(self, "_ring_r", 0)
            print("[gaps] " + " ".join(
                "%d:%dpx" % (k, g * rr) for k, g in
                sorted(zip(loads, gaps), key=lambda t: -t[0])[:6]), flush=True)
        self.destroy()

    self.after(4200, _fit)
    self.after(7500, _job)
    _real(self)


tk.Misc.mainloop = fake
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_net)
app.mainloop()
try:
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass
