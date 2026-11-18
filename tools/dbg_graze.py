# -*- coding: utf-8 -*-
"""诊断4：扫描环序擦卡权重 graze_w 对最终几何的影响
用法：G=8 python tools/dbg_graze.py
"""
import os
import sys
import shutil
import tempfile
import math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

G = float(os.environ.get("G", "30"))
LO = float(os.environ.get("LO", "0.94"))
HI = float(os.environ.get("HI", "1.14"))

SRC = os.path.join(ROOT, "dist", "data", "agent.db")
os.makedirs(os.path.join(ROOT, "_tmp"), exist_ok=True)
TMP = tempfile.mkdtemp(prefix="dbggz_", dir=os.path.join(ROOT, "_tmp"))
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

import tkinter as tk
import netlayout as nl
import ui as ui_mod

_real_oc = nl.order_cycle


def oc(*a, **k):
    k["graze_w"] = G
    return _real_oc(*a, **k)


nl.order_cycle = oc
ui_mod.netlayout.order_cycle = oc
ui_mod.App.CH_R_LO = LO
ui_mod.App.CH_R_HI = HI

_real = tk.Misc.mainloop


def fake(self):
    def _go():
        try:
            self.fit_view()
            self.refresh_network()
            self.update_idletasks()
        except Exception as e:
            print("LAYOUT-ERR:", e)
        pos = self.node_pos
        cx, cy = self._web_center
        hw, hh = ui_mod.NODE_W / 2.0, ui_mod.NODE_H / 2.0
        seen, E = set(), []
        for l in (self._links_cache or []):
            kk = tuple(sorted((l["person_a"], l["person_b"])))
            if kk in seen:
                continue
            seen.add(kk)
            E.append((l["person_a"], l["person_b"]))
        segs = E + [(None, p) for p in pos]
        bad, worst = 0, 1e9
        for a, b in segs:
            pa = (cx, cy) if a is None else pos[a]
            pb = (cx, cy) if b is None else pos[b]
            for q in pos:
                if q == a or q == b:
                    continue
                g = nl.seg_rect_gap(pa, pb, pos[q], hw, hh)
                worst = min(worst, g)
                if g < 26.0:
                    bad += 1
        seq = sorted(pos, key=lambda p: math.atan2(pos[p][1] - cy, pos[p][0] - cx))
        idx = {p: i for i, p in enumerate(seq)}
        hist = {}
        for a, b in E:
            k2 = nl.ring_span(idx, a, b, len(seq))
            hist[k2] = hist.get(k2, 0) + 1
        rr = [math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in seq]
        jump = max(abs(rr[i] - rr[(i + 1) % len(rr)]) for i in range(len(rr)))
        print("G=%-4.0f cap %.2f~%.2f | 违规%d 净空%5.1f 跨距%s 半径%3.0f~%3.0f "
              "邻跳%3.0f avoid=%s"
              % (G, LO, HI, bad, worst, dict(sorted(hist.items())),
                 min(rr), max(rr), jump, getattr(self, "_net_avoid", None)))
        self.destroy()

    self.after(4200, _go)
    _real(self)


tk.Misc.mainloop = fake
app = ui_mod.App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_net)
app.mainloop()
shutil.rmtree(TMP, ignore_errors=True)
