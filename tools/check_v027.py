"""v0.27 直线布局几何检验（无窗口、纯计算）

跑若干合成网络，输出：
  · 直线压卡条数 / 最小净空    （目标：0 / ≥ 12px）
  · 线线交叉对数                （目标：尽量 0，共享端点不算）
  · 避让最大位移                （目标：不破族序，≤ ~60px）
  · 单次布局耗时
"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netlayout  # noqa: E402
import ui  # noqa: E402

NODE_W, NODE_H = ui.NODE_W, ui.NODE_H
HW, HH = NODE_W / 2.0, NODE_H / 2.0


class Stub:
    _slot_theta = 0.3


def make_case(n, nfam, nlink, seed=7):
    """合成人物表：按族分组，族内链路为主 + 少量跨族链路"""
    rnd = random.Random(seed)
    fams = [[] for _ in range(nfam)]
    for i in range(n):
        fams[i % nfam].append(i)
    items = []
    for fi, g in enumerate(fams):
        leader = f"人{g[0]}"           # intro_by 存的是名字（与真实库一致）
        for k, idx in enumerate(g):
            items.append({"pid": f"p{idx}", "name": f"人{idx}",
                          "score": 40 + (len(g) - k) * 3 + rnd.randint(0, 20),
                          "tier": "普通", "intro_by": "" if k == 0 else leader,
                          "_p": {}})
    pids = [it["pid"] for it in items]
    links = []
    used = set()
    for fi, g in enumerate(fams):        # 族内边（真实数据的主要形态）
        leader = f"p{g[0]}"
        for idx in g[1:]:
            links.append({"id": len(links), "person_a": f"p{idx}",
                          "person_b": leader, "relation": "引荐", "score": 70})
    while len(links) < nlink:
        a, b = rnd.sample(pids, 2)
        key = tuple(sorted((a, b)))
        if key in used or a == b:
            continue
        used.add(key)
        links.append({"id": len(links), "person_a": a, "person_b": b,
                      "relation": "合作", "score": rnd.randint(40, 90)})
    return items, links


def evaluate(items, links, W=1400, H=900, label=""):
    cx, cy = W / 2.0, H / 2.0
    t0 = time.perf_counter()
    stub = Stub()
    pos = ui.App._radial_positions(stub, items, {}, W, H, cx, cy, links)
    ring = dict(pos)
    edges = [(l["person_a"], l["person_b"]) for l in links]
    t1 = time.perf_counter()
    left, mv, mt = netlayout.relax_ring(pos, edges, cx, cy, HW, HH,
                                        clear=12.0, max_out=0.45,
                                        max_tan=0.33, rounds=60)
    t2 = time.perf_counter()
    hits = netlayout.card_hits(edges, pos, HW, HH, 10.0)
    cross, pairs = netlayout.count_crossings(edges, pos)
    gap = min([netlayout.seg_rect_gap(pos[a], pos[b], pos[p], HW, HH)
               for a, b in edges for p in pos if p not in (a, b)] or [999])
    me_gap = min([netlayout.seg_gap_to_point(pos[a], pos[b], (cx, cy)) - 58.0
                  for a, b in edges] or [999])
    print(f"[{label}] n={len(pos)} e={len(edges)}")
    print(f"   压卡(<10px) {len(hits):>2} 条 / 最小卡净空 {gap:6.2f}px "
          f"/ 圆心净空 {me_gap:6.1f}px")
    print(f"   交叉 {cross:>2} 对 / 避让目标余量 {left} 条 / 位移 {mv:5.1f}px")
    print(f"   布局 {1000*(t1-t0):5.1f}ms + 避让 {1000*(t2-t1):5.1f}ms")
    if pairs:
        print(f"   交叉样例：{pairs[:4]}")
    return pos, cross, len(hits)


if __name__ == "__main__":
    cases = [
        ("小网 15人/4族", make_case(15, 4, 6)),
        ("中等 24人/5族", make_case(24, 5, 14)),
        ("大网 60人/8族", make_case(60, 8, 30)),
        ("密集 20人/3族", make_case(20, 3, 26, seed=3)),
        ("百人 100人/10族", make_case(100, 10, 60, seed=11)),
    ]
    for label, (items, links) in cases:
        evaluate(items, links, label=label)
