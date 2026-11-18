# -*- coding: utf-8 -*-
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
import db
db.init_db()
import tkinter as tk
from ui import App
_real = tk.Misc.mainloop

def fake(self):
    def _job():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, "_v025_cards.png"))
            print("shot saved", flush=True)
        except Exception as e:
            print("ERR", e, flush=True)
        self.destroy()
    self.after(4200, _job)
    _real(self)

tk.Misc.mainloop = fake
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_cards)
app.mainloop()
