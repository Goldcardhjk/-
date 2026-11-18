# -*- coding: utf-8 -*-
"""操作手册配图采集：隔离演示库，逐页/逐弹窗 PrintWindow 抓图。

复用 _v069/drive_v069.py 的那套「代码驱动 App + 合成抓帧」思路，但不做
时间轴，只在每个点位停稳后拍一张。

用法：.venv/Scripts/python.exe 手册_v070/shoot_manual.py
输出：手册_v070/shots/*.png
"""
import os
import sys
import time

ROOT = r"D:/进步壹号/进步壹号Agent"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "shots")
V065 = os.path.join(ROOT, "进步壹号发布视频", "_v065")
DEMO = os.path.join(ROOT, "进步壹号发布视频", "_v069", "demo_data")

os.environ["JINBU_DEMO_DATA"] = DEMO
os.environ["JINBU_FRAME_DIR"] = os.path.join(HERE, "_rawframes")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, V065)

os.makedirs(OUT, exist_ok=True)

import drive                                            # noqa: E402
from drive import Demo, Rec, close_tops, tabs, scene, gate_enter   # noqa: E402
import db                                               # noqa: E402

SHOTS = []


def snap(d, name, hold=0.9):
    d.pump(hold)
    path = os.path.join(OUT, name + ".png")
    try:
        d.grab(path)
        SHOTS.append((name, os.path.getsize(path)))
        print("  [ok] %-28s %d bytes" % (name, os.path.getsize(path)),
              flush=True)
    except Exception as ex:                             # noqa: BLE001
        print("  [FAIL] %s: %r" % (name, ex), flush=True)


def pid_of(keyword):
    for p in db.list_persons():
        if keyword in (p.get("name") or ""):
            return p["id"]
    ps = db.list_persons()
    return ps[0]["id"] if ps else None


def pop(d, name, method, *a, **kw):
    """打开弹窗 → 等它真的显示 → 拍 → 关掉。

    wait_window 会阻塞 d.pump，所以用 after 看门狗：窗口在了才拍，
    拍完 destroy（抄 _v065/drive.py:modal 的处理）。
    """
    hold = float(kw.pop("hold", 1.2))
    close_tops(d)
    st = {"shot": False}

    def _tick():
        tops = [w for w in d.app.winfo_children()
                if w.winfo_class() == "Toplevel"]
        if not tops:
            if st["shot"]:
                return
            d.app.after(180, _tick)
            return
        if not st["shot"]:
            st["shot"] = True
            d.app.after(int(hold * 1000), _tick)
            return
        snap(d, name, hold=0.0)
        for w in tops:
            try:
                w.destroy()
            except Exception:
                pass

    d.app.after(150, _tick)
    d.safe("弹窗 " + name, getattr(d.app, method), *a)
    d.pump(0.8)
    close_tops(d)


def main():
    db.switch_account(drive.WORK["slug"])
    if not db.get_user():
        db.save_user(drive.WORK["name"], drive.WORK.get("avatar"))
    db.save_scene(db.BUILTIN_SCENE)
    print("演示库：%d 人 / %d 条关系 / 积分 %d"
          % (len(db.list_persons()), len(db.list_links()),
             db.points_balance()), flush=True)
    db.clear_user()                      # 先亮账号入口层

    rec = Rec()
    d = Demo(rec)

    # ---- 00 账号入口层 ----
    snap(d, "00_account_gate", hold=1.6)
    gate_enter(d, drive.WORK["name"])
    d.pump(1.6)

    # ---- 一、直观性 ----
    tabs(d, "tab_net")
    snap(d, "10_network_all", hold=1.8)

    scene(d, "办公")
    snap(d, "11_scene_office", hold=1.2)
    scene(d, "家庭")
    snap(d, "12_scene_family", hold=1.2)
    scene(d, db.BUILTIN_SCENE)
    d.pump(0.8)

    d.app._show_all_links.set(True)
    d.safe("全部连线", d.app._toggle_all_links)
    snap(d, "13_all_links", hold=1.6)
    d.app._show_all_links.set(False)
    d.safe("收起连线", d.app._toggle_all_links)
    d.pump(0.6)

    # 悬停单人：只展开他的关系线
    pid = pid_of("王教授")
    if pid:
        d.safe("悬停", d.app._set_relationship_focus, pid)
        snap(d, "14_hover_focus", hold=1.2)
        d.safe("取消悬停", d.app._set_relationship_focus, None)
        d.pump(0.4)

    # ---- 二、自主推断 ----
    tabs(d, "tab_conn")
    snap(d, "20_connector_page", hold=1.4)

    tabs(d, "tab_net")
    d.pump(0.6)
    pop(d, "21_llm_judge_result", "run_llm_judge", False, hold=1.6)
    tabs(d, "tab_net")
    snap(d, "22_after_judge", hold=1.8)

    # 资料卡：五维拆解在这里
    if pid:
        pop(d, "23_profile_card", "open_profile", pid, hold=2.0)

    # ---- 三、升温 ----
    tabs(d, "tab_net")
    d.side_scroll(0.0)
    snap(d, "30_sidebar_top", hold=1.2)
    d.side_scroll(0.55)
    snap(d, "31_sidebar_rank", hold=1.2)
    d.side_scroll(1.0)
    snap(d, "32_sidebar_ai", hold=1.2)
    d.side_scroll(0.0)

    tabs(d, "tab_cards")
    snap(d, "33_cards_page", hold=1.6)

    tabs(d, "tab_greet")
    d.safe("选人", d.app.gr_person.set, "王教授")
    d.safe("选节日", d.app.gr_holiday.set, "教师节")
    d.pump(0.5)
    d.safe("生成祝福", d.app.gen_greeting)
    snap(d, "34_greet_result", hold=1.6)

    tabs(d, "tab_gift")
    d.safe("选人", d.app.g_person.set, "小琳")
    d.safe("推荐礼物", d.app.on_gift_person)
    d.pump(0.6)
    ch = []
    try:
        ch = d.app.gift_tree.get_children()
    except Exception:
        pass
    if ch:
        d.app.gift_tree.selection_set(ch[0])
        d.app.gift_tree.focus(ch[0])
        d.safe("比价", d.app.on_gift_select, None)
    snap(d, "35_gift_page", hold=1.4)

    if pid:
        pop(d, "36_record_contact", "record_contact", pid, hold=1.4)

    # ---- 积分与支付 ----
    tabs(d, "tab_net")
    pop(d, "40_points_dialog", "open_points_dialog", hold=2.0)

    print("\n共 %d 张：" % len(SHOTS), flush=True)
    for n, s in SHOTS:
        print("  %-28s %7d" % (n, s), flush=True)
    try:
        d.app.destroy()
    except Exception:
        pass
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
