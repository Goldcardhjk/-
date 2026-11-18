# -*- coding: utf-8 -*-
"""真实数据验收截图：把 dist/data/agent.db 复制到临时目录，清掉手动位置，
跑自动布局后截图——不动用户真实库。

用法：python tools/shot_net_real.py [输出前缀]
"""
import os
import sys
import shutil
import signal
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SRC = os.path.join(ROOT, "dist", "data", "agent.db")
os.makedirs(os.path.join(ROOT, "_tmp"), exist_ok=True)
TMP = tempfile.mkdtemp(prefix="shotreal_", dir=os.path.join(ROOT, "_tmp"))
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
print("[prep] 临时库", db.DB_PATH,
      "persons", conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0], flush=True)
conn.close()

import tkinter as tk
from ui import App

OUT = sys.argv[1] if len(sys.argv) > 1 else "_v030_real"

_real = tk.Misc.mainloop


def fake(self):
    def _fit():
        self.fit_view()
        self.refresh_network()
        self.update_idletasks()

    def _job1():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, OUT + ".png"))
            print("shot saved", flush=True)
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
            shot.grab(self.winfo_id(), os.path.join(ROOT, OUT + "_hover.png"))
            print("hover saved", flush=True)
        except Exception as e:
            print("SHOT-ERR2:", e, flush=True)
        print("[metrics] avoid =", getattr(self, "_net_avoid", None), flush=True)
        self.destroy()

    self.after(4200, _fit)
    self.after(7500, _job1)
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
