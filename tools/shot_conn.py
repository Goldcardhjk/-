# -*- coding: utf-8 -*-
"""截图：绿泡泡连接器页 v0.23（屏内坐标；全部视图 + 家人筛选小内容视图）"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
import db
db.init_db()
import tkinter as tk
from ui import App
_real_mainloop = tk.Misc.mainloop

def fake_mainloop(self):
    def _job():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, "_v022_conn.png"))
            print("shot all saved", flush=True)
            # 切「家人」筛选（4 人，内容不足一屏 → 走宽度微缩兜底路径）
            self._filter_conn("家人")
            self.update()
            self.update_idletasks()
            self.after(600, _job2)
        except Exception as e:
            print("SHOT-ERR:", e, flush=True)
    def _job2():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, "_v022_conn_family.png"))
            print("shot family saved", flush=True)
        except Exception as e:
            print("SHOT-ERR2:", e, flush=True)
        self.destroy()
    self._job2 = _job2
    self.after(1600, _job)
    _real_mainloop(self)

tk.Misc.mainloop = fake_mainloop
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_conn)
app.mainloop()
