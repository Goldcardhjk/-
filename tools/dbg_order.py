# -*- coding: utf-8 -*-
"""诊断3：真实数据环序的可达质量（长时搜索 vs 短时搜索）"""
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import math
import random
import time
import collections

import netlayout as nl

c = sqlite3.connect("dist/data/agent.db")
ps = c.execute("SELECT id,name,intro_by,closeness FROM persons").fetchall()
ls = c.execute("SELECT person_a,person_b,score FROM links").fetchall()
c.close()

ids = [p[0] for p in ps]
name = {p[0]: p[1] for p in ps}
n2p = {p[1]: p[0] for p in ps}
edges, seen = [], set()
for l in ls:
    k = tuple(sorted((l[0], l[1])))
    if k in seen:
        continue
    seen.add(k)
    edges.append((l[0], l[1]))
wts = {(l[0], l[1]): (l[2] / 100.0 if l[2] is not None else 0.5) for l in ls}
kids, roots = {}, []
for p in ps:
    par = n2p.get(p[2] or "")
    if par not in (None, p[0]):
        kids.setdefault(par, []).append(p[0])
        edges.append((par, p[0]))
        wts[(par, p[0])] = 0.8
    else:
        roots.append(p[0])

# 分数（closeness → 0~100）排序，模拟 App 的 auto 顺序
sc = {p[0]: round((p[3] or 0) * 100) for p in ps}

def dfs_order(root_list, kid_key=lambda x: -sc[x]):
    pre, seen2 = [], set()

    def v(x):
        if x in seen2:
            return
        seen2.add(x)
        pre.append(x)
        for k in sorted(kids.get(x, []), key=kid_key):
            v(k)
    for r in root_list:
        v(r)
    for i in ids:
        v(i)
    return pre


def stats(order, E=None):
    E = E or edges
    idx = {p: i for i, p in enumerate(order)}
    n = len(order)
    hist = collections.Counter(nl.ring_span(idx, a, b, n) for a, b in E)
    return dict(sorted(hist.items())), nl.cycle_crossings(order, E), \
        nl.ring_cost(order, E, wts, cross_w=8, load_w=0.3, graze_w=30)


for label, pids in (("db", ids), ("score", sorted(ids, key=lambda x: -sc[x]))):
    pre = dfs_order(sorted(roots, key=lambda x: -sc[x]))
    for ms in (1500, 4000, 9000):
        t0 = time.monotonic()
        o = nl.order_cycle(pids, edges, weights=wts, init=pre, load_w=0.3,
                           graze_w=30, cross_w=8, repair=True, max_evals=ms)
        h, cr, cost = stats(o)
        print("%-5s evals=%5d  %5.0fms 跨距%s 交叉%d 代价%.1f"
              % (label, ms, (time.monotonic() - t0) * 1000, h, cr, cost))

# 长时随机搜索（30s）看可达下界
pre = dfs_order(sorted(roots, key=lambda x: -sc[x]))
o = nl.order_cycle(sorted(ids, key=lambda x: -sc[x]), edges, weights=wts,
                   init=pre, load_w=0.3, graze_w=30, cross_w=8, repair=False)
best = list(o)
bs = stats(best)
print("初始(无修复)", bs[0], "交叉", bs[1], "代价", round(bs[2], 1))
rng = random.Random(7)
t0 = time.monotonic()
n = len(best)
while time.monotonic() - t0 < 8.0:
    cand = list(best)
    i, j = rng.randrange(n), rng.randrange(n)
    cand[i], cand[j] = cand[j], cand[i]
    s = stats(cand)
    if (s[1], s[2]) <= (bs[1], bs[2]):
        best, bs = cand, s
print("8s 搜索    ", bs[0], "交叉", bs[1], "代价", round(bs[2], 1))
idx = {p: i for i, p in enumerate(best)}
print("环序:", " → ".join(name[p] for p in best))
