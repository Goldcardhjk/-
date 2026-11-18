# -*- coding: utf-8 -*-
"""v0.21 benchmark：人多卡顿定位

备份库 → 插入 50/100/200 合成人物 → cProfile 跑 refresh_cards / refresh_network
→ 打印热点 → 清理合成数据（BENCH_ 前缀）→ 恢复库。
"""
import os
import sys
import time
import shutil
import sqlite3
import cProfile
import pstats
import io
import signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

DB = os.path.join("data", "agent.db")
BAK = os.path.join("data", "_bench_bak.db")

# 先备份再动库
shutil.copy2(DB, BAK)
print("[bench] db backed up ->", BAK)

import db

db.init_db()
conn = db.get_conn()
conn.execute("DELETE FROM persons WHERE name LIKE 'BENCH_%'")
conn.commit()
conn.close()

import images_util  # noqa: E402
from ui import App  # noqa: E402


def synth(n):
    """插入 n 个合成人物（带生日，触发提醒行）"""
    import datetime
    conn = db.get_conn()
    for i in range(n):
        bd = "1990-%02d-%02d" % ((i % 12) + 1, (i % 27) + 1)
        conn.execute(
            "INSERT INTO persons (name, relation_type, closeness, sentiment_weight,"
            " intro_by, address, phone, birthday, important_dates, prefs, taboo,"
            " notes, source, avatar, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("BENCH_%03d" % i, "朋友", 0.5 + (i % 40) / 100.0,
             0.4 + (i % 40) / 100.0, "引荐%d" % (i % 5),
             "地址%d号" % i, "138%08d" % i, bd, "[]",
             '["喜好%d"]' % i, '["禁忌%d"]' % i, "备注%d" % i, "bench", None,
             datetime.datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()


def timed(label, fn, repeat=1):
    best = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        dt = (time.perf_counter() - t0) * 1000
        best = dt if best is None else min(best, dt)
    print("[bench] %-34s %8.1f ms" % (label, best))
    return best


def run(n):
    print("\n===== N = %d =====" % n)
    app = App()
    app.geometry("1600x920+4000+4000")
    app.update()
    timed("refresh_cards (first)", lambda: (app.refresh_cards(), app.update()))
    timed("refresh_cards (second, warm)", lambda: (app.refresh_cards(), app.update()))
    timed("refresh_network", lambda: (app.refresh_network(), app.update()))
    timed("refresh_combos", lambda: (app.refresh_combos(), app.update()))
    # 首页右栏（提醒列表）
    def _remind():
        import scoring
        ranked = scoring.rank_all()
        persons = {p["id"]: p for p in db.list_persons()}
        app._refresh_reminders(
            [{"_p": persons[r["id"]], "name": r["name"]} for r in ranked[:8]])
    timed("refresh_reminders(8 rows)", _remind)

    # cProfile 抓 refresh_cards 热点
    pr = cProfile.Profile()
    pr.enable()
    app.refresh_cards()
    app.update()
    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(18)
    print(s.getvalue())

    pr2 = cProfile.Profile()
    pr2.enable()
    app.refresh_network()
    app.update()
    pr2.disable()
    s2 = io.StringIO()
    ps2 = pstats.Stats(pr2, stream=s2).sort_stats("cumulative")
    ps2.print_stats(18)
    print(s2.getvalue())
    app.destroy()


def cleanup():
    conn = db.get_conn()
    conn.execute("DELETE FROM persons WHERE name LIKE 'BENCH_%'")
    conn.commit()
    conn.close()
    print("[bench] synth data cleaned")


def _sig_cleanup(*_a):
    conn = db.get_conn()
    conn.execute("DELETE FROM persons WHERE name LIKE 'BENCH_%'")
    conn.commit(); conn.close()
    shutil.copy2(BAK, DB)
    print("[bench] signal cleanup done", flush=True)
    os._exit(0)

signal.signal(signal.SIGTERM, _sig_cleanup)
signal.signal(signal.SIGINT, _sig_cleanup)

try:
    for n in (50, 100, 200):
        synth(n)
        run(n)
        cleanup()
finally:
    # 恢复原始库（防任何残留）
    try:
        app_backup = None
    except Exception:
        pass
    conn = db.get_conn()
    conn.execute("DELETE FROM persons WHERE name LIKE 'BENCH_%'")
    conn.commit()
    conn.close()
    shutil.copy2(BAK, DB)
    os.remove(BAK)
    print("[bench] db restored from backup")
