# -*- coding: utf-8 -*-
"""v0.28 拖拽回归：手一松，除了我拖的那张卡，别的卡一个像素都不许动

用户诉求（2026-10-28）：「为什么我现在调整位置他会自动重新布局，不要在
我调整位置的时候他自动重新布局」。

这条测试模拟真实鼠标事件（按下 → 拖 60/40 像素 → 松开），再比对整张图
的 node_pos：
- 被拖的卡：位移 ≈ 屏幕位移 / zoom
- 其余卡：位移必须为 0（旧版会去重叠推挤 / 炸开自愈整图重排）
并检查位置已落库。结束后还原。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import db
db.init_db()

from ui import App

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [ok] " if cond else "  [FAIL] ") + name +
          (("  | " + str(extra)) if extra else ""))


def main():
    app = App()
    app.geometry("1600x920+120+120")
    app.nb.select(app.tab_net)
    app.update()
    app.refresh_network()
    app.update()

    c = app.web_canvas
    c.update_idletasks()
    pid = next(iter(app.node_pos))
    p = db.get_person(pid)
    old = (p.get("pos_x"), p.get("pos_y"))
    name = p.get("name")
    before = dict(app.node_pos)
    z = max(1e-6, getattr(app, "_zoom", 1.0))
    ox, oy = c.canvasx(0), c.canvasy(0)      # 视口左上角在 canvas 坐标系的偏移
    wx, wy = before[pid][0] - ox, before[pid][1] - oy

    print("拖拽对象：%s  zoom=%.2f  canvas=%dx%d" % (
        name, z, c.winfo_width(), c.winfo_height()))

    # v0.29 起「拖到视口边缘画布自动平移」是特性（见 check_nowall_v029），
    # 会让 1:1 跟手的断言混入平移量。这里先关掉它，只验「不触发重排」。
    _edge_pan, app._edge_pan = app._edge_pan, (lambda *a, **k: None)

    try:
        c.event_generate("<Button-1>", x=int(wx + 10), y=int(wy + 10))
        app.update()
        for k in range(1, 5):
            c.event_generate("<B1-Motion>",
                            x=int(wx + 10 + 60 * k / 4.0),
                            y=int(wy + 10 + 40 * k / 4.0))
            app.update()
        c.event_generate("<ButtonRelease-1>", x=int(wx + 70), y=int(wy + 50))
        app.update()

        after = dict(app.node_pos)
        dx = after[pid][0] - before[pid][0]
        dy = after[pid][1] - before[pid][1]
        check("被拖的卡跟着鼠标走",
              abs(dx - 60 / z) < 6 and abs(dy - 40 / z) < 6,
              (round(dx, 1), round(dy, 1)))

        moved = {q: (round(after[q][0] - before[q][0], 1),
                     round(after[q][1] - before[q][1], 1))
                 for q in before if q != pid and q in after}
        bad = {q: v for q, v in moved.items() if abs(v[0]) > 0.5 or abs(v[1]) > 0.5}
        check("其余卡片零位移（不触发重排）", not bad, bad or len(moved))

        row = db.get_person(pid)
        check("位置已写入数据库",
              row.get("pos_x") is not None
              and abs(float(row["pos_x"]) * c.winfo_width()
                      - after[pid][0]) < 8,
              (row.get("pos_x"), row.get("pos_y")))

        # 再刷新十次（模拟切页回来 / 定时刷新）：位置不许漂
        keep = after[pid]
        for _ in range(10):
            app.refresh_network()
            app.update()
        drift = (abs(app.node_pos[pid][0] - keep[0]),
                 abs(app.node_pos[pid][1] - keep[1]))
        check("连续刷新 10 次位置不漂移", max(drift) < 0.5,
              tuple(round(v, 1) for v in drift))
    finally:
        app._edge_pan = _edge_pan
        # 还原：原件若本身就像测试拖出来的（|比例|>1.05 只可能来自拖拽），
        # 就清掉而不是写回——否则每次跑测试都会把脏值「合法地」保存下来
        if old[0] is None or abs(float(old[0])) > 1.05 or abs(float(old[1])) > 1.05:
            db.clear_position(pid)
        else:
            db.set_position(pid, old[0], old[1])
        app._layout_sig = None
        app.destroy()

    print("\n===== 拖拽回归：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
