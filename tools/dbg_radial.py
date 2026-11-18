# -*- coding: utf-8 -*-
"""调试 radial 截图空白：打印画布尺寸/node_pos/scrollregion/item 数"""
import os, sys, datetime
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
print("[pre] persons:", n, flush=True)

import tkinter as tk
from ui import App
_real_mainloop = tk.Misc.mainloop

def dump(tag):
    c = app.web_canvas
    np = getattr(app, "node_pos", {})
    xs = [p[0] for p in np.values()]; ys = [p[1] for p in np.values()]
    print("[%s] canvas=%dx%d zoom=%.2f nodes=%d items=%d sr=%s xview=%s"
          % (tag, c.winfo_width(), c.winfo_height(), app._zoom, len(np),
             len(c.find_all()), c.cget("scrollregion"),
             c.xview()), flush=True)
    if np:
        print("   pos range x=[%.0f,%.0f] y=[%.0f,%.0f] center=%s"
              % (min(xs), max(xs), min(ys), max(ys), app._web_center), flush=True)

def fake_mainloop(self):
    def _job():
        dump("after1200")
        self.after(800, self._job2)
    def _job2(self):
        dump("after2000")
        self.destroy()
    self._job2 = lambda: _job2(self)
    self.after(1200, _job)
    _real_mainloop(self)

tk.Misc.mainloop = fake_mainloop
app = App()
app.geometry("1600x920+4000+4000")
app.update_idletasks()
dump("init+idletasks")
app.refresh_network()
dump("after-refresh")
app.mainloop()

c = db.get_conn()
c.execute("DELETE FROM persons WHERE name LIKE 'RAD_%'")
c.commit(); c.close()
print("[cleanup done]", flush=True)
