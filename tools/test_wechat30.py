# -*- coding: utf-8 -*-
"""绿泡泡 30 人：把沙盒好友全部导进临时库（等价于点「一键拉取全部」），
跑真实的自动布局，量「画出来之后」的干涉。

不动用户真实库：先把 dist/data/agent.db 复制到 _tmp/，再往副本里补人。

用法：python tools/test_wechat30.py [输出前缀]
"""
import os
import sys
import math
import time
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

OUT = sys.argv[1] if len(sys.argv) > 1 else "_wx30"

SRC = os.path.join(ROOT, "dist", "data", "agent.db")
os.makedirs(os.path.join(ROOT, "_tmp"), exist_ok=True)
TMP = tempfile.mkdtemp(prefix="wx30_", dir=os.path.join(ROOT, "_tmp"))
DATA = os.path.join(TMP, "data")
os.makedirs(DATA, exist_ok=True)
shutil.copy2(SRC, os.path.join(DATA, "agent.db"))

import db
db.BASE = TMP
db.DATA_DIR = DATA
db.DB_PATH = os.path.join(DATA, "agent.db")
db.init_db()

import wechat_sandbox as ws

conn = db.get_conn()
conn.execute("DELETE FROM links WHERE person_a NOT IN (SELECT id FROM persons) "
             "OR person_b NOT IN (SELECT id FROM persons)")
conn.execute("UPDATE persons SET pos_x=NULL, pos_y=NULL")
conn.commit()
conn.close()

# ① 补齐沙盒里还没拉取的人（= 点「一键拉取全部」）
have = {p["name"] for p in db.list_persons()}
new = [c for c in ws.list_contacts() if c["name"] not in have]
for c in new:
    db.save_person(ws.contact_to_person(c))

# ② 同步沙盒内的关系连线（复刻 App._sync_sandbox_links）
name2id = {p["name"]: p["id"] for p in db.list_persons()}
for c in ws.list_contacts():
    a = name2id.get(c["name"])
    if not a:
        continue
    for tgt, rel in ws.contact_links(c):
        b = name2id.get(tgt)
        if b and b != a:
            db.add_link(a, b, rel)

conn = db.get_conn()
n_p = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
n_l = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
pairs = conn.execute(
    "SELECT COUNT(*) FROM (SELECT DISTINCT MIN(person_a,person_b), "
    "MAX(person_a,person_b) FROM links)").fetchone()[0]
conn.close()
print("[prep] 沙盒新增 %d 人；临时库 persons=%d links=%d（唯一边 %d，边/人=%.2f）"
      % (len(new), n_p, n_l, pairs, pairs / max(1, n_p)), flush=True)

import tkinter as tk
import netlayout as nl
from ui import App, NODE_W, NODE_H

hw, hh = NODE_W / 2.0, NODE_H / 2.0


def seg_cross(p1, p2, p3, p4):
    def cr(o, a, b):
        return ((a[0] - o[0]) * (b[1] - o[1])
                - (a[1] - o[1]) * (b[0] - o[0]))
    d1, d2 = cr(p3, p4, p1), cr(p3, p4, p2)
    d3, d4 = cr(p1, p2, p3), cr(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


_real = tk.Misc.mainloop
_STAT = {"n": 0, "dt": 0.0}
_orig_refresh = App.refresh_network


def _hook_refresh(self):
    t0 = time.time()
    _orig_refresh(self)
    _STAT["n"] += 1
    _STAT["dt"] = (time.time() - t0) * 1000.0


App.refresh_network = _hook_refresh


def fake(self):
    def _go():
        dt = _STAT["dt"]
        try:
            # fit_view 只写 _zoom，画面要等 _draw_web 才更新。重画一次，但必须
            # 冻结布局（_freeze_layout=True 走 reuse 分支复用 node_pos），否则
            # 会重算一遍，量到的就不是刚才那套位置了。
            self.fit_view()
            self._freeze_layout = True
            self.refresh_network()
            self.update_idletasks()
        except Exception as e:
            print("[dbg] fit_view 失败:", e, flush=True)
        pos = self.node_pos
        name = {it["pid"]: it["name"] for it in self._web_items()}
        cx, cy = getattr(self, "_web_center", (0.0, 0.0))
        segs = [(rt["pts"][0], rt["pts"][1],
                 name[rt["lnk"]["person_a"]], name[rt["lnk"]["person_b"]])
                for rt in self._link_routes]
        spokes = [((cx, cy), pos[p], name[p]) for p in pos]

        tot, worst = 0, []
        for s in segs:
            hit = [sp[2] for sp in spokes
                   if sp[2] not in (s[2], s[3])
                   and seg_cross(s[0], s[1], sp[0], sp[1])]
            tot += len(hit)
            if hit:
                worst.append((s[2] + "-" + s[3], hit))
        cc = sum(1 for i in range(len(segs)) for j in range(i + 1, len(segs))
                 if seg_cross(segs[i][0], segs[i][1],
                              segs[j][0], segs[j][1]))
        gmin, gwho = 999.0, ""
        for s in segs:
            for q in pos:
                if name[q] in (s[2], s[3]):
                    continue
                g = nl.seg_rect_gap(s[0], s[1], pos[q], hw, hh)
                if g < gmin:
                    gmin, gwho = g, "%s-%s 近 %s" % (s[2], s[3], name[q])
        rv = sorted(math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pos)
        rm = sum(rv) / len(rv)
        inner = sum(1 for r in rv if r < 0.86 * rm)

        print("[result] 人数 %d  连线 %d" % (len(pos), len(segs)))
        print("[result] 穿辐射线 %d 处   线-线交叉 %d 对   最小净空 %.1fpx (%s)"
              % (tot, cc, gmin, gwho))
        print("[result] 半径 %.2fR ~ %.2fR   内圈 %d 张   耗时 %.0fms"
              % (rv[0] / rm, rv[-1] / rm, inner, dt))
        print("[result] 两层接了吗 =", getattr(self, "_net_inner", None),
              "  avoid =", getattr(self, "_net_avoid", None))
        for w in sorted(worst, key=lambda x: -len(x[1]))[:6]:
            print("           穿线 %-16s 穿 %d 条 %s"
                  % (w[0], len(w[1]), w[1][:4]))

        def _shot():
            try:
                import shot
                self.update()
                shot.grab(self.winfo_id(), os.path.join(ROOT, OUT + ".png"))
                print("[shot] %s.png" % OUT, flush=True)
            except Exception as e:
                print("[shot] FAIL", e, flush=True)
            self.destroy()

        self.after(600, _shot)

    self.after(2600, _go)
    _real(self)


tk.Misc.mainloop = fake
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_net)
app.mainloop()
shutil.rmtree(TMP, ignore_errors=True)
