# -*- coding: utf-8 -*-
"""v0.21：N=100 分步复现（定位 bench 卡死/爆内存点）"""
import os
import sys
import time
import shutil
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

DB = os.path.join("data", "agent.db")
BAK = os.path.join("data", "_bench_bak.db")
shutil.copy2(DB, BAK)

import db

db.init_db()
conn = db.get_conn()
conn.execute("DELETE FROM persons WHERE name LIKE 'BENCH_%'")
for i in range(100):
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
print("[n100] synth done", flush=True)

from ui import App  # noqa: E402
import images_util  # noqa: E402

t0 = time.perf_counter()
app = App()
print("[n100] App() init: %.1f ms  cache_entries=%d px=%d"
      % ((time.perf_counter() - t0) * 1000, len(images_util._IMG_CACHE),
         images_util._IMG_CACHE_PX), flush=True)
app.geometry("1600x920+4000+4000")
app.update()
print("[n100] update: cache_entries=%d px=%d"
      % (len(images_util._IMG_CACHE), images_util._IMG_CACHE_PX), flush=True)

t0 = time.perf_counter()
app.refresh_network()
app.update()
print("[n100] refresh_network: %.1f ms  cache_entries=%d px=%d"
      % ((time.perf_counter() - t0) * 1000, len(images_util._IMG_CACHE),
         images_util._IMG_CACHE_PX), flush=True)

t0 = time.perf_counter()
app.refresh_cards()
app.update()
print("[n100] refresh_cards(incr): %.1f ms" % ((time.perf_counter() - t0) * 1000), flush=True)

t0 = time.perf_counter()
app.nb.select(app.tab_cards)
app.update()
print("[n100] select cards page: %.1f ms" % ((time.perf_counter() - t0) * 1000), flush=True)

app.destroy()
conn = db.get_conn()
conn.execute("DELETE FROM persons WHERE name LIKE 'BENCH_%'")
conn.commit()
conn.close()
shutil.copy2(BAK, DB)
os.remove(BAK)
print("[n100] cleaned", flush=True)
