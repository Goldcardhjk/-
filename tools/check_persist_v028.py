# -*- coding: utf-8 -*-
"""v0.28 位置持久化验证：拖拽保存的坐标，重启后必须原样回来

用户诉求（2026-10-28）：「每次就保存我调整的位置，退出程序后要下次进来
还是我之前保存的位置」。

复现「重启」= 丢空内存布局（node_pos / _layout_sig）后重新 refresh，
走的正是启动路径（从 persons.pos_x/pos_y 读回 pinned，再跑布局）。

测试前后还原该人物的原始坐标，不动用户真实数据。
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
    app.update()
    app.refresh_network()
    app.update()

    c = app.web_canvas
    W, H = c.winfo_width(), c.winfo_height()
    pid = next(iter(app.node_pos))
    p = db.get_person(pid)
    old = (p.get("pos_x"), p.get("pos_y"))
    name = p.get("name")
    print("测试对象：%s  canvas %dx%d" % (name, W, H))

    def restart():
        """模拟重开程序：内存布局全丢，只有库里的坐标"""
        app.node_pos = {}
        app._layout_sig = None
        app.update()
        app.refresh_network()
        app.update()

    try:
        # ① 普通位置
        for tx, ty in ((0.31, 0.62), (0.75, 0.28)):
            db.set_position(pid, tx, ty)
            restart()
            got = app.node_pos.get(pid)
            ok = got is not None and abs(got[0] - tx * W) < 6 \
                and abs(got[1] - ty * H) < 6
            check("重启后位置保持 (%.2f, %.2f)" % (tx, ty), ok,
                  None if got is None else (round(got[0], 1),
                                            round(got[1], 1)))

        # ② 拖到画布外（比例 >1）：以前会被当脏数据清掉，现在必须保留
        db.set_position(pid, 1.18, 0.45)
        restart()
        got = app.node_pos.get(pid)
        ok = got is not None and abs(got[0] - 1.18 * W) < 8
        check("拖到画布外的位置不被当脏数据清掉", ok,
              None if got is None else (round(got[0], 1), round(got[1], 1)))
        still = db.get_person(pid)
        check("库里的坐标没被自愈清空",
              still.get("pos_x") is not None
              and abs(float(still["pos_x"]) - 1.18) < 1e-6,
              still.get("pos_x"))

        # ③ 重启后再拖一次，位置仍然跟着走（不会回弹）
        db.set_position(pid, 0.42, 0.40)
        restart()
        a = app.node_pos.get(pid)
        db.set_position(pid, 0.58, 0.55)
        restart()
        b = app.node_pos.get(pid)
        check("连续两次拖拽都能落到新位置",
              a is not None and b is not None
              and abs(b[0] - 0.58 * W) < 6 and abs(b[0] - a[0]) > 100,
              (round(a[0], 1), round(b[0], 1)) if a and b else None)
    finally:
        # 还原
        # 还原：原件若本身就像测试拖出来的（|比例|>1.05 只可能来自拖拽），
        # 就清掉而不是写回——否则每次跑测试都会把脏值「合法地」保存下来
        if old[0] is None or abs(float(old[0])) > 1.05 or abs(float(old[1])) > 1.05:
            db.clear_position(pid)
        else:
            db.set_position(pid, old[0], old[1])
        app.destroy()

    print("\n===== 位置持久化：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
