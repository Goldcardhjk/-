# -*- coding: utf-8 -*-
"""v0.22：插入 ~24 合成人物（复刻用户截图的族群结构）后截网络页"""
import os, sys, shutil, datetime, signal
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
DB = os.path.join("data", "agent.db"); BAK = DB + ".bakshot"
shutil.copy2(DB, BAK)
import db
db.init_db()
conn = db.get_conn()
conn.execute("DELETE FROM persons WHERE name LIKE 'RAD_%'")
# 复刻用户截图结构：张师姐族 / 李博士族 / 王教授族 / 李总族 + 散客
plan = [
    ("张师姐", 4), ("李博士", 4), ("王教授", 5), ("李总", 2),
    (None, 4), (None, 4),
]
i = 0
for ib, cnt in plan:
    for k in range(cnt):
        conn.execute("INSERT INTO persons (name, relation_type, closeness, sentiment_weight, intro_by, address, phone, birthday, important_dates, prefs, taboo, notes, source, avatar, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("RAD_%02d" % i, "朋友", 0.55, 0.5, ib, "地址%d" % i, "138%08d" % i,
             "1990-0%d-1%d" % (i % 9 + 1, i % 9), "[]", "[]", "[]", "", "bench",
             None, datetime.datetime.now().isoformat(timespec="seconds")))
        i += 1
conn.commit(); conn.close()

def cleanup(*_a):
    try:
        c = db.get_conn()
        c.execute("DELETE FROM persons WHERE name LIKE 'RAD_%'")
        c.commit(); c.close()
    except Exception:
        pass
    shutil.copy2(BAK, DB)
    try: os.remove(BAK)
    except Exception: pass
    print("[cleanup done]", flush=True)

signal.signal(signal.SIGTERM, lambda *a: (cleanup(), os._exit(0)))

import tkinter as tk
from ui import App

_real_mainloop = tk.Misc.mainloop

def fake_mainloop(self):
    def _job():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, "_v022_radial.png"))
            print("shot saved", flush=True)
        except Exception as e:
            print("SHOT-ERR:", e, flush=True)
        self.destroy()
        cleanup()
    self.after(1200, _job)
    _real_mainloop(self)

tk.Misc.mainloop = fake_mainloop
app = App()
app.geometry("1600x920+4000+4000")
app.refresh_network()
