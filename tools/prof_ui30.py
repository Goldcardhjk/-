# -*- coding: utf-8 -*-
"""30 人下 UI 各项操作的耗时拆解：弄清「卡得没法操作」卡在哪一步。

用临时库（复制真实库 + 按绿泡泡沙盒补齐 30 人），完全走真实 UI 代码路径。
"""
import os
import sys
import time
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SRC = os.path.join(ROOT, "dist", "data", "agent.db")
os.makedirs(os.path.join(ROOT, "_tmp"), exist_ok=True)
TMP = tempfile.mkdtemp(prefix="prof_", dir=os.path.join(ROOT, "_tmp"))
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
conn.execute("UPDATE persons SET pos_x=NULL, pos_y=NULL")
conn.commit()
conn.close()

have = {p["name"] for p in db.list_persons()}
for c in ws.list_contacts():
    if c["name"] not in have:
        db.save_person(ws.contact_to_person(c))
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
NP = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
NL = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
conn.close()
print("[库] persons=%d links=%d" % (NP, NL), flush=True)

import tkinter as tk
from ui import App

# 看每次 _draw_web 到底走了「复用」还是「重排」：比对进/出的布局签名，
# 并检查 node_pos 是否覆盖了全部人物。
_orig_draw = App._draw_web
_SEEN = {"prev": None}


def _wrap_draw(self, items=None):
    before = getattr(self, "_layout_sig", None)
    _orig_draw(self, items)
    after = getattr(self, "_layout_sig", None)
    if items is None:
        items = self._web_items()
    pids = [it["pid"] for it in items]
    missing = [p for p in pids if p not in (self.node_pos or {})]
    same = (before is not None and before == after)
    print("[draw] sig %s  pos 缺 %d 张" %
          ("未变(复用)" if same else "变了(重排)", len(missing)), flush=True)
    _SEEN["prev"] = after


App._draw_web = _wrap_draw

_real = tk.Misc.mainloop


def T(label, fn, n=1):
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) * 1000.0 / n
    print("[耗时] %-28s %7.0f ms" % (label, dt), flush=True)
    return dt


def fake(self):
    def _go():
        try:
            self.update()
        except Exception:
            pass

        print("\n--- 关系网络 ---", flush=True)
        T("首次布局（含构造图）", self.refresh_network)
        T("刷新·复用位置", self.refresh_network, 3)

        app._zoom = min(1.0, app._zoom * 1.12)
        T("滚轮缩放一次", self.refresh_network, 3)

        app._freeze_layout = True
        try:
            T("拖动松手重绘", self.refresh_network, 3)
        finally:
            app._freeze_layout = False

        pid = list(self.node_pos)[0]
        T("单击卡片→打开资料卡", lambda: self.open_profile(pid))
        try:
            self.update()
        except Exception:
            pass

        print("\n--- 其它页 ---", flush=True)
        T("人脉卡片页刷新", self.refresh_cards)
        T("下拉框刷新", self.refresh_combos, 3)
        T("切到关系网络 tab", lambda: (self.nb.select(self.tab_net),
                                       self.update()))

        print("\n--- 内部拆解 ---", flush=True)
        items = self._web_items()
        T("  _web_items（查库+打分）", self._web_items, 3)
        T("  _refresh_rank 排名行", lambda: self._refresh_rank(items), 3)
        T("  _refresh_reminders 提醒", lambda: self._refresh_reminders(items), 3)
        T("  _refresh_overview 概览", lambda: self._refresh_overview(items), 3)

        rts = len(getattr(self, "_link_routes", []))
        print("[信息] 连线 %d 条  卡片 %d 张  zoom=%.2f"
              % (rts, len(self.node_pos), self._zoom), flush=True)
        self.destroy()

    self.after(2200, _go)
    _real(self)


tk.Misc.mainloop = fake
app = App()
app.geometry("1600x920+100+100")
app.nb.select(app.tab_net)
app.mainloop()
shutil.rmtree(TMP, ignore_errors=True)
