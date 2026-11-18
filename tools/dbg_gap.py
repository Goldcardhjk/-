# -*- coding: utf-8 -*-
"""诊断：真实数据自动布局后，哪些「线-卡」净空不足（打印人名与数值）"""
import os
import sys
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SRC = os.path.join(ROOT, "dist", "data", "agent.db")
os.makedirs(os.path.join(ROOT, "_tmp"), exist_ok=True)
TMP = tempfile.mkdtemp(prefix="dbggap_", dir=os.path.join(ROOT, "_tmp"))
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
import math
import netlayout as nl
from ui import App, NODE_W, NODE_H

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
        name = {it["pid"]: it["name"] for it in self._web_items()}
        bx = getattr(self, "_web_center", (0.0, 0.0))
        cx, cy = bx
        c = self.web_canvas
        scale = getattr(c, "scale", 1.0)
        print("canvas %dx%d cx=%.0f cy=%.0f" % (c.winfo_width(),
                                                c.winfo_height(), cx, cy))
        links = []
        seen = set()
        for l in (self._links_cache or []):
            k = tuple(sorted((l["person_a"], l["person_b"])))
            if k in seen:
                continue
            seen.add(k)
            links.append(k)
        segs = links + [(None, p) for p in pos]
        badges = []
        for a, b in segs:
            pa = (cx, cy) if a is None else pos[a]
            pb = (cx, cy) if b is None else pos[b]
            for q in pos:
                if q == a or q == b:
                    continue
                g = nl.seg_rect_gap(pa, pb, pos[q], NODE_W / 2, NODE_H / 2)
                if g < 26.0:
                    badges.append((round(g, 1),
                                   name.get(a, "我" if a is None else "?"),
                                   name.get(b, "我" if b is None else "?"),
                                   name.get(q, "?")))
        badges.sort()
        print("edges=%d 违规=%d" % (len(links), len(badges)))
        for b in badges:
            print("   gap=%6.1f  %s-%s  压 %s" % b)
        rad = [math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pos]
        print("半径 %.0f~%.0f" % (min(rad), max(rad)))
        print("avoid=", getattr(self, "_net_avoid", None))
        self.destroy()

    self.after(4200, _go)
    _real(self)


tk.Misc.mainloop = fake
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_net)
app.mainloop()
shutil.rmtree(TMP, ignore_errors=True)
