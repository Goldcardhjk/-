# -*- coding: utf-8 -*-
"""诊断「画出来的样子」：线端是否贴卡、关系线之间是否交叉、线离卡多远

dbg_gap 只看「线到卡的净空」，这里看画布上真实的路由结果（_link_routes），
包括：端点是否真的贴在卡片边框上（悬空线头）、关系线两两交叉、关系线与
辐射线交叉。
"""
import os
import sys
import shutil
import tempfile
import math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SRC = os.path.join(ROOT, "dist", "data", "agent.db")
os.makedirs(os.path.join(ROOT, "_tmp"), exist_ok=True)
TMP = tempfile.mkdtemp(prefix="dbgdraw_", dir=os.path.join(ROOT, "_tmp"))
DATA = os.path.join(TMP, "data")
os.makedirs(DATA, exist_ok=True)
shutil.copy2(SRC, os.path.join(DATA, "agent.db"))

import db
db.BASE = TMP
db.DATA_DIR = DATA
db.DB_PATH = os.path.join(DATA, "agent.db")
db.init_db()
conn = db.get_conn()
cleared = conn.execute(
    "SELECT count(*) FROM persons WHERE pos_x IS NOT NULL").fetchone()[0]
conn.execute("UPDATE persons SET pos_x=NULL, pos_y=NULL")
conn.commit()
conn.close()

import tkinter as tk
import netlayout as nl
from ui import App, NODE_W, NODE_H

_real = tk.Misc.mainloop


def _seg_cross(p1, p2, p3, p4):
    def cr(o, a, b):
        return ((a[0] - o[0]) * (b[1] - o[1])
                - (a[1] - o[1]) * (b[0] - o[0]))
    d1, d2 = cr(p3, p4, p1), cr(p3, p4, p2)
    d3, d4 = cr(p1, p2, p3), cr(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _rect_dist(pt, r, hw, hh):
    """点到卡片边框的距离（在框内为负）"""
    dx = abs(pt[0] - r[0]) - hw
    dy = abs(pt[1] - r[1]) - hh
    if dx <= 0 and dy <= 0:
        return max(dx, dy)
    return math.hypot(max(dx, 0.0), max(dy, 0.0))


def fake(self):
    def _go():
        try:
            self.fit_view()
            self.refresh_network()
            self.update_idletasks()
        except Exception as e:
            print("LAYOUT-ERR:", e)
        pos = self.node_pos
        name = {it["pid"]: it["name"] for it in self._web_items()}
        cx, cy = getattr(self, "_web_center", (0.0, 0.0))
        hw, hh = NODE_W / 2.0, NODE_H / 2.0
        rts = self._link_routes
        print("cards=%d routes=%d" % (len(pos), len(rts)))
        rad = sorted(math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pos)
        print("半径 %.0f~%.0f（跨度 %.0f%%）"
              % (rad[0], rad[-1], (rad[-1] - rad[0]) / (sum(rad) / len(rad)) * 100))
        _rm = sum(rad) / len(rad)
        _lst = sorted(((math.hypot(pos[p][0] - cx, pos[p][1] - cy), name[p])
                       for p in pos))
        print("   每人半径/R：", "  ".join("%s %.2f" % (nm, r / _rm) for r, nm in _lst))

        # ① 线头是否贴卡
        print("\n--- 线头离自己卡片的边框距离（应 ≈ +1.5）")
        bad_tips = []
        for rt in rts:
            lnk = rt["lnk"]
            for pid, pt in ((lnk["person_a"], rt["pts"][0]),
                            (lnk["person_b"], rt["pts"][1])):
                g = _rect_dist(pt, pos[pid], hw, hh)
                if abs(g - 1.5) > 1.0:
                    bad_tips.append((round(g, 2), name[pid]))
        bad_tips.sort()
        print("   异常线头 %d 个 %s" % (len(bad_tips), bad_tips[:6]))

        # ② 关系线两两交叉
        segs = [(rt["pts"][0], rt["pts"][1],
                 name[rt["lnk"]["person_a"]], name[rt["lnk"]["person_b"]])
                for rt in rts]
        cross = []
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                if _seg_cross(segs[i][0], segs[i][1], segs[j][0], segs[j][1]):
                    cross.append((segs[i][2] + "-" + segs[i][3],
                                  segs[j][2] + "-" + segs[j][3]))
        print("\n--- 关系线交叉 %d 对" % len(cross))
        for c in cross[:10]:
            print("   ", c)
        print("   count_crossings(中心连线) =",
              nl.count_crossings([(rt["lnk"]["person_a"], rt["lnk"]["person_b"])
                                  for rt in rts], pos)[0])

        # ③ 关系线与辐射线交叉
        spokes = [((cx, cy), pos[p], name[p]) for p in pos]
        scross = []
        for s in segs:
            hit = []
            for sp in spokes:
                if sp[2] in (s[2], s[3]):
                    continue
                if _seg_cross(s[0], s[1], sp[0], sp[1]):
                    hit.append(sp[2])
            scross.append((s[2] + "-" + s[3], hit))
        tot = sum(len(h) for _e, h in scross)
        print("\n--- 关系线穿过辐射线：合计 %d 处" % tot)
        for e, h in sorted(scross, key=lambda x: -len(x[1]))[:8]:
            print("    %-12s 穿 %d 条 %s" % (e, len(h), h))

        # ④ 线到非端点卡片的最小净空
        worst = []
        for s in segs:
            a = s[2]
            b = s[3]
            pa, pb = s[0], s[1]
            for q in pos:
                if name[q] in (a, b):
                    continue
                g = nl.seg_rect_gap(pa, pb, pos[q], hw, hh)
                worst.append((round(g, 1), a + "-" + b, name[q]))
        worst.sort()
        print("\n--- 线到非端点卡片最紧的 6 处")
        for w in worst[:6]:
            print("    gap=%5.1f  %-12s 近 %s" % w)

        # ⑤ 相邻两条边的夹角（同一张卡上，两条边出射方向夹角）
        print("\n--- 同一张卡上两条边的出射夹角（< 18° 看着会「糊在一起」）")
        byp = {}
        for rt in rts:
            lnk = rt["lnk"]
            for pid, other, pt in ((lnk["person_a"], lnk["person_b"], rt["pts"][1]),
                                   (lnk["person_b"], lnk["person_a"], rt["pts"][0])):
                byp.setdefault(pid, []).append((other, pt))
        tight = []
        for pid, lst in byp.items():
            for i in range(len(lst)):
                for j in range(i + 1, len(lst)):
                    p0 = pos[pid]
                    a1 = math.atan2(lst[i][1][1] - p0[1], lst[i][1][0] - p0[0])
                    a2 = math.atan2(lst[j][1][1] - p0[1], lst[j][1][0] - p0[0])
                    d = abs(a1 - a2) % (2 * math.pi)
                    d = min(d, 2 * math.pi - d)
                    if d < math.radians(18):
                        tight.append((round(math.degrees(d), 1), name[pid],
                                      name[lst[i][0]], name[lst[j][0]]))
        tight.sort()
        print("   过近 %d 组" % len(tight))
        for t in tight[:8]:
            print("    %5.1f°  %s 的 %s / %s" % t)
        self.destroy()

    self.after(4200, _go)
    _real(self)


tk.Misc.mainloop = fake
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_net)
app.mainloop()
print("\n(清空了 %d 张卡的存档坐标，纯自动布局)" % cleared)
shutil.rmtree(TMP, ignore_errors=True)
