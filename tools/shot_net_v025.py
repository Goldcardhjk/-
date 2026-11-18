# -*- coding: utf-8 -*-
"""v0.25 截图：网络页默认（零弦线）+ 悬停某人（只显示其关系线）

窗口必须置屏内（离屏 Tk 跳过绘制，PrintWindow 会截到空 canvas）
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
import db
db.init_db()
import tkinter as tk
from ui import App

_real_mainloop = tk.Misc.mainloop


def fake_mainloop(self):
    def _job1():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, "_v025_net_default.png"))
            print("shot default saved", flush=True)
            # 悬停一个关系最多的人
            cache = getattr(self, "_links_cache", [])
            deg = {}
            for l in cache:
                for k in ("person_a", "person_b"):
                    deg[l[k]] = deg.get(l[k], 0) + 1
            if deg:
                pid = max(deg, key=deg.get)
                self._hover(pid, True)
                self.update()
                self.update_idletasks()
                self._hover_pid = pid
                print("hover pid=%s deg=%d" % (pid, deg[pid]), flush=True)
            self.after(700, _job2)
        except Exception as e:
            print("SHOT-ERR:", e, flush=True)
            self.destroy()

    def _job2():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, "_v025_net_hover.png"))
            print("shot hover saved", flush=True)
        except Exception as e:
            print("SHOT-ERR2:", e, flush=True)
        self.destroy()

    self._job2 = _job2
    self.after(4500, _job1)
    _real_mainloop(self)


tk.Misc.mainloop = fake_mainloop
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_net)
app.mainloop()
