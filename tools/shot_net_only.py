# -*- coding: utf-8 -*-
"""v0.22：直接截当前库的网络页（不插数据；截图后清 RAD_ 残留）"""
import os, sys, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
import db
db.init_db()
conn = db.get_conn()
n = conn.execute("SELECT COUNT(*) FROM persons WHERE name LIKE 'RAD_%'").fetchone()[0]
conn.close()
print("[pre] RAD persons in db:", n, flush=True)

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
    self.after(1200, _job)
    _real_mainloop(self)

tk.Misc.mainloop = fake_mainloop
app = App()
app.geometry("1600x920+4000+4000")
app.refresh_network()
app.mainloop()
