# -*- coding: utf-8 -*-
"""加压测试：往临时库里塞更多人，跑真实的自动布局，量「画出来之后」的干涉。

不动用户真实库：先把 dist/data/agent.db 复制到 _tmp/，再往副本里插人。

用法：python tools/test_more.py [追加人数] [输出前缀]
      python tools/test_more.py 12 _more24
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

EXTRA = int(sys.argv[1]) if len(sys.argv) > 1 else 12
OUT = sys.argv[2] if len(sys.argv) > 2 else "_more%d" % (18 + EXTRA)
STYLE = os.environ.get("STYLE", "hard")

NAMES = ["周工", "许老师", "老谭", "罗姐", "阿彬", "梁书记", "小曾", "温医生",
         "赖师傅", "阿珍", "邵同学", "崔教练", "廖会计", "阿伟", "毕工",
         "冯店长", "简姨", "施师傅", "阿蓉", "柏老师", "祁工", "毛阿姨",
         "尉然", "岑老师", "米雪", "阿淦", "费工", "温哥"]
TYPES = ["挚友", "家人", "普通朋友", "同事", "客户", "老同学", "邻居"]

SRC = os.path.join(ROOT, "dist", "data", "agent.db")
os.makedirs(os.path.join(ROOT, "_tmp"), exist_ok=True)
TMP = tempfile.mkdtemp(prefix="more_", dir=os.path.join(ROOT, "_tmp"))
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
base_ids = [r[0] for r in conn.execute("SELECT id FROM persons ORDER BY id")]
base_n = len(base_ids)
# 真实库里可能残留「指向已删除人物」的关系（person 19 在 persons 表里不存在）。
# 不清掉的话，下面 INSERT 出来的第一个人刚好拿到 id 19，凭空多背三条关系，
# 测试结果就没法比了。
_orphan = conn.execute(
    "DELETE FROM links WHERE person_a NOT IN (SELECT id FROM persons) "
    "OR person_b NOT IN (SELECT id FROM persons)").rowcount
if _orphan:
    print("[prep] 清掉 %d 条悬空关系" % _orphan, flush=True)
conn.commit()
now = "2026-10-29 10:00:00"

new_ids = []
for i in range(EXTRA):
    nm = NAMES[i % len(NAMES)]
    cur = conn.execute(
        "INSERT INTO persons(name,relation_type,closeness,sentiment_weight,"
        "created_at,source) VALUES(?,?,?,?,?,?)",
        (nm, TYPES[i % len(TYPES)], round(0.35 + 0.05 * (i % 10), 2), 0.5,
         now, "test"))
    new_ids.append(cur.lastrowid)
conn.commit()

# 关系：① 链（相邻）② 每 2 人一条跨 4 位的弦 ③ 每 3 人连回一个老人（跨大半圈）
seq = base_ids + new_ids
links = []
for i, pid in enumerate(new_ids):
    pos_i = base_n + i
    links.append((seq[pos_i - 1], pid))
    if STYLE == "local":
        if i % 2 == 1:
            links.append((seq[pos_i - 2], pid))
    else:
        if i % 2 == 1 and pos_i - 4 >= 0:
            links.append((seq[pos_i - 4], pid))
        if i % 3 == 0:
            links.append((base_ids[(i * 5 + 2) % base_n], pid))
for a, b in links:
    if a == b:
        continue
    conn.execute("INSERT INTO links(person_a,person_b,relation,note,created_at,"
                 "score) VALUES(?,?,?,?,?,?)", (a, b, "认识", "", now, 50))
conn.commit()
n_p = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
n_l = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
conn.close()
print("[prep] 临时库 persons=%d links=%d（原 18 / 17）" % (n_p, n_l), flush=True)

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
    """记每一次真实布局的耗时——App 构造一次、切到关系网络 tab 又一次。
    用户点开关系网络看到的是**第二次**（切 tab 那次），不是我们上一版去
    强制重建的第三次。以前量重排后的位置，指标和用户看到的对不上。"""
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
            # 会重算一遍，截图和刚量到的位置就对不上了。
            self.fit_view()
            self._freeze_layout = True
            self.refresh_network()
            self.update_idletasks()
        except Exception as e:
            print("[dbg] fit_view 失败:", e, flush=True)
        pos = self.node_pos
        name = {it["pid"]: it["name"] for it in self._web_items()}
        cx, cy = getattr(self, "_web_center", (0.0, 0.0))
        rts = self._link_routes
        segs = [(rt["pts"][0], rt["pts"][1],
                 name[rt["lnk"]["person_a"]], name[rt["lnk"]["person_b"]])
                for rt in rts]
        spokes = [((cx, cy), pos[p], name[p]) for p in pos]

        # ① 长线穿辐射线
        tot, worst = 0, []
        for s in segs:
            hit = []
            for sp in spokes:
                if sp[2] in (s[2], s[3]):
                    continue
                if seg_cross(s[0], s[1], sp[0], sp[1]):
                    hit.append(sp[2])
            tot += len(hit)
            if hit:
                worst.append((s[2] + "-" + s[3], hit))
        # ② 线-线交叉
        cc = 0
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                if seg_cross(segs[i][0], segs[i][1],
                             segs[j][0], segs[j][1]):
                    cc += 1
        # ③ 线到非端点卡的最小净空
        gmin, gwho = 999.0, ""
        for s in segs:
            for q in pos:
                if name[q] in (s[2], s[3]):
                    continue
                g = nl.seg_rect_gap(s[0], s[1], pos[q], hw, hh)
                if g < gmin:
                    gmin, gwho = g, "%s-%s 近 %s" % (s[2], s[3], name[q])
        # ④ 半径 / 内圈
        rv = sorted(math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pos)
        rm = sum(rv) / len(rv)
        inner = sum(1 for r in rv if r < 0.86 * rm)

        print("[result] 人数 %d  连线 %d  卡片重叠 %d 对" %
              (len(pos), len(segs), nl.overlap_pairs(pos, hw, hh)))
        print("[result] 穿辐射线 %d 处   线-线交叉 %d 对   "
              "最小净空 %.1fpx (%s)" % (tot, cc, gmin, gwho))
        print("[result] 半径 %.2fR ~ %.2fR   内圈 %d 张   耗时 %.0fms"
              % (rv[0] / rm, rv[-1] / rm, inner, dt))
        print("[result] 两层接了吗 =", getattr(self, "_net_inner", None),
              "  avoid =", getattr(self, "_net_avoid", None))
        if worst:
            for w in sorted(worst, key=lambda x: -len(x[1]))[:5]:
                print("           穿线 %-16s 穿 %d 条 %s"
                      % (w[0], len(w[1]), w[1][:4]))
        def _shot():
            try:
                import shot
                self.update()
                shot.grab(self.winfo_id(), os.path.join(ROOT, OUT + ".png"))
                print("[shot] %s.png" % OUT, flush=True)
                if os.environ.get("UI_REVIEW"):
                    for label, tab in (("人脉卡片", self.tab_cards),
                                       ("智能送礼", self.tab_gift),
                                       ("祝福语", self.tab_greet),
                                       ("连接器", self.tab_conn)):
                        self.nb.select(tab)
                        self.update()
                        shot.grab(self.winfo_id(), os.path.join(
                            ROOT, OUT + "_" + label + ".png"))
            except Exception as e:
                print("[shot] FAIL", e)
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
