# -*- coding: utf-8 -*-
"""三次 refresh 的 smooth_link key 集合 diff"""
import os, sys, time, shutil, datetime, signal
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
DB = os.path.join("data", "agent.db"); BAK = DB + ".bak2"
shutil.copy2(DB, BAK)
import db
db.init_db()
conn = db.get_conn()
conn.execute("DELETE FROM persons WHERE name LIKE 'BENCH_%'")
for i in range(100):
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

keys = []
orig = images_util.smooth_link
def spy(col, length, width, angle=0.0, dash=None, **kw):
    keys.append((col, int(length / 6) * 6, round(width * 2) / 2,
                 round(angle, 1), str(dash)))
    return orig(col, length, width, angle=angle, dash=dash, **kw)
images_util.smooth_link = spy

app = App(); app.geometry("1600x920+4000+4000"); app.update()
sets = []
for k in range(3):
    keys.clear()
    t0 = time.perf_counter()
    app.refresh_network(); app.update()
    sets.append(set(keys))
    print("run%d: %.0f ms  link_calls=%d" % (k + 1, (time.perf_counter() - t0) * 1000, len(keys)), flush=True)
print("run1 vs run2 diff:", len(sets[0] ^ sets[1]), flush=True)
print("run2 vs run3 diff:", len(sets[1] ^ sets[2]), flush=True)
app.destroy()
cleanup()
