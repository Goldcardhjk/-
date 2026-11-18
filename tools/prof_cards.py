# -*- coding: utf-8 -*-
"""v0.21 精简 profile：只抓 refresh_cards / refresh_network 热点"""
import os
import sys
import shutil
import cProfile
import pstats
import io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

DB = os.path.join("data", "agent.db")
BAK = os.path.join("data", "_bench_bak.db")
shutil.copy2(DB, BAK)

import datetime
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

from ui import App  # noqa: E402

app = App()
app.geometry("1600x920+4000+4000")
app.update()

pr = cProfile.Profile()
pr.enable()
app.refresh_cards()
app.update()
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(22)
print("### refresh_cards ###")
print(s.getvalue())

pr2 = cProfile.Profile()
pr2.enable()
app.refresh_network()
app.update()
pr2.disable()
s2 = io.StringIO()
pstats.Stats(pr2, stream=s2).sort_stats("tottime").print_stats(16)
print("### refresh_network ###")
print(s2.getvalue())

app.destroy()
conn = db.get_conn()
conn.execute("DELETE FROM persons WHERE name LIKE 'BENCH_%'")
conn.commit()
conn.close()
shutil.copy2(BAK, DB)
os.remove(BAK)
print("cleaned")
