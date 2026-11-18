# -*- coding: utf-8 -*-
"""诊断：二维自由布局（relax_free）在真实数据上到底能拿到多少净空"""
import os
import sys
import time
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SRC = os.path.join(ROOT, "dist", "data", "agent.db")
os.makedirs(os.path.join(ROOT, "_tmp"), exist_ok=True)
TMP = tempfile.mkdtemp(prefix="dbgfree_", dir=os.path.join(ROOT, "_tmp"))
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

import math
import tkinter as tk
import netlayout as nl
from ui import App, NODE_W, NODE_H

HW, HH = NODE_W / 2.0, NODE_H / 2.0
_real = tk.Misc.mainloop


def stats(pos, segs, cx, cy, base):
    def gap(a, b, q):
        pa = (cx, cy) if a is None else pos[a]
        pb = (cx, cy) if b is None else pos[b]
        return nl.seg_rect_gap(pa, pb, pos[q], HW, HH)
    worst = min(gap(a, b, q) for a, b in segs for q in pos if q not in (a, b))
    n26 = sum(1 for a, b in segs for q in pos
              if q not in (a, b) and gap(a, b, q) < 26.0)
    n40 = sum(1 for a, b in segs for q in pos
              if q not in (a, b) and gap(a, b, q) < 40.0)
    rad = [math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pos]
    disp = max(math.hypot(pos[p][0] - base[p][0], pos[p][1] - base[p][1])
               for p in pos)
    slot = 2 * math.pi / len(pos)
    ang = max(abs((math.atan2(pos[p][1] - cy, pos[p][0] - cx)
                   - math.atan2(base[p][1] - cy, base[p][0] - cx)) / slot)
              for p in pos)
    return worst, n26, n40, min(rad), max(rad), disp, ang


def run(app, items, links, W, H, cx, cy):
    edges, seen = [], set()
    for l in links:
        k = tuple(sorted((l["person_a"], l["person_b"])))
        if k in seen:
            continue
        seen.add(k)
        edges.append(k)

    def fresh():
        return app._radial_positions(items, {}, W, H, cx, cy, links)

    def segs_of(pos):
        return edges + [(None, p) for p in pos]

    base = fresh()
    print("环 R=%.0f  边=%d  人数=%d" % (
        max(math.hypot(base[p][0] - cx, base[p][1] - cy) for p in base),
        len(edges), len(base)))

    def report(tag, pos, t0, extra=""):
        w, n26, n40, r0, r1, disp, ang = stats(pos, segs_of(pos), cx, cy, base)
        print("%-28s %6.0fms 违规@26=%2d @40=%2d 净空%6.1f R=%3.0f~%3.0f "
              "位移%5.0f 角偏%.2f %s"
              % (tag, (time.monotonic() - t0) * 1000, n26, n40, w, r0, r1,
                 disp, ang, extra))

    p = fresh()
    t0 = time.monotonic()
    left, _ = nl.relax_free(p, segs_of(p), cx, cy, HW, HH, clear=46.0,
                            r_lo=0.46, r_hi=1.58, da_slots=0.60,
                            step_r=22.0, step_a=0.036, sweeps=8,
                            r_min=58.0 + HH + 34.0)
    l2, _ = nl.relax_clear(p, segs_of(p), cx, cy, HW, HH, clear=26.0,
                           r_lo=0.90, r_hi=1.20, sweeps=6)
    report("free46 -> clear26", p, t0, "free剩%d→%d" % (left, l2))

    p = fresh()
    t0 = time.monotonic()
    nl.relax_clear(p, segs_of(p), cx, cy, HW, HH, clear=26.0,
                   r_lo=0.94, r_hi=1.14, sweeps=6)
    left, _ = nl.relax_free(p, segs_of(p), cx, cy, HW, HH, clear=46.0,
                            r_lo=0.46, r_hi=1.58, da_slots=0.60,
                            step_r=22.0, step_a=0.036, sweeps=8,
                            r_min=58.0 + HH + 34.0)
    l2, _ = nl.relax_clear(p, segs_of(p), cx, cy, HW, HH, clear=26.0,
                           r_lo=0.90, r_hi=1.20, sweeps=6)
    report("clear26 -> free46 -> clear26", p, t0, "free剩%d→%d" % (left, l2))


def fake(self):
    def _go():
        try:
            self.fit_view()
            self.refresh_network()
            self.update_idletasks()
        except Exception:
            import traceback
            traceback.print_exc()
        items = self._web_items()
        pids = {it["pid"] for it in items}
        links = [l for l in db.list_links()
                 if l["person_a"] in pids and l["person_b"] in pids]
        c = self.web_canvas
        W, H = c.winfo_width(), c.winfo_height()
        cx, cy = getattr(self, "_web_center", (W / 2.0, H / 2.0))
        try:
            run(self, items, links, W, H, cx, cy)
        except Exception:
            import traceback
            traceback.print_exc()
        self.destroy()

    self.after(1200, _go)
    _real(self)


tk.Misc.mainloop = fake
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_net)
app.mainloop()
shutil.rmtree(TMP, ignore_errors=True)
