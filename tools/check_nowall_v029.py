# -*- coding: utf-8 -*-
"""v0.29 「空气墙」回归：画布要能一直拖，不许半路撞墙停下来

用户诉求（2026-10-28）：「画布为什么有空气墙，应该整个的范围都是可以移动
的位置才行」。

旧版两处硬边界：
① 拖卡片钳在画布 ±50%（`nx = clamp(-0.5W, 1.5W)`）——拖到那儿就停住；
② 可平移世界 = 内容包围盒 + 10%~25% 视口，网一大就只剩百来像素可拖。

本测试：
- 世界（scrollregion）至少 2.4 屏 × 2.4 屏
- 用合成鼠标事件把一张卡拖出 3000+ px（远超旧钳位 1.5W），位置必须真的到那儿
- 拖到边上时画布自动平移，世界跟着扩张（region 变大）
- 存下的比例坐标 > 1.62（旧版会被当脏数据清掉），重新读取后位置还在
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


class Ev:
    """最小事件对象：处理函数只读 e.x / e.y"""
    def __init__(self, x, y):
        self.x, self.y = x, y


def main():
    app = App()
    app.geometry("1600x920+120+120")
    app.nb.select(app.tab_net)
    app.update()
    app.refresh_network()
    app.update()

    c = app.web_canvas
    c.update_idletasks()
    vw, vh = c.winfo_width(), c.winfo_height()
    x0, y0, x1, y1 = app._sr
    print("画布 %dx%d  世界 %.0f×%.0f（%.2f 屏 × %.2f 屏）" % (
        vw, vh, x1 - x0, y1 - y0, (x1 - x0) / vw, (y1 - y0) / vh))

    check("世界宽度 ≥ 2.4 屏", (x1 - x0) >= 2.4 * vw - 2, round(x1 - x0))
    check("世界高度 ≥ 2.4 屏", (y1 - y0) >= 2.4 * vh - 2, round(y1 - y0))

    pid = next(iter(app.node_pos))
    row = db.get_person(pid)
    old = (row.get("pos_x"), row.get("pos_y"))
    before = dict(app.node_pos)
    z = max(1e-6, getattr(app, "_zoom", 1.0))
    ox, oy = c.canvasx(0), c.canvasy(0)
    sx, sy = before[pid][0] - ox, before[pid][1] - oy
    print("拖拽对象：%s  zoom=%.2f  起点屏幕 (%d, %d)" % (
        row.get("name"), z, sx, sy))

    try:
        reg0 = app._sr
        app.on_press(Ev(int(sx + 8), int(sy + 8)))
        app.update()
        # 分 6 步往右拖 3600px（旧钳位会在 1.5W≈1900 处截停）
        for k in range(1, 7):
            app.on_drag(Ev(int(sx + 8 + 600 * k), int(sy + 8)))
            app.update()
        app.on_release(Ev(int(sx + 8 + 3600), int(sy + 8)))
        app.update()

        after = dict(app.node_pos)
        dx = after[pid][0] - before[pid][0]
        check("卡片能拖出 3000px 以上（无硬钳位）", dx > 3000 / z * 0.9,
              round(dx, 1))

        reg1 = app._sr
        check("世界随卡片扩张（右边伸长）", reg1[2] > reg0[2] + 100,
              (round(reg0[2]), round(reg1[2])))
        check("卡片仍在世界内",
              reg1[0] <= after[pid][0] <= reg1[2]
              and reg1[1] <= after[pid][1] <= reg1[3], None)

        row = db.get_person(pid)
        ratio = float(row.get("pos_x") or 0)
        check("保存的比例坐标超过旧上限 1.62", ratio > 1.62, round(ratio, 3))

        # 模拟重开：重新读取（旧版会在读取时把 >1.62 当脏数据清库）
        app._layout_sig = None
        app.node_pos = {}
        app.refresh_network()
        app.update()
        row2 = db.get_person(pid)
        check("重新加载位置没被清掉",
              row2.get("pos_x") is not None
              and abs(float(row2["pos_x"]) - ratio) < 1e-3, row2.get("pos_x"))
        check("重新加载后卡片还在原处",
              abs(app.node_pos.get(pid, (0, 0))[0] - after[pid][0]) < 2.0,
              round(app.node_pos.get(pid, (0, 0))[0] - after[pid][0], 1))

        # 平移撞边界：世界必须继续长，不许停住
        reg2 = app._sr
        app._pan_by(9000, 0)
        app.update()
        reg3 = app._sr
        check("平移撞边界 → 世界向左扩张（无空气墙）",
              reg3[0] < reg2[0] - 100, (round(reg2[0]), round(reg3[0])))
        check("平移后视口仍在世界内", app._view_origin()[0] >= reg3[0] - 1,
              round(app._view_origin()[0] - reg3[0], 1))
    finally:
        if old[0] is None or abs(float(old[0])) > 1.05 or abs(float(old[1])) > 1.05:
            db.clear_position(pid)
        else:
            db.set_position(pid, old[0], old[1])
        app._layout_sig = None
        app.destroy()

    print("\n===== 无空气墙：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
