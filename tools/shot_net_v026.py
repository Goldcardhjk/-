# -*- coding: utf-8 -*-
"""v0.26 截图验收：带关系边的网络（同族短边 + 跨族长边 + 多车道）

造 TESTV26_ 前缀人物与关系边，截图后清理。
窗口必须屏内（离屏 Tk 跳过绘制）。
"""
import os
import sys
import datetime
import signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import db

db.init_db()
conn = db.get_conn()
n = conn.execute("SELECT COUNT(*) FROM persons WHERE name LIKE 'TESTV26_%'").fetchone()[0]


def cleanup():
    c = db.get_conn()
    ids = [r[0] for r in c.execute(
        "SELECT id FROM persons WHERE name LIKE 'TESTV26_%'").fetchall()]
    if ids:
        q = ",".join("?" * len(ids))
        c.execute("DELETE FROM links WHERE person_a IN (%s) OR person_b IN (%s)"
                  % (q, q), ids + ids)
        c.execute("DELETE FROM persons WHERE id IN (%s)" % q, ids)
        c.commit()
    c.close()
    print("[cleanup done]", flush=True)


if n < 12:
    cleanup()
    plan = [("张师姐", 4), ("李博士", 4), ("王教授", 3)]
    name2id = {}
    i = 0
    for ib, cnt in plan:
        for k in range(cnt):
            nm = "TESTV26_%02d" % i
            cur = conn.execute(
                "INSERT INTO persons (name, relation_type, closeness, "
                "sentiment_weight, intro_by, address, phone, birthday, "
                "important_dates, prefs, taboo, notes, source, avatar, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (nm, "朋友", 0.55, 0.5, ib, "地址%d" % i, "138%08d" % i,
                 "1990-01-01", "[]", "[]", "[]", "", "bench", None,
                 datetime.datetime.now().isoformat(timespec="seconds")))
            name2id.setdefault(ib, []).append(cur.lastrowid)
            i += 1
    conn.commit()
    # 关系边：族内相邻（短边）+ 族内非相邻 + 跨族（长边）+ 多车道叠加
    fams = [name2id[k] for k in ("张师姐", "李博士", "王教授") if k in name2id]
    edges = []
    for g in fams:                      # 族内链式相邻
        for a, b in zip(g, g[1:]):
            edges.append((a, b, "朋友", 70))
    if len(fams) >= 2:                  # 族内首尾（跨过整族）
        edges.append((fams[0][0], fams[0][-1], "同事", 55))
        # 跨族：短跨、长跨都有
        edges.append((fams[0][-1], fams[1][0], "引荐", 60))
        edges.append((fams[0][0], fams[1][-1], "合作", 45))
    if len(fams) >= 3:
        edges.append((fams[1][0], fams[2][-1], "朋友", 65))
        edges.append((fams[0][1], fams[2][1], "校友", 50))
    for a, b, rel, sc in edges:
        conn.execute("INSERT INTO links (person_a, person_b, relation, score, "
                     "created_at) VALUES (?,?,?,?,?)",
                     (a, b, rel, sc,
                      datetime.datetime.now().isoformat(timespec="seconds")))
    conn.commit()
conn.close()
print("[pre] TESTV26 ready", flush=True)

import tkinter as tk
from ui import App

_real = tk.Misc.mainloop
signal.signal(signal.SIGTERM, lambda *a: (cleanup(), os._exit(0)))


def fake(self):
    def _zoom_fit():
        # 15 人时单环半径超出视口，缩到 0.62 才能看全环与外弧
        self._zoom = 0.62
        self.refresh_network()
        self.update_idletasks()

    def _job1():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, "_v026_links.png"))
            print("shot default saved", flush=True)
            # 悬停一张卡：看高亮 + 关系标签
            pid = None
            for p, _ in list(self.node_pos.items())[:1]:
                pid = p
            if pid:
                self._hover(pid, True)
                self.update()
                self.update_idletasks()
                self.after(500, _job2)
            else:
                self.destroy()
        except Exception as e:
            print("SHOT-ERR:", e, flush=True)
            self.destroy()

    def _job2():
        try:
            import shot
            shot.grab(self.winfo_id(), os.path.join(ROOT, "_v026_links_hover.png"))
            print("shot hover saved", flush=True)
        except Exception as e:
            print("SHOT-ERR2:", e, flush=True)
        self.destroy()
        cleanup()

    self.after(4200, _zoom_fit)
    self.after(5200, _job1)
    _real(self)


tk.Misc.mainloop = fake
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_net)
app.mainloop()
