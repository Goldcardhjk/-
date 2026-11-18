# -*- coding: utf-8 -*-
"""v0.22：辐射布局截图验收（窗口置于屏内 +100+100；截后清 RAD_ 残留）"""
import os, sys, datetime, signal
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
import db
db.init_db()
conn = db.get_conn()
n = conn.execute("SELECT COUNT(*) FROM persons WHERE name LIKE 'RAD_%'").fetchone()[0]
if n < 20:
    conn.execute("DELETE FROM persons WHERE name LIKE 'RAD_%'")
    plan = [("张师姐", 4), ("李博士", 4), ("王教授", 5), ("李总", 2), (None, 4), (None, 4)]
    i = 0
    for ib, cnt in plan:
        for k in range(cnt):
            conn.execute("INSERT INTO persons (name, relation_type, closeness, sentiment_weight, intro_by, address, phone, birthday, important_dates, prefs, taboo, notes, source, avatar, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("RAD_%02d" % i, "朋友", 0.55, 0.5, ib, "地址%d" % i, "138%08d" % i,
                 "1990-01-01", "[]", "[]", "[]", "", "bench", None,
                 datetime.datetime.now().isoformat(timespec="seconds")))
            i += 1
    conn.commit()
conn.close()
print("[pre] RAD persons:", n, flush=True)

import tkinter as tk
from ui import App
_real_mainloop = tk.Misc.mainloop

def cleanup():
    c = db.get_conn()
    c.execute("DELETE FROM persons WHERE name LIKE 'RAD_%'")
    c.commit(); c.close()
    print("[cleanup done]", flush=True)

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
    self.after(1400, _job)
    _real_mainloop(self)

signal.signal(signal.SIGTERM, lambda *a: (cleanup(), os._exit(0)))
tk.Misc.mainloop = fake_mainloop
app = App()
app.geometry("1600x920+100+100")     # 屏内坐标：Tk 才会真正渲染
app.refresh_network()
app.mainloop()
