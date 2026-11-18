# -*- coding: utf-8 -*-
"""合成数据验收截图（3 棵引荐树 + 跨树合作边）：全程在临时库里跑，
绝不碰用户的 data/agent.db（之前正是这个工具往真库里插了 TESTV27_ 人物）。
"""
import os
import sys
import shutil
import signal
import tempfile
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.makedirs(os.path.join(ROOT, "_tmp"), exist_ok=True)
TMP = tempfile.mkdtemp(prefix="shotsynth_", dir=os.path.join(ROOT, "_tmp"))
DATA = os.path.join(TMP, "data")
os.makedirs(DATA, exist_ok=True)
shutil.copy2(os.path.join(ROOT, "dist", "data", "agent.db"),
             os.path.join(DATA, "agent.db"))

import db
db.BASE = TMP
db.DATA_DIR = DATA
db.DB_PATH = os.path.join(DATA, "agent.db")
db.init_db()
conn = db.get_conn()
# 临时库清空成白板：只放这次要拍的合成数据（否则会混进真实人物）
conn.execute("DELETE FROM links")
conn.execute("DELETE FROM persons")
conn.commit()
n = 0


def cleanup():
    c = db.get_conn()
    ids = [r[0] for r in c.execute(
        "SELECT id FROM persons WHERE name LIKE 'TESTV27_%'").fetchall()]
    if ids:
        q = ",".join("?" * len(ids))
        c.execute("DELETE FROM links WHERE person_a IN (%s) OR person_b IN (%s)"
                  % (q, q), ids + ids)
        c.execute("DELETE FROM persons WHERE id IN (%s)" % q, ids)
        c.commit()
    c.close()
    print("[cleanup done]", flush=True)


if n < 13:
    cleanup()
    now = datetime.datetime.now().isoformat(timespec="seconds")

    def add(nm, intro, score_closeness=0.6):
        cur = conn.execute(
            "INSERT INTO persons (name, relation_type, closeness, "
            "sentiment_weight, intro_by, address, phone, birthday, "
            "important_dates, prefs, taboo, notes, source, avatar, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nm, "朋友", score_closeness, 0.5, intro, "地址", "13800000000",
             "1990-01-01", "[]", "[]", "[]", "", "bench", None, now))
        return cur.lastrowid

    # 3 棵引荐树：根（无引荐人）+ 若干被引荐人；另 1 位独立
    ids = {}
    ids["根A"] = add("TESTV27_张师姐", "")
    ids["根B"] = add("TESTV27_李博士", "")
    ids["根C"] = add("TESTV27_王教授", "")
    ids["独立"] = add("TESTV27_陈工", "")
    for i, nm in enumerate(["甲", "乙", "丙", "丁"]):
        ids["A%d" % i] = add("TESTV27_A%d" % i, "TESTV27_张师姐")
    for i, nm in enumerate(["戊", "己", "庚"]):
        ids["B%d" % i] = add("TESTV27_B%d" % i, "TESTV27_李博士")
    for i, nm in enumerate(["辛", "壬"]):
        ids["C%d" % i] = add("TESTV27_C%d" % i, "TESTV27_王教授")
    conn.commit()

    edges = []
    for k in ("A0", "A1", "A2", "A3"):
        edges.append((ids["根A"], ids[k], "引荐", 72))
    for k in ("B0", "B1", "B2"):
        edges.append((ids["根B"], ids[k], "引荐", 66))
    for k in ("C0", "C1"):
        edges.append((ids["根C"], ids[k], "引荐", 60))
    # 少量跨树「合作」长边
    edges.append((ids["A3"], ids["B0"], "合作", 55))
    edges.append((ids["A1"], ids["C0"], "同事", 48))
    edges.append((ids["独立"], ids["B2"], "朋友", 52))
    for a, b, rel, sc in edges:
        conn.execute("INSERT INTO links (person_a, person_b, relation, score, "
                     "created_at) VALUES (?,?,?,?,?)", (a, b, rel, sc, now))
    conn.commit()
conn.close()
print("[pre] TESTV27 ready", flush=True)

import tkinter as tk
from ui import App

_real = tk.Misc.mainloop
signal.signal(signal.SIGTERM, lambda *a: (cleanup(), os._exit(0)))


def fake(self):
    def _fit():
        self.fit_view()
        self.refresh_network()
        self.update_idletasks()

    def _job1():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, "_v030_synth.png"))
            print("shot default saved", flush=True)
            pid = next(iter(self.node_pos), None)
            if pid:
                self._hover(pid, True)
                self.update()
                self.update_idletasks()
                self.after(600, _job2)
            else:
                self.destroy()
        except Exception as e:
            print("SHOT-ERR:", e, flush=True)
            self.destroy()

    def _job2():
        try:
            import shot
            shot.grab(self.winfo_id(),
                      os.path.join(ROOT, "_v030_synth_hover.png"))
            print("shot hover saved", flush=True)
        except Exception as e:
            print("SHOT-ERR2:", e, flush=True)
        print("[metrics]", getattr(self, "_net_avoid", None), flush=True)
        self.destroy()
        cleanup()

    self.after(4200, _fit)
    self.after(7500, _job1)
    _real(self)


tk.Misc.mainloop = fake
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_net)
app.mainloop()
