# -*- coding: utf-8 -*-
"""v0.21：单会话 200 人干净验证（排除多 App 实例缓存污染）"""
import os, sys, time, shutil, datetime, signal
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
DB = os.path.join("data", "agent.db"); BAK = DB + ".bak3"
shutil.copy2(DB, BAK)
import db
db.init_db()
conn = db.get_conn()
conn.execute("DELETE FROM persons WHERE name LIKE 'BENCH_%'")
for i in range(200):
    bd = "1990-%02d-%02d" % ((i % 12) + 1, (i % 27) + 1)
    conn.execute("INSERT INTO persons (name, relation_type, closeness, sentiment_weight, intro_by, address, phone, birthday, important_dates, prefs, taboo, notes, source, avatar, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("BENCH_%03d" % i, "朋友", 0.5 + (i % 40) / 100.0, 0.4 + (i % 40) / 100.0, "引荐%d" % (i % 5), "地址%d号" % i, "138%08d" % i, bd, "[]", '["喜好%d"]' % i, '["禁忌%d"]' % i, "备注%d" % i, "bench", None, datetime.datetime.now().isoformat(timespec="seconds")))
conn.commit(); conn.close()

import images_util
from ui import App

def cleanup(*_a):
    try:
        c = db.get_conn()
        c.execute("DELETE FROM persons WHERE name LIKE 'BENCH_%'")
        c.commit(); c.close()
    except Exception:
        pass
    shutil.copy2(BAK, DB)
    try: os.remove(BAK)
    except Exception: pass
    print("[cleanup done]", flush=True)

signal.signal(signal.SIGTERM, lambda *a: (cleanup(), os._exit(0)))

t0 = time.perf_counter()
app = App()
print("App() init: %.0f ms" % ((time.perf_counter() - t0) * 1000), flush=True)
app.geometry("1600x920+4000+4000"); app.update()

def seg(label, fn):
    t0 = time.perf_counter()
    r = fn()
    print("  %-26s %8.0f ms  cache=%d px=%.1fM" % (label, (time.perf_counter() - t0) * 1000, len(images_util._IMG_CACHE), images_util._IMG_CACHE_PX / 1e6), flush=True)
    return r

seg("cards incr #1", lambda: (app.refresh_cards(), app.update()))
seg("cards incr #2", lambda: (app.refresh_cards(), app.update()))
seg("network #1", lambda: (app.refresh_network(), app.update()))
seg("network #2", lambda: (app.refresh_network(), app.update()))
seg("combos", lambda: (app.refresh_combos(), app.update()))
seg("切卡片页", lambda: (app.nb.select(app.tab_cards), app.update()))
seg("切网络页", lambda: (app.nb.select(app.tab_net), app.update()))
app.destroy()
cleanup()
