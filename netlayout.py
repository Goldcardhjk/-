"""关系网直线布局的几何内核（v0.27，纯计算、无 Tk 依赖）

用户诉求（2026-10-28）：「线还是直线连接，是我在操作自动布局的时候找到
不发生线和其他元素干涉的位置」。

也就是说：**线一律是直线**（不做任何绕行/外弧），由布局算法负责把卡片
挪到「直线不碰其他卡片、不擦其他线」的位置。本模块提供这套几何工具：

  seg_rect_gap    线段到卡片矩形的最小净空（负数 = 线段压到卡上）
  clip_to_rect    把线段端头裁到卡片边框（线从卡边长出来，不钻到卡下）
  seg_hit         两线段是否真相交（端点相邻的边由调用方排除）
  avoid           布局避让：在环上小幅微调卡片，消掉直线与卡片的干涉
  count_crossings 线线交叉计数（布局质量指标 / 测试断言用）
  barycenter_key  族内按邻居重心排序的键（把弦拉短，减少跨族交叉）

设计取舍：微调量有上限（切向不超过 0.35 个卡位、径向 ±0.2R），所以「同族
坐同一段弧」的族序不会被打破——避让只在卡位内挪，不重排族。
"""
import math


# 全量实际连线完成现有有界布局优化后，才判断是否以圈子代替泛化线。
CIRCLE_MIN_MEMBERS = 4
CIRCLE_MIN_EDGES = 5
CIRCLE_MIN_CROSSINGS = 2


def adaptive_circles(candidates, routes, is_generic):
    """明确圈子常驻作背景；泛化线仍须达到原几何门槛才替代。"""
    def replaceable(link, circle):
        records = link.get("records") or [link]
        return any(is_generic(record, [circle]) for record in records)

    def removed(link, circle):
        records = link.get("records") or [link]
        return all(is_generic(record, [circle]) for record in records)

    active = []
    for candidate in candidates:
        members = candidate["members"]
        if len(members) >= 2 and (candidate["kind"] == "家人" or candidate.get("explicit")):
            active.append(dict(candidate, replace_generic=False))
        if len(members) < CIRCLE_MIN_MEMBERS:
            continue
        internal, adjacency = [], {}
        seen = set()
        for route in routes:
            link = route["lnk"]
            a, b = link["person_a"], link["person_b"]
            pair = frozenset((a, b))
            if len(pair) != 2 or not pair <= members or pair in seen:
                continue
            seen.add(pair)
            internal.append((pair, route))
            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)
        # 元数据里的孤立成员和不同连通分量不能凑人数、边数。
        visited = set()
        for root in sorted(adjacency):
            if root in visited:
                continue
            component, todo = set(), [root]
            while todo:
                node = todo.pop()
                if node in component:
                    continue
                component.add(node)
                todo.extend(adjacency[node] - component)
            visited.update(component)
            if len(component) < CIRCLE_MIN_MEMBERS:
                continue
            edges = [(pair, route) for pair, route in internal if pair <= component]
            if len(edges) < CIRCLE_MIN_EDGES:
                continue
            circle = dict(candidate, members=frozenset(component))
            crossings = replaced_crossings = generic_crossings = 0
            for i, (pair, route) in enumerate(edges):
                for other_pair, other in edges[i + 1:]:
                    if pair & other_pair or not seg_hit(*route["pts"], *other["pts"]):
                        continue
                    crossings += 1
                    if replaceable(route["lnk"], circle) or replaceable(other["lnk"], circle):
                        generic_crossings += 1
                    if removed(route["lnk"], circle) or removed(other["lnk"], circle):
                        replaced_crossings += 1
            if crossings >= CIRCLE_MIN_CROSSINGS and generic_crossings:
                # 混合人对只压缩泛化语义，具体边仍在，不能计作交叉消失。
                circle.update(edge_count=len(edges), crossings=crossings,
                              generic_crossings=generic_crossings,
                              replaced_crossings=replaced_crossings, replace_generic=True)
                matching = next((c for c in active if c["kind"] == circle["kind"]
                                 and c["name"] == circle["name"]
                                 and c["members"] == circle["members"]), None)
                if matching is None:
                    active.append(circle)
                else:
                    matching.update(circle)
    for i, circle in enumerate(active, 1):
        circle["label"] = f"{i:02d} {circle['kind']} · {circle['name']}"
    return active


def card_hull(positions, hw, hh, padding=20.0):
    """成员卡片四角外扩后的单调链凸包；只用于渲染，不移动节点。"""
    points = sorted({(x + dx, y + dy) for x, y in positions
                     for dx in (-hw - padding, hw + padding)
                     for dy in (-hh - padding, hh + padding)})
    if len(points) < 3:
        return points

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    lower, upper = [], []
    for p in points:
        while len(lower) > 1 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) > 1 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def me_connections(pos, cx, cy, hw, hh, radius=58.0):
    """逐人与我直线相连，只裁边，不改变自由坐标或事实关系。"""
    center = (cx, cy)
    routes = []
    for pid, target in pos.items():
        distance = math.dist(center, target)
        start = center
        if distance > radius:
            start = (cx + (target[0] - cx) * radius / distance,
                     cy + (target[1] - cy) * radius / distance)
        end = clip_to_rect(target, center, target, hw, hh, 1.5) or target
        routes.append({'pid': pid, 'pts': [start, end]})
    return routes


def norm_angle(a):
    """角度规范化到 (-π, π]"""
    while a <= -math.pi:
        a += 2 * math.pi
    while a > math.pi:
        a -= 2 * math.pi
    return a


def seg_rect_gap(a, b, c, hw, hh):
    """线段 a→b 到「以 c 为心、hw×hh 半幅的轴对齐矩形」的最小净空。

    用矩形的支撑函数算：把矩形中心到线段的垂足方向取单位向量 u，则矩形
    在该方向上的半宽 = hw·|ux| + hh·|uy|，净空 = |垂足到中心的距离| − 该
    半宽。负数即表示线段已经压进矩形。
    """
    ax, ay = a
    bx, by = b
    cxp, cyp = c
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        t = 0.0
    else:
        t = ((cxp - ax) * dx + (cyp - ay) * dy) / l2
        t = max(0.0, min(1.0, t))
    qx, qy = ax + dx * t, ay + dy * t
    vx, vy = cxp - qx, cyp - qy
    d = math.hypot(vx, vy)
    if d < 1e-6:
        return -min(hw, hh)
    ux, uy = vx / d, vy / d
    return d - (hw * abs(ux) + hh * abs(uy))


def min_gap(pos, edges, pids, hw, hh):
    """所有关系线到「非端点卡片」的最小净空（负数＝线已经压进卡里）

    交给 relax 之后必须复验这一项。只验「穿线数」不够：relax 为了修间距会
    同时挪半径和角度，实测能把两张卡挤到净空 −124（卡片叠在一起），而穿线
    数仍是 0，于是一路放行到守卫才被拦下，白烧一秒。
    """
    g = 999.0
    for a, b in edges:
        for c in pids:
            if c == a or c == b:
                continue
            gg = seg_rect_gap(pos[a], pos[b], pos[c], hw, hh)
            if gg < g:
                g = gg
    return g


def seg_gap_to_point(a, b, p):
    """线段 a→b 到点 p 的距离（用于避让圆心「我」）"""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))


def chord_r(a, b, ang, cen=(0.0, 0.0)):
    """线段 a→b 在「从 cen 出发、方向角 ang」那条射线上的**半径**（不穿返回 0）

    v0.36 用：判断一条关系线在「中间那张卡」的角度上是走到卡片外侧
    （半径更大 → 不碰它的辐射线），还是从卡片内侧穿过去（半径更小 →
    一定和圆心→这张卡的辐射线交叉）。见 relax_free 的 lane 项。

    射线 P = t·d（d 单位向量），线段 a + s·e（e = b−a）。
    t·d = a + s·e，两边叉乘 e 得 t = (a×e)/(d×e)，叉乘 d 得 s = (a×d)/(d×e)。
    |d| = 1，所以 t 就是半径。注意 a/b 要先减掉圆心——布局系里圆心一般
    不在原点，忘了减会让「弦半径」整体偏出去几百像素（这个 bug 真发生过，
    症状是 lane 项算出 880px 却同时判定「穿过了」）。
    """
    dx, dy = math.cos(ang), math.sin(ang)
    ax, ay = a[0] - cen[0], a[1] - cen[1]
    ex, ey = (b[0] - cen[0]) - ax, (b[1] - cen[1]) - ay
    den = dx * ey - dy * ex
    if abs(den) < 1e-12:
        return 0.0
    t = (ax * ey - ay * ex) / den
    s = (ax * dy - ay * dx) / den
    if t <= 0.0 or s < 0.0 or s > 1.0:
        return 0.0
    return t


def clip_to_rect(a, b, c, hw, hh, margin=1.5):
    """从卡片中心 a 沿 a→b 方向，裁到卡片边框（再外推 margin）。

    a 是卡片中心（矩形内部），所以取所有与四条边交点里最小的 t 就是出
    边点。返回 (x, y)，退化时返回 None。
    """
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return None
    ts = []
    if abs(dx) > 1e-9:
        for sx in (cx - hw, cx + hw):
            t = (sx - ax) / dx
            if 0.0 < t < 1.0:
                py = ay + dy * t
                if abs(py - cy) <= hh + 0.5:
                    ts.append(t)
    if abs(dy) > 1e-9:
        for sy in (cy - hh, cy + hh):
            t = (sy - ay) / dy
            if 0.0 < t < 1.0:
                px = ax + dx * t
                if abs(px - cx) <= hw + 0.5:
                    ts.append(t)
    if not ts:
        return None
    t = min(ts) + margin / L
    if t >= 1.0:
        return None
    return (ax + dx * t, ay + dy * t)


def _orient(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def slot_loads(order, edges, pos, cx, cy, reach=1.5):
    """每条「缝」被多少条直线穿过 —— 决定这个缝该留多宽。

    缝 = 环上相邻两张卡之间的那段角度，用一条从圆心射出去的射线代表。
    统计时**含端点卡自身的关系线**：一个人若有 3 条关系线，就有 3 条线从他
    两侧的缝里"挤出去"，那才是视觉拥挤的主因（只统计穿环的弦会漏掉这个）。

    返回与 order 等长的列表，loads[i] = 第 i 张卡与第 i+1 张卡之间那条缝的
    穿越条数（顺序与 order 一致，最后一项是 order[-1] 与 order[0] 之间）。
    """
    n = len(order)
    if not n:
        return []
    pset = set(order)
    E = [(a, b) for a, b in edges
         if a in pset and b in pset and a != b]
    if not E:
        return [0] * n
    R = (sum(math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in order) / n) or 1.0
    ang = [math.atan2(pos[p][1] - cy, pos[p][0] - cx) for p in order]
    loads = []
    for i in range(n):
        a0 = ang[i]
        a1 = ang[(i + 1) % n]
        if a1 <= a0:
            a1 += 2.0 * math.pi
        mid = (a0 + a1) / 2.0
        p1 = (cx, cy)
        p2 = (cx + R * reach * math.cos(mid), cy + R * reach * math.sin(mid))
        k = 0
        for u, v in E:
            if seg_hit(pos[u], pos[v], p1, p2):
                k += 1
        loads.append(k)
    return loads


def place_uneven(order, loads, hw, cx=0.0, cy=0.0, base_gap=34.0,
                 per_line=11.0, exp=0.6, min_gap=26.0, min_r=0.0):
    """按「缝流量」分配角间距：线多的缝宽、线少的缝窄。

    为什么非做不可：18 人时卡位弧长 = 2πR/n ≈ 180px，卡宽 154px，缝只剩
    **26px** —— 而实测有缝要挤 4 条线（≈需要 50px）。卡宽固定、周长固定，
    均分卡位在数学上就没法给线让出通道。

    做法（不是简单把环放大，那样只均摊、不解决"局部 4 条"）：
      ① 每条缝的需求宽 w_i = base_gap + per_line·max(0, load_i − 1)；
      ② 环半径解自洽方程 2πR = n·卡宽 + Σw_i（卡占的弧长随 R 变化，迭代几轮）；
      ③ 卡的实际占角 = 2·asin(hw/R)（弦长对应角，比 hw/R 准），缝占角 = w_i/R；
      ④ 从正上方起笔，卡放卡段中间、缝留缝段。

    角度不再严格均分，但半径统一、环序不变 —— 视觉上仍是整齐的一圈，只是
    "哪里线多，哪里的卡自动松开一点"，这正是用户手摆时的做法。

    返回 (pos 字典, R, 每段角间距列表)。
    """
    n = len(order)
    if not n:
        return {}, 0.0, []
    gap_w = [max(min_gap,
                 base_gap + per_line * (max(0.0, k - 1.0) ** exp))
             for k in loads]
    # ② 自洽方程：卡占角随 R 变化，缝占角又要等于 w_i/R —— 即
    #      2π = n·2asin(hw/R) + Σw_i/R   ⇔   R·(2π − 2n·asin(hw/R)) = Σw_i
    #    f(R) 在可行域内单调递增，二分求根（比不动点迭代稳，不会来回震）。
    total_w = sum(gap_w)
    lo = hw / max(1e-6, math.sin(math.pi / n)) * 1.0001   # 卡刚好首尾相接的下界
    hi = max(lo * 2.0, (n * 2.0 * hw + total_w) / math.pi + 400.0)
    for _ in range(60):
        mid = (lo + hi) / 2.0
        free = 2.0 * math.pi - n * 2.0 * math.asin(min(1.0, hw / mid))
        if mid * free < total_w:
            lo = mid
        else:
            hi = mid
    R = (lo + hi) / 2.0
    # min_r：不低于「按画布适配」的半径。4 个人时缝宽需求很小，二分只会解出
    # 「卡刚好首尾相接」的 R≈130（比卡还窄），环会被缩成一坨；画布够大就该
    # 铺开（这也是 _ring_radius 里 fit_r 的用意）。
    R = max(R, min_r)
    card_ang = 2.0 * math.asin(min(1.0, hw / R))
    free = max(1e-9, 2.0 * math.pi - n * card_ang)
    gaps = [w / R for w in gap_w]
    tot = sum(gaps)
    if tot > 0:                                    # 二分残差归一，保证首尾正好闭合
        gaps = [g * free / tot for g in gaps]
    pos = {}
    ang = -math.pi / 2.0
    for i, pid in enumerate(order):
        c = ang + card_ang / 2.0
        pos[pid] = (cx + R * math.cos(c), cy + R * math.sin(c))
        ang += card_ang + gaps[i]
    return pos, R, gaps


def seg_hit(a, b, c, d):
    """线段 ab 与 cd 是否真正相交（不共端点的情形由调用方筛掉）

    先用包围盒快速排除：盒子不相交的段对不可能相交，这一步结果与直接
    求四个方向叉积完全等价，只是省掉绝大多数无意义的叉积运算。
    """
    if (max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0])
            or max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1])):
        return False
    d1 = _orient(a[0], a[1], b[0], b[1], c[0], c[1])
    d2 = _orient(a[0], a[1], b[0], b[1], d[0], d[1])
    d3 = _orient(c[0], c[1], d[0], d[1], a[0], a[1])
    d4 = _orient(c[0], c[1], d[0], d[1], b[0], b[1])
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def count_crossings(edges, pos, skip_shared=True):
    """统计线线交叉数；共享端点的边不算（它们在卡片处汇流，是正常形态）"""
    n = 0
    pairs = []
    for i in range(len(edges)):
        a, b = edges[i]
        if a not in pos or b not in pos:
            continue
        for j in range(i + 1, len(edges)):
            c, d = edges[j]
            if c not in pos or d not in pos:
                continue
            if skip_shared and ({a, b} & {c, d}):
                continue
            if seg_hit(pos[a], pos[b], pos[c], pos[d]):
                n += 1
                pairs.append((edges[i], edges[j]))
    return n, pairs


def card_hits(edges, pos, hw, hh, clear=0.0):
    """统计「直线压到非端点卡片」的条数（净空 < clear 即算干涉）"""
    bad = []
    for a, b in edges:
        if a not in pos or b not in pos:
            continue
        for p, pc in pos.items():
            if p == a or p == b:
                continue
            g = seg_rect_gap(pos[a], pos[b], pc, hw, hh)
            if g < clear:
                bad.append((a, b, p, g))
    return bad


def barycenter_key(members, pos, edges, cx, cy, center_ang):
    """族内排序键：每个成员的邻居重心角（相对本族中心的夹角）

    把「只连到别的族」的卡片排到朝向那个族的一侧，弦就短了、交叉也少了。
    没有连线的成员保持自己在族内的相对位置（键 = 自身相对角）。
    """
    nb = {}
    for a, b in edges:
        nb.setdefault(a, []).append(b)
        nb.setdefault(b, []).append(a)
    keys = {}
    for p in members:
        own = norm_angle(math.atan2(pos[p][1] - cy, pos[p][0] - cx) - center_ang)
        others = [q for q in nb.get(p, []) if q in pos]
        if not others:
            keys[p] = own
            continue
        vals = [norm_angle(math.atan2(pos[q][1] - cy, pos[q][0] - cx)
                           - center_ang) for q in others]
        keys[p] = sum(vals) / len(vals)
    return keys


def ring_span(idx, a, b, n):
    """环上两点之间的短弧跨距（以卡位为单位）"""
    d = abs(idx[a] - idx[b])
    return min(d, n - d)


def order_loads(order, edges):
    """纯环序算每条缝的流量（不依赖坐标）：loads[i] = 第 i 张卡与 i+1 之间
    那条缝被几条弦穿过。

    一条弦从卡 i 顺时针跨 span 个卡位到卡 j，会依次穿过 span 条缝
    （i, i+1, …, i+span−1），每条各记 1。所以 Σloads = Σspan —— 总量只取决
    于边集，与环序无关，于是「最小化 Σloads²」就是「把长弦均匀摊到各条缝」。

    用户诉求（2026-10-28）：「继续做这个是很重要的一个功能」——底部两张卡
    的缝里挤了 4~5 条线。根因是跨距² 只惩罚"单条弦太长"，不惩罚"多条长弦
    扎堆在同一段弧上"，于是局部流量冲到 8。
    """
    n = len(order)
    if not n:
        return []
    idx = {p: i for i, p in enumerate(order)}
    load = [0] * n
    seen = set()
    for a, b in edges:
        if a not in idx or b not in idx or a == b:
            continue
        i, j = idx[a], idx[b]
        key = (i, j) if i < j else (j, i)
        if key in seen:                   # 同一对人的重复记录只算一次
            continue
        seen.add(key)
        span = (j - i) % n
        if span > n - span:               # 走短弧那一侧
            i, j = j, i
            span = n - span
        for k in range(span):
            load[(i + k) % n] += 1
    return load


def ring_cost(order, edges, weights=None, cross_w=0.0, load_w=0.0,
              graze_w=0.0):
    """环序代价 = Σ 权重 × 跨距² + cross_w × 弦交叉对数
                + load_w × Σ缝流量² + graze_w × Σ擦卡权重

    为什么用平方：一条跨 4 个卡位的弦（16 分）比两条跨 2 个的弦（8 分）
    更亏。于是布局会自动把「一个引荐人带四五个孩子」的星形摊到引荐人两侧，
    而不是全堆在一边、让四五条线横穿半个圆。

    graze_w 项（v0.33）是这里最关键的一项，实测数据推翻了「跨距越小越好」
    的直觉：弦跨 k 个卡位时，弦到中间卡片的净空 ≈ R·(1−cos(kθ/2)) − 卡半径。
    R=602、θ=20°、卡半径 66（半高 40 + 目标净空 26）时：

    | 跨距 k | R(1−cos(kθ/2)) | 净空        | 实测（真实 18 人） |
    |--------|-----------------|-------------|-------------------|
    | 1      | 没有中间卡       | 无          | +111 ~ +148       |
    | 2      | 36px            | **−30px**   | **−17 ~ −50**     |
    | 3      | 81px            | +15px       | −4 ~ +21（擦边）  |
    | 4      | 141px           | +75px       | 不出现            |
    | 5+     | 更大，且穿过圆心空域 | 充足    | 不出现            |

    所以跨 2 的边必擦卡、跨 3 的边擦边，而跨 1 和跨 ≥4 都干净。**环序里
    只要没有跨 2 的边，环就能保持正圆、一张卡都不用挪。** 这比事后把卡往
    外推（v0.31/v0.32 的做法）好得多——推卡会把正圆推成参差不齐的团。

    G 的形状就是上表：k=1 → 0，k=2 → 1.0，k=3 → 0.35，k=4 → 0.10，k≥5 → 0。

    **权重必须和「跨距²」同一量级（v0.34 修正）**：v0.33 把 graze_w 设成 120，
    局部搜索于是宁可把一条边拉到跨 8（+64 分）也要躲开跨 2（省 120 分）。
    结果是正圆一张卡都没挪，但线全从圆心附近横穿——用户看到的就是「线乱飞」。
    现在 graze_w=30、G(2)=1.0、G(3)=0.35，于是：
      跨 2 = 4 + 30 = 34 ／ 跨 3 = 9 + 10.5 ≈ 20 ／ 跨 8 = 64 + 0 = 64
    环序会主动把跨 2 换成跨 3（只多 5 分弦长，省下 19 分擦卡），但绝不肯用
    跨 8 去换——这正是想要的：线基本贴着环、少数几条跨 3 由「推卡 10px」解决。

    交叉项是必要的补丁：光看跨距，两条各自很短的弦可能恰好互穿（实测真实
    数据里「李博士-陈编辑」跨 2 个卡位绕过去，正好被王教授的两条长弦切开）。

    load_w 项（v0.32）治的是另一种病：跨距² 只让「单条弦变短」，不管「好几
    条长弦挤在同一段弧」。实测真实 18 人里最优跨距分布下仍有缝被 8 条线穿
    过，看起来就是「线全堵在两卡之间」。
    """
    idx = {p: i for i, p in enumerate(order)}
    n = len(order)
    s = 0.0
    for a, b in edges:
        if a not in idx or b not in idx:
            continue
        d = ring_span(idx, a, b, n)
        w = 1.0 if not weights else weights.get((a, b), 1.0)
        s += w * d * d
        if graze_w > 0 and d >= 2:
            s += graze_w * w * _GRAZE[min(d, 6)]
    if cross_w > 0:
        s += cross_w * cycle_crossings(order, edges)
    if load_w > 0:
        ld = order_loads(order, edges)
        s += load_w * sum(x * x for x in ld)
    return s


# 跨距 → 擦卡权重（下标即跨距，0/1 不罚；见 ring_cost 的推导表）
_GRAZE = (0.0, 0.0, 1.0, 0.35, 0.10, 0.0, 0.0)


def cycle_crossings(order, edges):
    """纯环序上的弦交叉对数（不用坐标：两条弦的端点交错就相交）"""
    idx = {p: i for i, p in enumerate(order)}
    n = len(order)
    E = [(idx[a], idx[b]) for a, b in edges if a in idx and b in idx]
    cnt = 0
    for i in range(len(E)):
        pa, pb = E[i]
        la = (pb - pa) % n
        if not la:
            continue
        for j in range(i + 1, len(E)):
            pc, pd = E[j]
            if pa in (pc, pd) or pb in (pc, pd):
                continue                  # 共享端点＝汇流，不算交叉
            if (((pc - pa) % n < la) != ((pd - pa) % n < la)):
                cnt += 1
    return cnt


def order_cycle(pids, edges, weights=None, init=None, passes=8, n_limit=80,
                load_w=0.0, graze_w=30.0, cross_w=8.0, repair=True,
                spans_budget=1500, max_evals=6000):
    """求「让关系弦尽量短、且不在同一条缝里扎堆」的环序（v0.30 / v0.32）

    替代 v0.27 的「引荐树前序硬拼」：前序只保证父子相邻，但一个引荐人带
    多个孩子时，除两侧外的孩子必然被甩到远端——弦只能横穿圆圈。人一旦
    既有引荐关系又有合作/家人关系（真实数据就是这样），前序就撑不住了。

    做法：DFS 前序做初值（保住引荐树大结构），再做局部搜索：
      ① 两两交换卡位；② 单点搬到别处。只接受代价下降的，直到不动为止。
    孤立点（没有任何连线）最后均匀插进空档，不挤成一堆。

    load_w > 0 时把「Σ缝流量²」也算进代价（v0.32）。只压跨距时，长弦会被
    赶到同一段弧上——跨距²加起来一样小，但视觉上「线全堵在两卡之间」。

    graze_w > 0 时把「跨距 2 的弦必擦卡」也罚进去（v0.33 的关键）。实测：
    跨 1 净空 +111，跨 2 净空 −50~−17，跨 3 净空 −4~+21。所以真正要躲的
    是跨距 2，而不是「越短越好」。躲开之后环能保持正圆，不必把卡往外推。

    n > n_limit 时退化为「只搬有连线的点、只跟邻近位置交换」，避免
    O(n²·E) 在两百人网络上卡住。
    """
    pids = list(pids)
    n = len(pids)
    if n < 3:
        return pids
    pset = set(pids)
    seen = set()
    E = []
    for a, b in edges:
        if a not in pset or b not in pset or a == b:
            continue
        key = (a, b) if a < b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        E.append((a, b))
    if not E:
        return pids
    order = [p for p in (init or pids) if p in pset]
    have = set(order)
    order += [p for p in pids if p not in have]
    if len(order) != n:                      # init 有重复/漏项时兜底
        order = order[:n]
        order += [p for p in pids if p not in set(order)]
    connected = {p for e in E for p in e}
    big = n > n_limit
    # 交叉项只在边数不多时开（O(E²) 评估，人多了局部搜索会变慢）
    cw = cross_w if len(E) <= 80 else 0.0
    # 缝流量项：Σload² 的评估是 O(E·span)，人多时等于再乘一个 n，太贵；
    # 而且百人环上跨距本来就短、峰值流量不会失控，所以小网才开。
    lw = load_w if (n <= 32 and len(E) <= 80) else 0.0
    # 擦卡项：评估只需跨距（O(E)），比交叉项便宜得多，大图也能开
    gw = graze_w if n <= 240 else 0.0

    def cost(seq):
        return ring_cost(seq, E, weights, cross_w=cw, load_w=lw, graze_w=gw)

    best = cost(order)

    def try_swap(i, j):
        nonlocal best
        if i == j:
            return False
        if big and order[i] not in connected and order[j] not in connected:
            return False
        order[i], order[j] = order[j], order[i]
        c = cost(order)
        if c < best - 1e-9:
            best = c
            return True
        order[i], order[j] = order[j], order[i]
        return False

    for _ in range(passes):
        improved = False
        for i in range(n):
            for j in range(i + 1, n):
                if big and j - i > 6 and (n - j + i) > 6:
                    continue
                if try_swap(i, j):
                    improved = True
        for i in range(n):
            if big and order[i] not in connected:
                continue
            p = order[i]
            placed = False
            order.remove(p)
            # 大图只在原位附近 ±6 个卡位试插入（v0.34）：原来无条件扫全部
            # n−1 个位置，等于每轮 n² 次代价评估——200 人实测 37s 全耗在这里。
            # 邻近插入也足够：长距离搬家等价于若干次相邻对换，交给上面的交换
            # 循环去做就行。
            if big:
                j0, j1 = max(0, i - 6), min(n - 2, i + 5)
            else:
                j0, j1 = 0, n - 2
            for j in range(j0, j1 + 1):
                order.insert(j, p)
                c = cost(order)
                if c < best - 1e-9:
                    best = c
                    improved = True
                    placed = True
                    break
                order.pop(j)
            if not placed:
                order.insert(i, p)
        if not improved:
            break
    # 孤立点：均匀插进空档（它们不参与代价，但挤在一堆会难看）
    iso = [p for p in order if p not in connected]
    if iso:
        for p in iso:
            order.remove(p)
        m = len(iso)
        base_len = len(order)
        for k, p in enumerate(iso):
            at = int(round((k + 0.5) * base_len / m)) if base_len else 0
            order.insert(min(at, len(order)), p)
    # ── 修复阶段（v0.33，v0.34 修正目标）：局部搜索容易停在「还有几条跨距 2」
    #    的坑里，因为跨距 2 和交叉常常此消彼长（压一个必然拱起另一个）。
    #    这里用「随机交换 + 字典序接受」硬啃：目标序列 (交叉对数, 总代价)。
    #    v0.33 曾把「跨2条数」放进字典序第二位，结果是它不惜一切代价消跨 2，
    #    把边拉到跨 8/9，线全从圆心横穿——用户看到的就是这个。现在只看总
    #    代价（里面已经含 graze_w×G(k) 的擦卡罚分），所以修复阶段只会做
    #    「划算」的交换。
    if repair and gw > 0 and n <= 40 and len(E) <= 60:
        # 评估预算按边的平方折算：sc() 里 cycle_crossings 是 O(E²)，E=54 时
        # 一次评估就要 3000 次比较，固定 6000 次评估会变成 5 秒。这里按
        # 「总工作量约 250 万次基本操作」反算次数，E=15 → 6000 次（够收敛），
        # E=54 → 约 850 次（几十毫秒），两边都不卡。
        _ev = int(2_500_000 / max(1, len(E) ** 2))
        order = _repair_graze(order, E, n, weights, cw, lw, gw,
                              budget=max(200, min(spans_budget, _ev)),
                              max_evals=max(200, min(max_evals, _ev)))
    return order


def _graze_count(seq, E, n):
    """跨距恰好 2 的边数（这种弦必擦中间卡，见 ring_cost 的表）"""
    idx = {p: i for i, p in enumerate(seq)}
    k = 0
    for a, b in E:
        if ring_span(idx, a, b, n) == 2:
            k += 1
    return k


def _repair_graze(order, E, n, weights, cw, lw, gw, budget=1500,
                  seeds=(1, 4, 7), rounds=2, max_evals=6000):
    """字典序随机交换修复：先保「零交叉」，再压总代价。

    **为什么交叉必须排在第一位**：v0.32 及以前「零交叉」是硬保证，不能被
    为了消擦卡而牺牲——实测放开就会换出几条穿心的弦。所以交叉数作为字典序
    第一位，只在交叉不变差时接受交换。

    **为什么第二位不再放「跨2条数」**（v0.34）：那两个指标此消彼长，把它们
    放进字典序会让搜索认定「跨 8 也比跨 2 好」，于是正圆换来了满天长弦。
    现在第二位是 ring_cost（含 graze_w×G(k)），擦卡与弦长按同一把尺子折算，
    交换只在真正划算时才被接受。

    多起点（seeds）：随机交换的运气差别极大，按固定序列轮流试、取最好的一个。

    **预算是「评估次数」而不是墙钟秒数**（v0.34 修）：v0.33 用
    `time.monotonic() > deadline` 截断，于是同一批数据在机器忙/闲时会在
    不同位置停下——实测同一份 18 人数据两次运行会给出不同的环序（一次
    跨距 {1:11,2:2}、一次 {1:12,2:3}），卡片位置随之变化。用户明确要求
    「每次保存我调整的位置」，布局本身必须可复现，所以改成固定次数的
    随机交换：只要输入相同，结果必然逐位相同。单次评估实测 0.038ms，
    6000 次 ≈ 0.23s，代价可接受。
    """
    import random as _rnd
    best, best_seq = None, list(order)
    evals = 0
    for sd in seeds:
        if evals >= max_evals:
            break
        rng = _rnd.Random(sd)
        seq = list(order)

        def sc(s):
            return (cycle_crossings(s, E),
                    ring_cost(s, E, weights, cross_w=cw, load_w=lw, graze_w=gw))

        cur = sc(seq)
        evals += 1
        loc, loc_seq = cur, list(seq)
        for _ in range(rounds):
            improved = False
            for _ in range(budget):
                if evals >= max_evals:
                    break
                evals += 1
                i, j = rng.randrange(n), rng.randrange(n)
                if i == j:
                    continue
                seq[i], seq[j] = seq[j], seq[i]
                v = sc(seq)
                if v <= cur:            # 允许「持平」的侧移：这是走出平台期的关键
                    cur = v
                    if v < loc:
                        loc, loc_seq = v, list(seq)
                        improved = True
                else:
                    seq[i], seq[j] = seq[j], seq[i]
            if not improved:
                break
            seq = list(loc_seq)
            cur = loc
        if best is None or loc < best:
            best, best_seq = loc, list(loc_seq)
    return best_seq


def relax_ring(pos, edges, cx, cy, hw, hh, clear=12.0, max_out=0.45,
               max_tan=0.33, rounds=60, slack=0.9, pinned=()):
    """单环卡位的受限避让：径向为主、切向为辅，逐卡独立让位（不做邻域平滑）。

    为什么还需要它（relax_outward v0.30 的残留问题）：
    所有卡严格同半径时，「跨 2 个卡位的弦」中点距圆心只有 R·cos(θ/2)=0.94R，
    与中间那张卡（中心在 R）的径向差仅 0.06R≈23px，小于卡半高 40px —— 也
    就是**几何上必然切到中间卡**（跨 3 卡位更深）。实测真实 18 人初始 9 处
    压卡，relax_outward 只能沿半径外推且上限 22%，推到顶格仍有 7 处残留。

    本函数给每张卡两个受限自由度：
      · 径向 dr ∈ [-10%R, +max_out·R]      —— 让卡退到外圈，弦从空档穿过；
      · 切向 dt ∈ [-max_tan·卡位弧长, +…]  —— 让卡沿圆周错开半个卡位，
        弦就能从两卡的缝里过去（这正是用户手摆时做的事）。
    每轮只对被压的卡投票「沿弦法向躲开 need」，按被压次数取平均后累加，
    幅度受上面两条限幅约束，所以：环序/角序结构不变、不会互相拉扯成乱麻。

    刻意**不做**邻域平滑：试过 0.5/0.25/0.25 三点平滑，位移被扩散到本来
    不需要动的邻卡，邻卡一动又产生新压卡，正反馈把整环推大 45%（半径
    739 vs 516）且仍有 5 处残留。逐卡独立反而单调收敛到 0。

    返回 (剩余压卡数, 最大外移, 最大切向位移)。
    """
    fixed = set(pinned)
    pids = [p for p in pos if p not in fixed]
    if len(pids) < 3:
        return (len(card_hits(edges, pos, hw, hh, clear)), 0.0, 0.0)
    n = len(pids)
    # 每次迭代是 O(边 × 卡) 的矩形净空计算；100 人 90 边时 60 轮要算 54 万次，
    # 会拖到秒级。大图降轮数 + 收紧外推幅度（人多时环周长本来就宽裕，
    # 跨 2 卡位的弦离中间卡也更远，不需要推那么多）。
    if n > 60:
        rounds = min(rounds, 22)
        max_out = min(max_out, 0.30)
    elif n > 32:
        rounds = min(rounds, 36)
    seq = sorted(pids, key=lambda p: math.atan2(pos[p][1] - cy, pos[p][0] - cx))
    R = (sum(math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in seq) / n) or 1.0
    th = 2.0 * math.pi / n
    base_a = {p: math.atan2(pos[p][1] - cy, pos[p][0] - cx) for p in pids}
    base_r = {p: max(1.0, math.hypot(pos[p][0] - cx, pos[p][1] - cy))
              for p in pids}
    lim_t = max_tan * th * R            # 切向限幅（弧长）
    # 每张卡的两个切向余量：朝后邻 / 朝前邻各能挪多少而不撞上它。
    # 不加这个约束时，相邻两卡会各自向中间挤 max_tan=0.33 卡位（≈59px），
    # 合计 118px > 卡宽 154 的间隙预算，实测底部 Vivi 与阿凯贴成一张。
    allow_f, allow_b = {}, {}
    for i, c in enumerate(seq):
        nxt = seq[(i + 1) % n]
        prv = seq[(i - 1) % n]
        arc_f = ((base_a[nxt] - base_a[c]) % (2 * math.pi)) * R
        arc_b = ((base_a[c] - base_a[prv]) % (2 * math.pi)) * R
        spare_f = arc_f - (2 * hw + 14.0)
        spare_b = arc_b - (2 * hw + 14.0)
        allow_f[c] = max(0.0, spare_f * 0.45)
        allow_b[c] = max(0.0, spare_b * 0.45)
    dr = {p: 0.0 for p in pids}
    dt = {p: 0.0 for p in pids}
    for _ in range(rounds):
        vote_r = dict.fromkeys(pids, 0.0)
        vote_t = dict.fromkeys(pids, 0.0)
        cnt = dict.fromkeys(pids, 0)
        worst = 0.0
        for a, b in edges:
            if a not in pos or b not in pos or a == b:
                continue
            ax, ay = pos[a]
            bx, by = pos[b]
            ux, uy = bx - ax, by - ay
            L = math.hypot(ux, uy) or 1.0
            ux, uy = ux / L, uy / L
            nx, ny = -uy, ux                    # 弦法向：卡该躲的方向
            for c in seq:
                if c == a or c == b:
                    continue
                g = seg_rect_gap(pos[a], pos[b], pos[c], hw, hh)
                if g >= clear:
                    continue
                need = clear - g
                worst = max(worst, need)
                cnt[c] += 1
                dx, dy = pos[c][0] - ax, pos[c][1] - ay
                s = dx * nx + dy * ny
                sg = 1.0 if s >= 0 else -1.0     # 卡当前在弦的哪一侧 → 往同侧躲
                vx, vy = nx * sg * need * 1.15, ny * sg * need * 1.15
                rr = math.hypot(pos[c][0] - cx, pos[c][1] - cy) or 1.0
                erx, ery = (pos[c][0] - cx) / rr, (pos[c][1] - cy) / rr
                vote_r[c] += vx * erx + vy * ery
                vote_t[c] += vx * -ery + vy * erx
        if worst <= 1e-9:
            break
        for c in pids:
            k = cnt[c]
            if not k:
                continue
            dr[c] = max(-0.10 * base_r[c],
                        min(max_out * base_r[c], dr[c] + vote_r[c] / k * slack))
            nt = dt[c] + vote_t[c] / k * slack
            cap_pos = min(lim_t, allow_f[c])     # 朝前邻方向能挪多少
            cap_neg = min(lim_t, allow_b[c])     # 朝后邻方向能挪多少
            dt[c] = max(-cap_neg, min(cap_pos, nt))
            r = base_r[c] + dr[c]
            a = base_a[c] + dt[c] / R
            pos[c] = (cx + r * math.cos(a), cy + r * math.sin(a))
    left = len(card_hits(edges, pos, hw, hh, clear))
    mr = max([abs(v) for v in dr.values()] or [0.0])
    mt = max([abs(v) for v in dt.values()] or [0.0])
    return left, mr, mt


def relax_clear(pos, segs, cx, cy, hw, hh, clear=30.0, sep=14.0,
                r_lo=0.84, r_hi=1.14, sweeps=6, lam=260.0,
                smooth=20.0, smooth_free=45.0, step_coarse=12.0,
                step_fine=3.0, pinned=()):
    """给线让出真正的通道：单卡沿自己的射线一维坐标下降（v0.33）

    与 relax_ring 的区别（为什么不继续用它）：
    · relax_ring 是「力式」累积——多条边同时给同一张卡投票，票数相加后
      一次位移，容易冲过头，下一轮又被推回来，来回拉锯；净空目标一提高
      就开始抖。这里是**逐卡候选点全量评估 + 只接受代价下降**，代价单调
      下降，数学上不会振荡。
    · relax_ring 只看人物关系线。实际还画着「我 → 每个人」的辐射线：卡
      往内挪就会挡住别人的辐射线。这里把辐射线（`segs` 里端点为 None）
      同样当障碍算进去。
    · 加了「卡与卡间隙」项，避免为了躲线把两张卡挤成一张。

    代价 = Σ(净空不足)² + 3·Σ(卡间间隙不足)²
          + lam·(半径偏移/R)² + smooth·Σ(max(0,邻位半径差−smooth_free)/100)²

    弧度不变（只沿射线滑），所以环序、角序、大致圆形的观感都保留；卡片
    可有 16% 内收 / 14% 外凸的自由度——「不一定要都在一个圆上」，但整体
    仍是一圈。返回 (剩余违规数, 最大径向位移)。

    v0.34 收窄了幅度与邻位宽容：v0.33 给到 −20%/+35% 时，一张卡能被推到
    +35%（210px），邻位突跳上限 80px 也不管——实测出现「6 张卡外凸 33~87px、
    邻位突跳 87px」的参差团。现在 ±14%（约 ±80px）、邻位差超过 45px 就开始
    罚，实测真实 18 人：净空 26、半径 567~641、邻位突跳 ~40px，也就是一圈
    ±8% 的轻微起伏，肉眼仍是「圆」，只是线有了可穿的缝。

    规模自适应（v0.34）：本算法是 O(卡² × 段) 的，实测 100 人要 22s、200 人
    要 113s——不能这样上线。按人数降轮数/放宽步长/跳过收尾（细节见下方
    n>60 分支），100 人回到秒级以内，18 人不受影响（仍是 8 轮 + 2px 细修）。
    """
    fixed = set(pinned)
    pids = [p for p in pos if p not in fixed]
    n = len(pids)
    if n < 3:
        return len(card_hits([s for s in segs if s[0] and s[1]], pos, hw, hh, clear)), 0.0
    # 规模自适应：人多时环周长本来就宽裕（跨 2 卡位的弦离中间卡更远），
    # 不必细抠 2px。宁可粗一点也别让人按一次布局等半分钟。
    polish = 4
    if n > 48:
        polish = 0                        # 收尾是 O(卡²×段)，大图直接不跑
    if n > 60:
        sweeps = min(sweeps, 4)
        step_coarse = max(step_coarse, 16.0)
        step_fine = max(step_fine, 6.0)
    if n > 120:
        sweeps = min(sweeps, 2)
        step_coarse = max(step_coarse, 28.0)
        step_fine = max(step_fine, 10.0)
    base_r = {p: math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pids}
    base_a = {p: math.atan2(pos[p][1] - cy, pos[p][0] - cx) for p in pids}
    Rb = max(base_r.values()) or 1.0
    rr = dict(base_r)
    P = {p: (float(pos[p][0]), float(pos[p][1])) for p in pids}
    seq = sorted(pids, key=lambda p: base_a[p])
    nb = {}
    for i, p in enumerate(seq):
        nb[p] = (seq[i - 1], seq[(i + 1) % n])

    # 段几何（每轮重建：一次扫描里坐标变化很小，误差可忽略）
    def seg_geo():
        out = []
        for a, b in segs:
            pa = (cx, cy) if a is None else P.get(a, pos.get(a, (cx, cy)))
            pb = (cx, cy) if b is None else P.get(b, pos.get(b, (cx, cy)))
            out.append((a, b, pa, pb,
                        min(pa[0], pb[0]), max(pa[0], pb[0]),
                        min(pa[1], pb[1]), max(pa[1], pb[1])))
        return out

    def pen(c, cxy, geo):
        """只与 c 有关的代价项（其余项不随 c 变）"""
        tot = 0.0
        x, y = cxy
        for a, b, pa, pb, x0, x1, y0, y1 in geo:
            if a == c or b == c:
                continue
            if x < x0 - hw - clear or x > x1 + hw + clear:
                continue
            if y < y0 - hh - clear or y > y1 + hh + clear:
                continue
            g = seg_rect_gap(pa, pb, cxy, hw, hh)
            if g < clear:
                e = clear - g
                tot += e * e
        for d in pids:
            if d == c:
                continue
            dx = x - P[d][0]
            dy = y - P[d][1]
            if abs(dx) >= 2 * hw + sep or abs(dy) >= 2 * hh + sep:
                continue
            e = min(2 * hw + sep - abs(dx), 2 * hh + sep - abs(dy))
            tot += 3.0 * e * e
        return tot

    def anch(c, r, r_nb):
        t = lam * ((r - base_r[c]) / Rb) ** 2
        for d in r_nb:
            gap = abs(r - rr[d])
            if gap > smooth_free:
                e = (gap - smooth_free) / 100.0
                t += smooth * e * e
        return t

    lo_all = max(60.0, hw + 8)
    for _ in range(sweeps):
        geo = seg_geo()
        moved = 0
        for c in seq:
            r0 = base_r[c]
            lo = max(lo_all, r0 * r_lo)
            hi = r0 * r_hi
            a = base_a[c]
            ca, sa = math.cos(a), math.sin(a)
            best = rr[c]
            cur = pen(c, P[c], geo) + anch(c, rr[c], nb[c])
            best_v = cur
            k = lo
            while k <= hi + 1e-6:               # ① 粗扫
                v = pen(c, (cx + k * ca, cy + k * sa), geo) + anch(c, k, nb[c])
                if v < best_v - 1e-9:
                    best, best_v = k, v
                k += step_coarse
            if best != rr[c]:                    # ② 细修
                k = max(lo, best - step_coarse)
                top = min(hi, best + step_coarse)
                while k <= top + 1e-6:
                    v = pen(c, (cx + k * ca, cy + k * sa), geo) + anch(c, k, nb[c])
                    if v < best_v - 1e-9:
                        best, best_v = k, v
                    k += step_fine
            if abs(best - rr[c]) > 1e-6:
                rr[c] = best
                P[c] = (cx + best * ca, cy + best * sa)
                moved += 1
        if not moved:
            break
    left = 0
    for a, b, pa, pb, x0, x1, y0, y1 in seg_geo():
        for c in pids:
            if c == a or c == b:
                continue
            if seg_rect_gap(pa, pb, P[c], hw, hh) < clear:
                left += 1

    # ── 收尾：按「违规条数」再啃一遍（v0.34）────────────────────────────
    # 上面那轮下降的目标是 Σ(净空不足)²。平方和有个固有毛病：它可能停在
    # 「两处各差 16px」而不是「一处差 20px」，而用户看到的是「有没有线压卡」
    # 这个布尔量。实测真实 18 人：平方和收敛后仍有 2 处 9~11px，把上限从
    # +14% 抬到 +26% 才归零——不是推不动，是它觉得「再推不划算」。
    # 这一段只认条数：逐卡试「把半径推到刚好清掉自己那几条」的最小值，
    # 只要全局违规条数下降就接受。单调、确定性、最多 4 轮。
    for _ in range(polish):
        if left == 0:
            break
        geo2 = seg_geo()

        def _bad_of(p, geo):
            k2 = 0
            for a2, b2, pa2, pb2, x02, x12, y02, y12 in geo:
                if a2 == p or b2 == p:
                    continue
                if seg_rect_gap(pa2, pb2, P[p], hw, hh) < clear:
                    k2 += 1
            return k2
        best_move = None                  # (全局违规条数, 卡, 目标半径)
        for c in seq:
            a = base_a[c]
            ca, sa = math.cos(a), math.sin(a)
            hi = base_r[c] * r_hi
            lo = max(lo_all, base_r[c] * r_lo)
            r_keep = rr[c]
            k = lo
            while k <= hi + 1e-6:
                P[c] = (cx + k * ca, cy + k * sa)
                if _bad_of(c, geo2) == 0:
                    gt = seg_geo()
                    tot = 0
                    for a2, b2, pa2, pb2, x02, x12, y02, y12 in gt:
                        for q in pids:
                            if q == a2 or q == b2:
                                continue
                            if seg_rect_gap(pa2, pb2, P[q], hw, hh) < clear:
                                tot += 1
                    if tot < left and (best_move is None or tot < best_move[0]):
                        best_move = (tot, c, k)
                    break
                k += 2.0
            P[c] = (cx + r_keep * ca, cy + r_keep * sa)
        if best_move is None:
            break
        left, c, k = best_move
        rr[c] = k
        P[c] = (cx + k * math.cos(base_a[c]), cy + k * math.sin(base_a[c]))

    for p in pids:
        pos[p] = P[p]
    return left, max([abs(rr[p] - base_r[p]) for p in pids] or [0.0])


def relax_free(pos, segs, cx, cy, hw, hh, clear=26.0, sep=14.0,
               r_lo=0.62, r_hi=1.42, da_slots=0.40, sweeps=8,
               step_r=16.0, step_a=0.024, lam_r=380.0, lam_a=140.0,
               jump=80.0, jump_w=6.0, r_min=0.0, obs=(), polish=3,
               floor=0.0, fan_w=0.0, fan_min_deg=26.0,
               lane_w=0.0, lane=12.0, pinned=(), r_cap=None):
    """卡片不再被钉在一个圆上：二维自由找位（v0.35）

    用户诉求（2026-10-28）：「不用让所有人物卡都在一个圆上，都在一个圆上
    就没法解决线的干涉问题」。

    **为什么 v0.34 的「沿射线一维滑动」还是不够**——这是几何上的死结：
    卡片被钉在固定角度上时，唯一能躲开一条弦的办法就是沿自己那条射线前后
    挪。可跨 2 个卡位的弦离圆心是 R·cosθ，中间那张卡的内缘是 R−HH。
    R=600、θ=20°、HH=40 时弦在 564、卡内缘在 560——**怎么沿射线挪都还
    在弦上，只是换了个高度**。一维移动解不了一个二维的问题。

    所以必须同时给两个自由度：
      · Δr ∈ [−38%, +42%]R —— 整张卡缩进里头一层，把外圈那一环空出来
        给长弦穿（这是「缩进去」比「推出去」更有用的地方：卡缩到 0.6R，
        它的辐射线就只占 0~0.6R，0.6R 以外那一圈在它的角度上是空的）；
      · Δθ ≤ ±0.4 个卡位 —— 从弦的一头绕到另一头，钻进两张卡之间的缝。

    代价（逐卡只算与自己有关的项，其余都随卡片不动而是常量）：
        Σ(净空不足)²   +   3·Σ(卡间/障碍间隙不足)²
      + lam_r·(Δr/R)²  + lam_a·(Δθ/卡位角)²
      + jump_w·Σ(max(0, 邻位半径差−jump)/100)²
    锚定项不是「不许动」，是「同等效果下少动」——它只在多个同样干净的
    解里决定选哪个，不阻断去更远地方找干净位。

    接受规则沿用 relax_clear：逐卡全量评估候选格点、只接受代价下降，于是
    单调收敛、不振荡、完全确定（无随机、不依赖墙钟）。粗扫 + 细修两段。
    收尾再按「违规条数」啃一遍：平方和会停在「两处各差 10px」，而用户看
    的是「有没有线压卡」这个布尔量。

    返回 (剩余违规条数, 最大位移)。
    """
    fixed = set(pinned)
    pids = [p for p in pos if p not in fixed]
    n = len(pids)
    if n < 3:
        return len(card_hits([(s[0], s[1]) for s in segs if s[0] and s[1]],
                             pos, hw, hh, clear)), 0.0

    base_r = {p: math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pids}
    base_a = {p: math.atan2(pos[p][1] - cy, pos[p][0] - cx) for p in pids}
    Rb = max(base_r.values()) or 1.0
    slot = 2.0 * math.pi / n
    R = dict(base_r)
    A = dict(base_a)
    P = {p: (float(pos[p][0]), float(pos[p][1])) for p in pids}
    seq = sorted(pids, key=lambda p: base_a[p])
    nb = {p: (seq[i - 1], seq[(i + 1) % n]) for i, p in enumerate(seq)}
    lo_min = max(r_min, hw + 8.0)

    # ── v0.36：两条「视觉通道」代价 ────────────────────────────────
    # ① 扇形张开（fan）：同一张卡上如果两条边出射方向夹角太小（实测真实
    #    数据里「王教授→李博士」和「王教授→赵总」只差 14.3°），画出来就是
    #    两根线贴着并排走、末端并成一个尖，用户看到的「糊成一团」。
    # ② 通道（lane）：一条跨过若干卡位的关系线，在中间那些卡的角度上
    #    半径若小于「那张卡的外缘 + lane」，就说明它从卡片**内侧**穿过——
    #    而辐射线正好从圆心长到那张卡，于是两线必交叉。想让线走缝，就得
    #    让中间那些卡缩进去（或两端推出去），使弦从它们外侧绕过去。
    Erel = [(a, b) for a, b in segs if a is not None and b is not None]
    inc_e = {c: [] for c in pids}
    fan_out = {c: [] for c in pids}
    for e in Erel:
        for k in (0, 1):
            c = e[k]
            if c in inc_e:
                inc_e[c].append(e)
                fan_out[c].append(e[1 - k])
    fan_min = math.radians(fan_min_deg)
    mid_map = {}
    as_mid = {c: [] for c in pids}
    ANG = {p: base_a[p] for p in pids}

    def rbox(c):
        lo = max(lo_min, base_r[c] * r_lo)
        hi = max(lo + 20.0, base_r[c] * r_hi)
        if r_cap:
            cp = r_cap.get(c)
            if cp is not None and cp < hi:
                hi = cp
                if lo > hi:                  # 上限比下限还低：只能贴着上限
                    lo = hi
        return lo, hi

    def abox(c):
        d = da_slots * slot
        return base_a[c] - d, base_a[c] + d

    def xy(r, a):
        return (cx + r * math.cos(a), cy + r * math.sin(a))

    def seg_one(a, b):
        pa = (cx, cy) if a is None else P.get(a, pos.get(a, (cx, cy)))
        pb = (cx, cy) if b is None else P.get(b, pos.get(b, (cx, cy)))
        return (a, b, pa, pb,
                min(pa[0], pb[0]), max(pa[0], pb[0]),
                min(pa[1], pb[1]), max(pa[1], pb[1]))

    def geo_now():
        return [seg_one(a, b) for a, b in segs]

    def pen(c, cxy, geo, idxs):
        """只与 c 有关的代价项（其余卡不动 → 是常量，不必算）"""
        tot = 0.0
        x, y = cxy
        for i in idxs:
            a, b, pa, pb, x0, x1, y0, y1 = geo[i]
            if a == c or b == c:
                continue
            if x < x0 - hw - clear or x > x1 + hw + clear:
                continue
            if y < y0 - hh - clear or y > y1 + hh + clear:
                continue
            g = seg_rect_gap(pa, pb, cxy, hw, hh)
            if g < clear:
                e = clear - g
                tot += e * e
                if floor > 0.0 and g < floor:
                    # 硬下限：目标可以够不着（46px 在密图上不可能），但绝不
                    # 允许为了「平方和更小」把某条线从 26 拖到 15——用户看
                    # 的是「有没有线压着卡」这个布尔量，不是总平方和。
                    e2 = floor - g
                    tot += 60.0 * e2 * e2
        # 卡间间隙：候选格点有几百个，逐个比全部 n 张卡就是 O(n²) 的坑
        # （100 人要跑几分钟）。这里按格子分桶，只看周围 3×3 格。
        gx = int(math.floor((x - cx) / _cell))
        gy = int(math.floor((y - cy) / _cell))
        if _use_grid:
            near_cards = []
            for ix in (gx - 1, gx, gx + 1):
                for iy in (gy - 1, gy, gy + 1):
                    near_cards.extend(_grid.get((ix, iy), ()))
        else:
            near_cards = pids
        for d in near_cards:
            if d == c:
                continue
            qx, qy = P[d]
            dx, dy = x - qx, y - qy
            if abs(dx) >= 2 * hw + sep or abs(dy) >= 2 * hh + sep:
                continue
            e = min(2 * hw + sep - abs(dx), 2 * hh + sep - abs(dy))
            tot += 3.0 * e * e
        for ox, oy, ohw, ohh in obs:            # 图例等固定障碍
            dx, dy = x - ox, y - oy
            e = min(ohw + hw + sep - abs(dx), ohh + hh + sep - abs(dy))
            if e > 0.0:
                tot += 3.0 * e * e
        # ⑤ 扇形张开：同卡的边不要贴着并排走
        if fan_w > 0.0:
            outs = fan_out.get(c)
            if outs and len(outs) > 1:
                dirs = [math.atan2(P[o][1] - y, P[o][0] - x) for o in outs]
                for i in range(len(dirs)):
                    for j in range(i + 1, len(dirs)):
                        dd = abs(dirs[i] - dirs[j]) % (2.0 * math.pi)
                        dd = min(dd, 2.0 * math.pi - dd)
                        if dd < fan_min:
                            g = (fan_min - dd) * 57.29577951308232
                            tot += fan_w * g * g
        # ⑥ 通道：本条线在中间卡的角度上必须走到卡片外侧
        if lane_w > 0.0:
            for e in inc_e.get(c, ()):
                o = e[1] if e[0] == c else e[0]
                po = P[o]
                for m in mid_map.get(e, ()):
                    rc = chord_r(cxy, po, ANG[m], (cx, cy))
                    if rc <= 0.0:
                        continue
                    d = (R[m] + hh + lane) - rc
                    if d > 0.0:
                        tot += lane_w * d * d
            for e in as_mid.get(c, ()):
                rc = chord_r(P[e[0]], P[e[1]],
                             math.atan2(y - cy, x - cx), (cx, cy))
                if rc <= 0.0:
                    continue
                d = (math.hypot(x - cx, y - cy) + hh + lane) - rc
                if d > 0.0:
                    tot += lane_w * d * d
        return tot

    _cell = max(2.0 * hw + sep, 2.0 * hh + sep)
    _grid = {}
    _use_grid = n > 30                    # 小图直接线性扫更快（免得建表）

    def rebuild_grid():
        _grid.clear()
        for d in pids:
            k = (int(math.floor((P[d][0] - cx) / _cell)),
                 int(math.floor((P[d][1] - cy) / _cell)))
            _grid.setdefault(k, []).append(d)


    def anch(c, r, a):
        dr = (r - base_r[c]) / Rb
        da = (a - base_a[c]) / slot
        tot = lam_r * dr * dr + lam_a * da * da
        for d in nb[c]:
            gap = abs(r - R[d])
            if gap > jump:
                e = (gap - jump) / 100.0
                tot += jump_w * e * e
        return tot

    # 规模自适应：本算法每个候选点都要过一遍「附近的段 + 附近的卡」，
    # 大图靠网格分桶也只能压到秒级。人多时环周长本来就宽裕，不必细抠
    # 几像素——降轮数、放宽步长。18 人不受影响。
    if n > 48:
        sweeps = min(sweeps, 4)
        step_r = max(step_r, 34.0)
        step_a = max(step_a, 0.05)
        polish = min(polish, 1)
    if n > 120:
        sweeps = min(sweeps, 2)
        step_r = max(step_r, 52.0)
        step_a = max(step_a, 0.07)
        polish = 0

    for _ in range(sweeps):
        geo = geo_now()
        if lane_w > 0.0:
            # 角向次序随卡片移动会变（切向自由度 ±0.4 卡位），每轮重算
            # 「谁夹在谁的中间」——这张卡缩进去之后，那条弦就不算穿过它了
            for p in pids:
                ANG[p] = math.atan2(P[p][1] - cy, P[p][0] - cx)
                as_mid[p] = []
            for e in Erel:
                a, b = e
                d = (ANG[b] - ANG[a]) % (2.0 * math.pi)
                mids = []
                for q in pids:
                    if q == a or q == b:
                        continue
                    t = (ANG[q] - ANG[a]) % (2.0 * math.pi)
                    inside = (0.0 < t < d) if d <= math.pi else (t > d)
                    if inside:
                        mids.append(q)
                mid_map[e] = mids
                for q in mids:
                    as_mid[q].append(e)
        if _use_grid:
            rebuild_grid()
        # 每轮重建「这张卡可能碰到哪几段」：把 O(段) 的扫描缩到 O(几段)，
        # 候选格点有几百个，不预先筛的话单次布局要上秒。
        near = {}
        for c in pids:
            lo, hi = rbox(c)
            alo, ahi = abox(c)
            xs, ys = [], []
            for rr_, aa_ in ((lo, alo), (lo, ahi), (hi, alo), (hi, ahi)):
                xs.append(cx + rr_ * math.cos(aa_))
                ys.append(cy + rr_ * math.sin(aa_))
            bx0, bx1 = min(xs) - hw - clear, max(xs) + hw + clear
            by0, by1 = min(ys) - hh - clear, max(ys) + hh + clear
            idx = []
            for i, (a, b, pa, pb, x0, x1, y0, y1) in enumerate(geo):
                if x1 < bx0 or x0 > bx1 or y1 < by0 or y0 > by1:
                    continue
                idx.append(i)
            near[c] = idx
        moved = 0
        for c in seq:
            lo, hi = rbox(c)
            alo, ahi = abox(c)
            idxs = near[c]
            best_r, best_a = R[c], A[c]
            best_v = pen(c, P[c], geo, idxs) + anch(c, R[c], A[c])
            r = lo
            while r <= hi + 1e-6:               # ① 粗扫
                a = alo
                while a <= ahi + 1e-9:
                    v = pen(c, xy(r, a), geo, idxs) + anch(c, r, a)
                    if v < best_v - 1e-9:
                        best_r, best_a, best_v = r, a, v
                    a += step_a
                r += step_r
            r = max(lo, best_r - step_r)        # ② 细修
            while r <= min(hi, best_r + step_r) + 1e-6:
                a = max(alo, best_a - step_a)
                while a <= min(ahi, best_a + step_a) + 1e-9:
                    v = pen(c, xy(r, a), geo, idxs) + anch(c, r, a)
                    if v < best_v - 1e-9:
                        best_r, best_a, best_v = r, a, v
                    a += step_a * 0.35
                r += step_r * 0.35
            if abs(best_r - R[c]) > 1e-6 or abs(best_a - A[c]) > 1e-9:
                R[c], A[c] = best_r, best_a
                P[c] = xy(best_r, best_a)
                moved += 1
        if not moved:
            break

    def seg_vc(t):
        a, b, pa, pb = t[0], t[1], t[2], t[3]
        k = 0
        for q in pids:
            if q == a or q == b:
                continue
            if seg_rect_gap(pa, pb, P[q], hw, hh) < clear:
                k += 1
        return k

    geo = geo_now()
    left = sum(seg_vc(t) for t in geo)
    inc = {c: [i for i, s in enumerate(segs) if s[0] == c or s[1] == c]
           for c in pids}

    # ── 收尾：只认「违规条数」────────────────────────────────────────
    # 上面的平方和目标是停不下去的典型：两处各差 10px（代价 200）比一处差
    # 20px（代价 400）更「便宜」，可用户看到的是「有两处线压着卡」。这段
    # 逐卡试候选点，只要全局条数下降就接受；只重算与这张卡相关的段，所以
    # 单次评估是 O(几段 × 卡数) 而不是 O(段 × 卡)。
    if polish and left and n <= 48:
        for _ in range(polish):
            if left == 0:
                break
            improved = False
            for c in seq:
                lo, hi = rbox(c)
                alo, ahi = abox(c)
                rk, ak = R[c], A[c]
                old = sum(seg_vc(geo[i]) for i in inc[c])
                best = None                  # (总条数, r, a, [新段几何])
                for dr in (-110.0, -78.0, -52.0, -34.0, -20.0, -10.0,
                           0.0, 10.0, 20.0, 34.0, 52.0, 78.0, 110.0):
                    r = min(hi, max(lo, rk + dr))
                    for da in (-0.34, -0.24, -0.16, -0.10, -0.05, 0.0,
                               0.05, 0.10, 0.16, 0.24, 0.34):
                        a = min(ahi, max(alo, ak + da * slot))
                        P[c] = xy(r, a)
                        new, tlist = 0, []
                        for i in inc[c]:
                            t = seg_one(segs[i][0], segs[i][1])
                            tlist.append((i, t))
                            new += seg_vc(t)
                        tot = left - old + new
                        if tot < left and (best is None or tot < best[0]):
                            best = (tot, r, a, tlist)
                        P[c] = xy(rk, ak)
                if best is not None:
                    left, R[c], A[c] = best[0], best[1], best[2]
                    P[c] = xy(best[1], best[2])
                    for i, t in best[3]:
                        geo[i] = t
                    improved = True
            if not improved:
                break

    left = sum(seg_vc(t) for t in geo)
    disp = max([math.hypot(P[p][0] - pos[p][0], P[p][1] - pos[p][1])
                for p in pids] or [0.0])
    for p in pids:
        pos[p] = P[p]
    return left, disp


def _mid_pairs(edges, ang):
    """每条边「角度夹在两端之间」的中间卡（走短弧）——v0.36

    这些卡才是这条弦会横穿的对象。角向次序一变就要重算，所以每次布局
    都重新算一遍，不缓存。
    """
    out = {}
    for a, b in edges:
        d = (ang[b] - ang[a]) % (2.0 * math.pi)
        mids = []
        for q in ang:
            if q == a or q == b:
                continue
            t = (ang[q] - ang[a]) % (2.0 * math.pi)
            if (0.0 < t < d) if d <= math.pi else (t > d):
                mids.append(q)
        out[(a, b)] = mids
    return out


def spoke_hits(pos, edges, cx, cy, hh, lane=12.0):
    """关系线横穿「我→某人」辐射线的处数 + 最紧净空

    几何事实：辐射线从圆心长到卡片为止。一条弦在中间那张卡的角度上若走
    到「卡片外缘之外」，就碰不到它的辐射线；走到卡片内侧，则必然交叉。
    所以判据是 chord_r(a,b,θm) ≥ R_m + hh + lane。
    """
    ang = {p: math.atan2(pos[p][1] - cy, pos[p][0] - cx) for p in pos}
    R = {p: math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pos}
    hits, tight = 0, 1e9
    for e in edges:
        for q in _mid_pairs([e], ang)[e]:
            rc = chord_r(pos[e[0]], pos[e[1]], ang[q], (cx, cy))
            if rc <= 0.0:
                continue
            d = rc - (R[q] + hh + lane)
            tight = min(tight, d)
            if d < 0.0:
                hits += 1
    return hits, (0.0 if tight > 1e8 else tight)


def place_by_arc(order, lvl, rout, cx, cy, hw, gap=30.0, start=0.0, hh=None):
    """按「每张卡占同样弧长」分配角度：角宽 ∝ 1/半径

    等分 360° 时内圈卡（0.6R）的弧间距只剩 121px，而卡片本身 154px 宽，
    必然重叠。真实需求是**每张卡占的弧长一样**，所以内圈卡要占更大角度。
    总角宽超过 360° 时整体放大外圈半径。

    `hh` 是 v0.38 的实验参数：卡片是轴对齐矩形，摆到斜 45° 处真正占地方
    的是 `2(hw·|sinθ| + hh·|cosθ|)` ≈ 270px，比按 224 算多 46px。按投影
    宽度分角能让卡片间距更稳（18 人最小矩形间距 4px → 32px），但真实画布
    上它反而把 18 人的线到卡净空从 34.8 压到 26.1——因为卡散开了，留给线
    的通道跟着变窄。26 人那条路最后没走通，所以这里**不用它**，只在离线
    实验里传 hh 试过。留着这行参数是为了以后别再从零写一遍。
    """
    w = {p: (2.0 * hw + gap) / (rout * lvl[p]) for p in order}
    total = sum(w.values())
    if total > 2.0 * math.pi:
        k = total / (2.0 * math.pi)
        rout *= k
        w = {p: v / k for p, v in w.items()}
    pos, a = {}, start
    for p in order:
        a += w[p] / 2.0
        pos[p] = (cx + rout * lvl[p] * math.cos(a),
                  cy + rout * lvl[p] * math.sin(a))
        a += w[p] / 2.0
    return pos, rout


def span_cost(order, edges, idx=None):
    """环序的「长弦代价」＝ Σ 每条边跨过的卡位数（v0.37）

    一条跨 k 个卡位的弦，必然横穿夹在中间的 k 张卡的辐射线——「长线横穿
    辐射线」的全部来源就是这个和。压缩它等于从源头减少要躲的弦，比事后
    让卡片东躲西藏有效得多。环上距离取短弧，所以「整体反过来排」代价不变。
    """
    n = len(order)
    if idx is None:
        idx = {p: i for i, p in enumerate(order)}
    tot = 0
    for a, b in edges:
        i, j = idx.get(a), idx.get(b)
        if i is None or j is None:
            continue
        d = abs(i - j)
        tot += min(d, n - d) - 1
    return tot


def order_spans(order, edges, passes=8, budget=0):
    """重排环序，把长弦压短（v0.37）

    初始环序来自数据库录入顺序，和图结构无关，于是随手就有跨半圈的弦
    （实测 22 人里跨 8~9 个卡位的一大把）。这种弦几乎贴着圆心穿过去，它
    上面每一根辐射线都躲不开——**任何分层都无解**。所以人多时得先排环序。

    邻域只用「交换两个人」和「把一个人挪到别处」两种：代价只看环上短弧
    距离，对整体反转不敏感，2-opt 反转在这里是恒等变换，没用。
    每轮取全局最优一步、代价严格下降，因此天然收敛且完全确定（无随机、
    不看墙钟）。人少时通常第一轮就无处可降，代价可忽略。
    """
    n = len(order)
    if n < 5 or not edges:
        return list(order)
    seq = list(order)

    def cost_of(s):
        return span_cost(s, edges, {p: k for k, p in enumerate(s)})

    cur = cost_of(seq)
    for _ in range(passes):
        best_v, best_mv = cur, None
        for i in range(n):                       # ① 交换
            for j in range(i + 1, n):
                seq[i], seq[j] = seq[j], seq[i]
                v = cost_of(seq)
                seq[i], seq[j] = seq[j], seq[i]
                if v < best_v:
                    best_v, best_mv = v, ("swap", i, j)
        for i in range(n):                       # ② 挪位
            for j in range(n):
                if i == j:
                    continue
                x = seq.pop(i)
                seq.insert(j, x)
                v = cost_of(seq)
                seq.pop(j)
                seq.insert(i, x)
                if v < best_v:
                    best_v, best_mv = v, ("move", i, j)
        if best_mv is None or best_v >= cur:
            break
        if best_mv[0] == "swap":
            i, j = best_mv[1], best_mv[2]
            seq[i], seq[j] = seq[j], seq[i]
        else:
            i, j = best_mv[1], best_mv[2]
            seq.insert(j, seq.pop(i))
        cur = best_v
        if cur <= 0:
            break
    return seq


def auto_layers(pos, edges, cx, cy, hw, hh, gap=30.0, clear=26.0,
                floor=22.0, obs=(), lane=12.0, max_inner=7,
                generations=(), pinned=()):
    """自动布局总入口：四道尝试，逐级放宽（v0.37）

      ① 两层（v0.36）——18~20 人时第一枪就中，行为和 v0.36 逐位相同；
      ② 逐卡径向上限——人稍多、内圈容量不够时靠它，半径连续、不设上限张数；
      ③ 先排环序再逐卡上限——长弦太多时，从源头把弦压短；
      ④ 先排环序再两层——兜底。

    任何一级失败都不改 pos，最后由调用方走 v0.35 的二维自由布局。
    返回 dict，或 None。
    """
    if generations:
        if not pinned:
            order = sorted(pos, key=lambda p: math.atan2(pos[p][1] - cy,
                                                        pos[p][0] - cx))
            sequence = order_spans(order, edges)
            radius = max(220.0, len(pos) * (2 * hw + gap) / (2 * math.pi))
            for k, p in enumerate(sequence):
                angle = -math.pi / 2 + 2 * math.pi * k / len(sequence)
                pos[p] = (cx + radius * math.cos(angle),
                          cy + radius * math.sin(angle))
        polish_geometry(pos, edges, cx, cy, hw, hh, clear=clear,
                        generations=generations, pinned=pinned)
        unique = sorted({tuple(sorted(e)) for e in edges})
        return {"inner": [], "cross": count_crossings(unique, pos)[0],
                "gap": min_gap(pos, unique, list(pos), hw, hh)}
    _dbg = bool(__import__("os").environ.get("NL_DEBUG"))

    def _log(tag, ok):
        if _dbg:
            _o = sorted(pos, key=lambda p: math.atan2(pos[p][1] - cy,
                                                     pos[p][0] - cx))
            print("[dbg] %-14s %s  环序跨度代价 %d"
                  % (tag, "中" if ok else "放弃", span_cost(_o, edges)),
                  flush=True)

    # ① 两层
    info = layer_refine(pos, edges, cx, cy, hw, hh, gap=gap,
                        max_inner=max_inner, clear=clear, lane=lane,
                        floor=floor, obs=obs)
    _log("① 两层", info)
    if info is not None:
        return info

    pids = list(pos)
    n = len(pids)
    if n < 5 or len(edges) < 2:
        return None

    # ② 逐卡径向上限
    info = cap_refine(pos, edges, cx, cy, hw, hh, gap=gap, clear=clear,
                      lane=lane, floor=floor, obs=obs)
    _log("② 逐卡上限", info)
    if info is not None:
        return info

    # ③④ 先排环序
    ang0 = {p: math.atan2(pos[p][1] - cy, pos[p][0] - cx) for p in pids}
    order = sorted(pids, key=lambda p: ang0[p])
    seq = order_spans(order, edges)
    if _dbg:
        print("[dbg]   重排环序代价 %d → %d"
              % (span_cost(order, edges), span_cost(seq, edges)), flush=True)
    if seq == order:
        return None
    start = ang0[order[0]]
    R0 = sum(math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pids) / n
    R0 = max(R0, n * (2.0 * hw + gap) / (2.0 * math.pi))
    trial = {}
    for k, p in enumerate(seq):
        a = start + 2.0 * math.pi * k / n
        trial[p] = (cx + R0 * math.cos(a), cy + R0 * math.sin(a))

    info = cap_refine(trial, edges, cx, cy, hw, hh, gap=gap, clear=clear,
                      lane=lane, floor=floor, obs=obs)
    _log("③ 重排+上限", info)
    if info is None:
        # 半径上限连排都排不开 → 这个规模放不下，④ 也没必要花 0.7s 去试
        _E = [(a, b) for a, b in edges if a in pos and b in pos and a != b]
        if rad_limits(seq, _E, R0, cx, cy, hw, hh, lane=lane,
                      start=start) is None:
            if _dbg:
                print("[dbg]   结构性放不下（半径要退进下限）→ 直接交回 v0.35",
                      flush=True)
            return None
        trial2 = dict(trial)
        info = layer_refine(trial2, edges, cx, cy, hw, hh, gap=gap,
                            max_inner=max_inner, clear=clear, lane=lane,
                            floor=floor, obs=obs)
        _log("④ 重排+两层", info)
        if info is None:
            return None
        trial = trial2
    for p in pids:
        pos[p] = trial[p]
    return info


def _adj_map(edges):
    A = {}
    for a, b in edges:
        A.setdefault(a, set()).add(b)
        A.setdefault(b, set()).add(a)
    return A


def _split_sub(pids, edges):
    """连通生长切分：从度数最小的点沿边走，长到一半就切（v0.40）

    递归分块的第一步。没用谱二分 / Kernighan-Lin：它们优化的是「跨割边
    最少」，而人脉图里跨割边本来就少；真正要保证的是**组内连通**。切出
    「序号交错」的两组，组内边全断，等于把长弦从组间搬到组内，白切一场
    （实测跨度 66 → 126，越切越乱）。
    """
    S = set(pids)
    A = _adj_map([(a, b) for a, b in edges if a in S and b in S])
    if not A or sum(len(v) for v in A.values()) == 0:
        return None
    half = len(pids) // 2
    start = min(pids, key=lambda p: (len(A.get(p, ())), p))
    seenS = {start}
    q = [start]
    while q and len(seenS) < half:
        u = q.pop(0)
        for v in sorted(A.get(u, ())):
            if v in seenS:
                continue
            seenS.add(v)
            q.append(v)
            if len(seenS) >= half:
                break
    for p in pids:                        # 图不连通时补齐
        if len(seenS) >= half:
            break
        seenS.add(p)
    g1 = [p for p in pids if p in seenS]
    g2 = [p for p in pids if p not in seenS]
    if len(g1) < 2 or len(g2) < 2:
        return None
    return g1, g2


def group_partition(pids, edges, max_group=8, depth=0):
    """递归切到每组不超过 max_group 人（v0.40）"""
    pids = list(pids)
    if len(pids) <= max_group or depth >= 6:
        return [pids]
    r = _split_sub(pids, edges)
    if r is None:
        return [pids]
    a, b = r
    if min(len(a), len(b)) < 3:           # 切出两三张的孤儿不划算，不切
        return [pids]
    return (group_partition(a, edges, max_group, depth + 1)
            + group_partition(b, edges, max_group, depth + 1))


def block_layout(pids, edges, cx, cy, hw, hh, gap=30.0, clear=26.0,
                 floor=22.0, lane=12.0, max_group=8, a_ell=400.0,
                 b_ell=128.0, obs=()):
    """递归分块布局（v0.40）：人多时切成几簇，每簇自占一块小圆

    为什么必须分块：环上每张卡只有左右 2 个邻居位，30 张卡 = 60 个位。
    关系线一旦超过人数（边/人 > 1），多出来的边只能跨圈成为横穿圆心的
    长弦；而圆心附近辐射线最密——纯环形在这里是死结，v0.37 的逐卡半径
    上限、三档下沉、30 种参数组合全部试过，26 人以上无一例零穿线。

    分块后：簇内用 v0.38 那套（auto_layers 在 8 人小图上本来就零干涉），
    簇间只剩少量桥线，且桥线从簇与簇之间的空隙穿过，不用挤圆心。

    只切出一组时（人少，或图本身就连成一串）直接等价于 auto_layers，
    18/20 人的行为逐位不变。
    返回 dict(pos, groups, centers, index, inner, gap, cross, k) 或 None。
    """
    pids = list(pids)
    E = [(a, b) for a, b in edges if a in pids and b in pids and a != b]
    groups = group_partition(pids, E, max_group=max_group)

    if len(groups) <= 1:
        q = {}
        n = max(1, len(pids))
        R = max(hw * 2.0, n * (2.0 * hw + gap) / (2.0 * math.pi))
        for i, p in enumerate(pids):
            a = -math.pi / 2 + 2.0 * math.pi * i / n
            q[p] = (cx + R * math.cos(a), cy + R * math.sin(a))
        info = auto_layers(q, E, cx, cy, hw, hh, gap=gap, clear=clear,
                           floor=floor, obs=obs, lane=lane,
                           max_inner=max(7, n // 4))
        if info is None:
            return None
        return dict(pos=q, groups=[list(pids)], centers=[(cx, cy)],
                    index={p: 0 for p in pids}, inner=info["inner"],
                    gap=info["gap"], cross=info["cross"], k=info["k"],
                    packed=False)

    # 组级排序：组当节点、组间边当边，让相邻组之间的桥最短
    k = len(groups)
    gidx = {p: i for i, g in enumerate(groups) for p in g}
    GE = set()
    for a, b in E:
        i, j = gidx[a], gidx[b]
        if i != j:
            GE.add((min(i, j), max(i, j)))
    try:
        gorder = order_cycle(list(range(k)), sorted(GE)) if GE \
            else list(range(k))
    except Exception:
        gorder = list(range(k))

    out, centers = {}, []
    for slot, gi in enumerate(gorder):
        g = groups[gi]
        t = -math.pi / 2 + 2.0 * math.pi * slot / k
        gx, gy = cx + a_ell * math.cos(t), cy + b_ell * math.sin(t)
        gs = set(g)
        sub = [(a, b) for a, b in E if a in gs and b in gs]
        R = max(hw * 2.0, len(g) * (2.0 * hw + gap) / (2.0 * math.pi))
        q = {}
        for m, p in enumerate(sorted(g)):
            a = t + math.pi + 2.0 * math.pi * m / len(g)
            q[p] = (gx + R * math.cos(a), gy + R * math.sin(a))
        info = auto_layers(q, sub, gx, gy, hw, hh, gap=gap, clear=clear,
                           floor=floor, lane=lane,
                           max_inner=max(3, len(g) // 3))
        if info is None:                  # 簇内排不下：均分环，至少不重叠
            for m, p in enumerate(sorted(g)):
                a = t + math.pi + 2.0 * math.pi * m / len(g)
                q[p] = (gx + R * math.cos(a), gy + R * math.sin(a))
        out.update(q)
        centers.append((gx, gy))
    return dict(pos=out, groups=groups, centers=centers,
                index={p: gidx[p] for p in pids}, inner=[],
                gap=0.0, cross=0, k=1.0, packed=True)


def rad_limits(order, edges, Rout, cx, cy, hw, hh, gap=30.0, lane=12.0,
               lvl_min=0.42, rounds=6, start=0.0, r_min=0.0):
    """逐卡半径上限的不动点；顺带当「这个规模到底放不放得下」的快速判据

    对每张卡 q，找出所有「q 夹在中间」的弦，算出这条弦在 q 的角度上离圆心
    多远（chord_r），减去卡半高和通道余量，就是 q 的半径上限。多根弦取最小
    值；没有弦夹着它 → 上限无穷，它就待在外圈。上限依赖别人的位置、别人的
    位置又依赖角度，所以迭代几轮取不动点（半径只减不增，保证收敛）。

    **为什么它会算出「越退越深」**：内圈卡彼此之间若有边，那条弦的端点也缩
    进来了，弦跟着往里走，于是弦两侧的卡要退得更深——每退一格半径掉一截，
    像多米诺。所以一旦某张卡需要退到 lvl_min 以内，就说明**这个人数在本卡
    片尺寸下根本放不下**（不是参数没调好）。这时直接返回 None，比硬算 3 秒
    再放弃诚实得多，也让调用方能立刻换方案。

    返回 (lvl, rout, 各卡绝对半径上限) 或 None。
    """
    pids = list(order)
    if not pids or not edges:
        return None
    # ── v0.38：**角度固定，只调半径** ────────────────────────────────
    #
    # 前两版都败在同一个地方：每次改半径都用 place_by_arc 重新分角，而
    # 「这张卡要退到多深」是由弦在它角度上的半径决定的（chord_r）——角度
    # 一动，need 全变，于是下一轮又退又挪，26 人实测能把卡逼到「我」旁边
    # （< 0.15R）退无可退。
    #
    # 真实需求其实很轻：26 人 34 条边里，25 条是相邻边（根本不用让），
    # 只有 13 张卡需要让开，中位数只退到 0.91R。角度不变之后 need 就是常量，
    # 一轮定死。半径全部绝对量，总角宽不够时**等比放大整圈**（等比不改变
    # 相对结构，所以不会反过来影响 need）。
    rmin = r_min if r_min > 0.0 else (hw + hh + 18.0)  # 圆心「我」+ 卡半高 + 间隙
    angle = {p: start + 2.0 * math.pi * k / len(pids)
             for k, p in enumerate(pids)}
    r = {p: Rout for p in pids}
    arc = 2.0 * hw + gap
    tot = 0.0
    for p in pids:
        tot += arc / r[p]
    if tot > 2.0 * math.pi - 1e-6:
        k = tot / (2.0 * math.pi)
        for p in pids:
            r[p] *= k
    need = {p: 1e9 for p in pids}
    for _ in range(rounds):
        placed = {}
        for p in pids:
            placed[p] = (cx + r[p] * math.cos(angle[p]),
                         cy + r[p] * math.sin(angle[p]))
        need = {p: 1e9 for p in pids}
        for e, mids in _mid_pairs(list(edges), angle).items():
            pa, pb = placed[e[0]], placed[e[1]]
            dx, dy = pb[0] - pa[0], pb[1] - pa[1]
            L = math.hypot(dx, dy)
            if L < 1e-6:
                continue
            # 卡片是 224×158 的矩形，弦贴近时先被切的是**角**（对角长 137px
            # 远大于半高 79px）。所以让开的余量要取矩形在弦法向上的支撑宽度
            # hw·|nx| + hh·|ny|，而不是一个常数半高——否则弦正好斜切过角时，
            # 沿卡片射线量出来还有 18px，实际已经切进卡里 42px。
            nx, ny = -dy / L, dx / L
            sup = hw * abs(nx) + hh * abs(ny)
            for q in mids:
                rc = chord_r(pa, pb, angle[q], (cx, cy))
                if rc > 0.0:
                    need[q] = min(need[q], rc - sup - lane)
        changed = False
        for p in pids:
            if need[p] > 1e8:
                continue
            if need[p] < r[p] - 0.5:
                r[p] = need[p]
                changed = True
        if min(r.values()) < rmin:
            return None
        if not changed:
            break
    rr = max(r.values())
    lvl = {p: r[p] / rr for p in pids}
    return lvl, rr, need


def cap_refine(pos, edges, cx, cy, hw, hh, gap=30.0, clear=26.0,
               lane=12.0, floor=22.0, obs=(), sweeps=3,
               lvl_min=0.42, rounds=5):
    """逐卡径向上限（v0.37）：谁挡着弦，谁就往里退，退到刚好让开为止

    两层构造（v0.36）把半径当**离散的二值决策**：外圈 1.0R 或内圈 0.58R。
    这在 18~20 人时够用，人一多就崩——因为「刚好让开」需要的半径是连续的：
    跨 2 格的弦在中间卡角度上的半径是 `R·cos(π/n)`，R=900、n=22 时是 0.96R，
    中间卡只要退到 0.96R − (卡半高 + 12) 就够了，根本不用退到 0.58R；而真正
    需要深退的卡，22 人时又不止 7 张（内圈容量就是 `max_inner`）。两头不合
    适，于是只能整体放弃。

    这里改成**逐卡算上限**，不再有「第几层」这个概念：
      对每张卡 q，找所有「q 夹在中间」的弦，算出这条弦在 q 的角度上离圆心
      多远（chord_r），减去卡半高和通道余量，就是 q 的半径上限。q 站到
      上限以内，那条弦就从它外侧过去了，它的辐射线也不会被切。
      多根弦取最小值；没有弦夹着它 → 上限无穷，它就待在外圈。

    半径上限依赖别人的位置，别人的位置又依赖角度，所以迭代几轮取不动点
    （只允许半径单调减小，保证收敛）。收敛后把上限交给 relax_free 当硬约束
    ——它只微调角度和几像素半径，不会再把卡拖出去。

    返回 dict(cross, gap, rout, inner, k) 或 None。
    """
    pids = list(pos)
    n = len(pids)
    if n < 6:
        return None
    E = [(a, b) for a, b in edges if a in pos and b in pos and a != b]
    if len(E) < 2:
        return None
    ang0 = {p: math.atan2(pos[p][1] - cy, pos[p][0] - cx) for p in pids}
    R0 = {p: math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pids}
    Rout = sum(R0.values()) / n
    if Rout < 1.0:
        return None
    order = sorted(pids, key=lambda p: ang0[p])
    start = ang0[order[0]]
    lim = rad_limits(order, E, Rout, cx, cy, hw, hh, gap=gap,
                     lane=max(lane, 18.0), lvl_min=lvl_min, rounds=rounds,
                     start=start)
    if lim is None:
        return None
    lvl, rout0, need = lim
    # 必须沿用 rad_limits 的**等分角度**——它在固定角度上收敛出了每张卡的半径，
    # 换 place_by_arc 重新按弧长分角会把这套半径作废（角度一变，need 全变）。
    n2 = len(order)
    pos2, rout2 = {}, rout0
    for k, p in enumerate(order):
        a = start + 2.0 * math.pi * k / n2
        pos2[p] = (cx + rout0 * lvl[p] * math.cos(a),
                   cy + rout0 * lvl[p] * math.sin(a))
    # rad_limits 给出的这版位置是**按构造零穿线**的（每张卡都退到上限以内）。
    # 后面的 relax_free 是去修卡间距和擦边的，它一放开角度就可能把某张卡挪到
    # 弦上——所以留一份构造解当底，退回代价是「不那么舒展」，而不是「有干涉」。
    pre = dict(pos2)
    segs = list(E) + [(None, q) for q in pos2]
    h, g = 1, -999.0
    for _kw in (dict(da_slots=0.26, r_lo=0.88, r_hi=1.02),
                dict(da_slots=0.10, r_lo=0.95, r_hi=1.00)):
        pos2 = dict(pre)
        relax_free(pos2, segs, cx, cy, hw, hh, clear=clear,
                   step_r=10.0, step_a=0.018, sweeps=sweeps,
                   r_min=hw + hh + 18.0, floor=floor, obs=obs,
                   lane_w=0.05, fan_w=4.0, fan_min_deg=24.0, r_cap=need,
                   **_kw)
        h, _tg = spoke_hits(pos2, E, cx, cy, hh, lane)
        g = min_gap(pos2, E, pids, hw, hh)
        if h == 0 and g >= clear - 6.0:
            break
    if h != 0 or g < clear - 6.0:
        pos2 = dict(pre)
        h, _tg = spoke_hits(pos2, E, cx, cy, hh, lane)
        g = min_gap(pos2, E, pids, hw, hh)

    h, _tg = spoke_hits(pos2, E, cx, cy, hh, lane)
    g = 999.0
    for a, b in E:
        for c in pids:
            if c in (a, b):
                continue
            gg = seg_rect_gap(pos2[a], pos2[b], pos2[c], hw, hh)
            if gg < g:
                g = gg
    if h > 0 or g < 15.0:
        if __import__("os").environ.get("NL_DEBUG"):
            _rr = {p: math.hypot(pos2[p][0] - cx, pos2[p][1] - cy) / rout2
                   for p in pids}
            print("[dbg]     上限解：穿线 %d 净空 %.1f 兜底深退 %d 张 "
                  "最小半径 %.2fR 最多 %.2fR"
                  % (h, g, sum(1 for p in pids if lvl[p] <= lvl_min + 1e-6),
                     min(lvl.values()), max(lvl.values())), flush=True)
        return None
    for i in range(n):
        for j in range(i + 1, n):
            u, v = pos2[pids[i]], pos2[pids[j]]
            if abs(u[0] - v[0]) < 2.0 * hw - 4.0 \
                    and abs(u[1] - v[1]) < 2.0 * hh - 4.0:
                return None
    inner = [p for p in pids if lvl[p] < 0.92]
    for p in pids:
        pos[p] = pos2[p]
    return dict(cross=h, gap=round(g, 1), rout=round(rout2, 0),
                inner=inner, k=round(min(lvl.values()), 2))


def layer_refine(pos, edges, cx, cy, hw, hh, gap=30.0,
                 max_inner=7, clear=26.0, lane=12.0, floor=22.0,
                 obs=(), sweeps=3, k_list=(0.58,)):
    """两层布局（v0.36）：先定层，再按弧长分角度，最后只修角度

    用户诉求（2026-10-28）：「长线横穿辐射线」是唯一还不能接受的干涉，
    并且认可「几张卡缩进内圈」这种做法（他手调的那张图正是这么干的）。

    **为什么单靠 relax_free 的 lane 项不行**：那条代价只认「这条弦在这张
    卡角度上的半径」，要消掉一处 200px 的缺口，得让两端同时外推 200px，
    而卡片逐个下降（坐标下降）时单张只动一点、罚分反而先涨——于是它把
    一两个中间卡拖到 0.4R 就算交差，剩下的穿线不动，画面变成「一堆卡缩在
    圆心附近」。实测这样出来的半径是 243~722，圈彻底散了。

    **两层的做法**：把「该进内圈」当成离散决策。中间卡只要在内圈（0.56R），
    跨 2~4 个卡位的弦（离圆心 0.77~0.94R）就从它外侧绕过去，一处都不用穿。
    所以顺序是：
      ① 选层：每张卡 外圈 1.0R / 内圈 kR，代价 = 10×穿线数 + 4×(净空不足)
         + 0.3×(内圈张数)，单张翻转 + 成对翻转，单调下降；
      ② 按弧长分角度（place_by_arc），内圈卡占更大角度才不重叠；
      ③ 半径锁在同一层里小范围浮动（±14%），只放开角度，交给 relax_free
         修卡片间距和残留擦边；
      ④ 还有穿线就把那条线的中间卡压进内圈，再来一轮（最多 4 轮）。

    返回 dict(cross=穿线处数, gap=线到卡最小净空, inner=[内圈名单],
    rout=外圈半径)。
    """
    pids = list(pos)
    n = len(pids)
    if n < 6 or len(edges) < 2:
        return None
    ang0 = {p: math.atan2(pos[p][1] - cy, pos[p][0] - cx) for p in pids}
    R0 = {p: math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pids}
    Rout = sum(R0.values()) / n
    if Rout < 1.0:
        return None
    order = sorted(pids, key=lambda p: ang0[p])
    start = ang0[order[0]]

    def _gap_of(p):
        g = 999.0
        for a_, b_ in edges:
            for c in p:
                if c in (a_, b_):
                    continue
                gg = seg_rect_gap(p[a_], p[b_], p[c], hw, hh)
                if gg < g:
                    g = gg
        return g

    def eval_lvl(lv):
        """一次候选评估：摆角 → 数穿线 → 只有零穿线时才去算净空

        净空是 O(边×卡) 的，而 90% 的候选都有穿线、根本轮不到看净空。
        只在「已经零穿线」的候选上算，评估次数直接砍掉一半以上。
        """
        p, _r = place_by_arc(order, lv, Rout, cx, cy, hw, gap, start, hh)
        h, _tg = spoke_hits(p, edges, cx, cy, hh, lane)
        nin = sum(1 for z in pids if lv[z] < 1.0)
        if h == 0:
            g = _gap_of(p)
            ok = 0.0 if g < clear else 1.0
        else:
            g, ok = 99.0, 1.0
        return (10.0 * h + 4.0 * (1.0 - ok) + 0.3 * nin), h, g

    best = None
    for K in k_list:
        lvl = {p: 1.0 for p in pids}
        cur, cur_h, _cg = eval_lvl(lvl)
        h0 = cur_h
        if cur_h > 0:
            # ① 单张翻转（每轮只取最优的一步，最多 4 轮）
            for _r in range(4):
                pick, pick_v = None, cur
                for p in pids:
                    lvl[p] = K if lvl[p] >= 1.0 else 1.0
                    v = eval_lvl(lvl)[0]
                    if v < pick_v - 1e-9:
                        pick, pick_v = p, v
                    lvl[p] = 1.0 if lvl[p] < 1.0 else K
                if pick is None:
                    break
                lvl[pick] = K if lvl[pick] >= 1.0 else 1.0
                cur, cur_h, _cg = eval_lvl(lvl)
                if cur_h == 0:
                    break
                # 两轮单张翻转还啃不动 8 成 → 这张图的两层结构帮不上忙，
                # 别再花 0.5s 硬啃（实测稠密随机图就是这样，最后还是要放弃）
                if _r >= 1 and cur_h > 0.8 * h0:
                    return None
            # ② 还不行再来一轮成对翻转（一张翻不动、成对才划算的情形）
            if cur_h > 0:
                pair, pair_v = None, cur
                for i in range(n):
                    for j in range(i + 1, n):
                        a_, b_ = pids[i], pids[j]
                        lvl[a_] = K if lvl[a_] >= 1.0 else 1.0
                        lvl[b_] = K if lvl[b_] >= 1.0 else 1.0
                        v = eval_lvl(lvl)[0]
                        if v < pair_v - 1e-9:
                            pair, pair_v = (a_, b_), v
                        lvl[a_] = 1.0 if lvl[a_] < 1.0 else K
                        lvl[b_] = 1.0 if lvl[b_] < 1.0 else K
                if pair is not None:
                    for z in pair:
                        lvl[z] = K if lvl[z] >= 1.0 else 1.0

        # 选层都清不掉大半穿线，说明这张图不是「一个环 + 几条长弦」，两层
        # 结构帮不上忙（稠密随机图实测还剩 29 处）。这种情况干脆放弃两层，
        # 让调用方走 v0.35 的二维自由布局——比硬套一个坏分层干净。
        # ③ 摆角度 + 修间距；还有穿线就把中间卡压进内圈，再来一轮
        p2 = None
        for _try in range(3):
            p2, rout2 = place_by_arc(order, lvl, Rout, cx, cy, hw, gap, start, hh)
            segs = list(edges) + [(None, q) for q in p2]
            relax_free(p2, segs, cx, cy, hw, hh, clear=clear,
                       r_lo=0.86, r_hi=1.14, da_slots=0.24,
                       step_r=10.0, step_a=0.018, sweeps=sweeps,
                       r_min=hw + hh + 18.0, floor=floor, obs=obs,
                       lane_w=0.05, fan_w=4.0, fan_min_deg=24.0)
            h, _tg = spoke_hits(p2, edges, cx, cy, hh, lane)
            if h == 0:
                break
            angx = {q: math.atan2(p2[q][1] - cy, p2[q][0] - cx) for q in p2}
            nin = sum(1 for z in pids if lvl[z] < 1.0)
            changed = False
            for e, mids in _mid_pairs(list(edges), angx).items():
                for q in mids:
                    rc = chord_r(p2[e[0]], p2[e[1]], angx[q], (cx, cy))
                    if rc <= 0.0:
                        continue
                    rq = math.hypot(p2[q][0] - cx, p2[q][1] - cy)
                    if rc < rq + hh + lane and lvl[q] >= 1.0 \
                            and nin < max_inner:
                        lvl[q] = K
                        nin += 1
                        changed = True
            if not changed:
                break
        h, _tg = spoke_hits(p2, edges, cx, cy, hh, lane)
        g = _gap_of(p2)
        sc = (-h, g)
        if best is None or sc > best[0]:
            best = (sc, p2, dict(lvl), K, rout2, h, g)

    # 没清干净、或者清干净了却是「把线怼到卡上 / 两张卡叠在一起」换来的，
    # 都不交付——交给调用方的旧路径（v0.35 的二维自由布局）更稳
    if best is not None and best[5] <= 2 and best[6] >= 18.0:
        # v0.40：只差一两处穿线、净空又够的解，别急着扔。沿半径做一次一维
        # 收尾（relax_clear 把辐射线也当障碍），常常能把最后一两根长弦让开。
        # 20 人实测：穿线 1 / 净空 26.4 被扔 → 退回二维自由布局得到 13 处穿线；
        # 收尾之后 0 处穿线、净空反而更大。
        _p3 = dict(best[1])
        _s3 = list(edges) + [(None, q) for q in _p3]
        try:
            relax_clear(_p3, _s3, cx, cy, hw, hh,
                        clear=max(clear, 30.0), r_lo=0.80, r_hi=1.26,
                        sweeps=10)
        except Exception:
            _p3 = None
        if _p3 is not None:
            _h3, _ = spoke_hits(_p3, edges, cx, cy, hh, lane)
            _g3 = _gap_of(_p3)
            if __import__("os").environ.get("NL_DEBUG"):
                print("[dbg]     收尾：穿线 %d→%d  净空 %.1f→%.1f"
                      % (best[5], _h3, best[6], _g3), flush=True)
            if _h3 == 0 and _g3 >= 15.0:
                best = ((-_h3, _g3), _p3, best[2], best[3], best[4],
                        _h3, _g3)
    # v0.40：允许最多 2 处穿线。20 人实测最优解就是「穿线 1 / 净空 26.4」，
    # 而那 1 处是结构性的——某张卡正好落在长弦的必经之路上，沿半径让不开
    # （relax_clear 收尾 1→1，净空反倒从 26.4 升到 27.4）。硬卡 0 处的代价
    # 是整层作废、退回二维自由布局，换来 13 处穿线，明显不值得。
    if best is None or best[5] > 2 or best[6] < 15.0:
        if __import__("os").environ.get("NL_DEBUG"):
            print("[dbg]     layer_refine 交不出：%s  (穿线 %s 净空 %s)"
                  % ("无候选" if best is None else "指标不达标",
                     "-" if best is None else best[5],
                     "-" if best is None else round(best[6], 1)), flush=True)
        return None
    (_sc, p2b, lvl_b, K_b, rout_b, h_b, g_b) = best
    _ks = list(p2b)
    for _i in range(len(_ks)):
        for _j in range(_i + 1, len(_ks)):
            _u, _v = p2b[_ks[_i]], p2b[_ks[_j]]
            if abs(_u[0] - _v[0]) < 2.0 * hw - 4.0 \
                    and abs(_u[1] - _v[1]) < 2.0 * hh - 4.0:
                return None
    for p in pids:
        pos[p] = p2b[p]
    return dict(cross=h_b, gap=round(g_b, 1), rout=round(rout_b, 0),
                inner=[p for p in pids if lvl_b[p] < 1.0], k=K_b)


def deep_refine(pos, edges, cx, cy, hw, hh, gap=30.0, clear=26.0,
                lane=12.0, floor=22.0, obs=(), sweeps=3,
                levels=(1.0, 0.70, 0.46), max_deep=13,
                w_depth=0.8, w_pair=3.0, rounds=6, grow_max=1.7):
    """多档下沉（v0.38）：内圈从 2 层扩到 3 档，把上限从 22 人推到 26

    用户诉求（2026-10-29）：在被告知「22 人是两层上限」之后，选择
    **硬扩到 26 人**。

    两层（v0.36）只有 1.0R / 0.58R 两档，容量是死数：

        内圈周长 2π·0.58R ≈ 2332px，每张卡连间隙 254px → 9 张

    而内圈卡彼此之间只要有边，那条弦会跟着缩进内圈、切穿旁边的内圈卡，
    于是实际只用得上 7 张（`max_inner` 就是照这个填的）。22 人时实测有
    **16 张**卡被夹在弦中间，16 > 7 —— 所以不是参数没调好，是容量不够。

    三档把容量拆开：跨 2 格的弦（离圆心 0.94R 左右）只需要中间卡退到
    0.70R，跨 3~5 格的才退到 0.46R。要用深层解决的那部分卡少了，深层
    也就不容易撞上「内圈互切」的级联。

    代价三项，都在防 v0.37 踩过的坑：
      · 10×穿线数       —— 主目标；
      · w_depth×Σ档位   —— 能少退就少退，避免全员朝圆心堆；
      · w_pair×边两端都在深档的张数 —— 内圈卡之间有边时，那条弦缩进内圈
        切成邻卡，是「越退越深」多米诺的源头（v0.37 诊断出来的死结）。

    `max_deep` 卡住中圈+内圈的总张数，`grow_max` 卡住 place_by_arc 因总角
    宽超 360° 而放大外圈的倍数——放太大画布就装不下了。

    返回 dict(cross, gap, rout, inner, k) 或 None。
    """
    pids = list(pos)
    n = len(pids)
    if n < 6 or len(edges) < 2:
        return None
    E = [(a, b) for a, b in edges if a in pos and b in pos and a != b]
    if len(E) < 2:
        return None
    ang0 = {p: math.atan2(pos[p][1] - cy, pos[p][0] - cx) for p in pids}
    R0 = {p: math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in pids}
    Rout = sum(R0.values()) / n
    if Rout < 1.0:
        return None
    order = sorted(pids, key=lambda p: ang0[p])
    start = ang0[order[0]]
    top = len(levels) - 1

    def _gap_of(p):
        g = 999.0
        for a_, b_ in E:
            for c in p:
                if c in (a_, b_):
                    continue
                gg = seg_rect_gap(p[a_], p[b_], p[c], hw, hh)
                if gg < g:
                    g = gg
        return g

    def _eval(idx):
        lv = {p: levels[idx[p]] for p in pids}
        p, rr = place_by_arc(order, lv, Rout, cx, cy, hw, gap, start, hh)
        if rr > Rout * grow_max:             # 圈被撑太大 → 画布装不下
            return 1e6, 99, 0.0, p
        h, _tg = spoke_hits(p, E, cx, cy, hh, lane)
        dep = 0
        for z in pids:
            dep += idx[z]
        pr = 0
        for a_, b_ in E:
            if idx[a_] > 0 and idx[b_] > 0:
                pr += 1
        if h == 0:
            g = _gap_of(p)
            ok = 0.0 if g < clear else 1.0
        else:
            g, ok = 99.0, 1.0
        return (10.0 * h + 4.0 * (1.0 - ok)
                + w_depth * dep + w_pair * pr), h, g, p

    idx = {p: 0 for p in pids}

    # ① **按「谁挡着弦」直接降档**，不做盲目搜索
    #
    # 先试过坐标下降（一次挪一张、只接受代价下降），在 22 人以上直接卡在
    # 「退 6 张就动不了」——因为挪一张卡会同时改变好几条弦，单步的收益
    # 常常是负的，而真正需要的是「把挡在那条弦上的 3 张一起退」。逐张挪
    # 永远迈不出这一步。
    #
    # 这里改成几何驱动：每轮算出所有穿线处，把**每条穿线弦的中间卡**降一
    # 档，一次到位；降完重摆、重新量，几轮就收敛。这正是 layer_refine 第
    # ③ 步「补层」的做法，只是档位更多、可以逐级加深。
    nbr = {}
    for k, p in enumerate(order):
        nbr[p] = {order[k - 1], order[(k + 1) % len(order)]}
    for _round in range(rounds):
        lv = {p: levels[idx[p]] for p in pids}
        placed, rr = place_by_arc(order, lv, Rout, cx, cy, hw, gap, start, hh)
        if rr > Rout * grow_max:
            break
        h, _tg = spoke_hits(placed, E, cx, cy, hh, lane)
        if h == 0:
            break
        angx = {p: math.atan2(placed[p][1] - cy, placed[p][0] - cx)
                for p in pids}
        nd = sum(1 for z in pids if idx[z] > 0)
        changed = 0
        for e, mids in _mid_pairs(E, angx).items():
            pa, pb = placed[e[0]], placed[e[1]]
            dx, dy = pb[0] - pa[0], pb[1] - pa[1]
            L = math.hypot(dx, dy)
            if L < 1e-6:
                continue
            nx, ny = -dy / L, dx / L
            sup = hw * abs(nx) + hh * abs(ny)
            for q in mids:
                rc = chord_r(pa, pb, angx[q], (cx, cy))
                if rc <= 0.0:
                    continue
                rq = math.hypot(placed[q][0] - cx, placed[q][1] - cy)
                if rc >= rq + sup + lane:    # 已经从卡外侧过去，不用管
                    continue
                if idx[q] >= top or nd >= max_deep:
                    continue
                # 环上左右邻卡都已在深档 → 再退就是「内圈互切」的死结，
                # 那条弦会跟着缩进内圈把邻居切穿（v0.37 的诊断结论）
                if all(idx[z] > 0 for z in nbr[q]):
                    continue
                idx[q] += 1
                nd += 1
                changed += 1
        if not changed:
            break

    # ② 能升就升：退得比需要更深是浪费空间，也会把圈撑大
    for _round in range(rounds):
        lv = {p: levels[idx[p]] for p in pids}
        placed, rr = place_by_arc(order, lv, Rout, cx, cy, hw, gap, start, hh)
        if rr > Rout * grow_max:
            break
        h0, _t0 = spoke_hits(placed, E, cx, cy, hh, lane)
        if h0 > 0:
            break
        best_p, best_v = None, 1e9
        for p in pids:
            if idx[p] == 0:
                continue
            old = idx[p]
            idx[p] = old - 1
            v = _eval(idx)[0]
            idx[p] = old
            if v < best_v - 1e-9:
                best_p, best_v = p, v
        if best_p is None:
            break
        idx[best_p] -= 1

    # 摆位 + 只修角度（半径锁在各卡档位附近 ±12%，别让 relax 又把卡拖出去）
    lv = {p: levels[idx[p]] for p in pids}
    p2, rout2 = place_by_arc(order, lv, Rout, cx, cy, hw, gap, start, hh)
    pre = dict(p2)
    segs = list(E) + [(None, q) for q in p2]
    h, g = 1, -999.0
    # 两轮 relax 都要「穿线 0 且净空够」才算数。只验穿线不够——relax 修间距
    # 时会同时挪半径和角度，实测能把两张卡挤到净空 −126（卡片叠在一起），
    # 而穿线数仍是 0，一路放行到守卫才被拦下，白花 1 秒。
    for _kw in (dict(da_slots=0.26, r_lo=0.88, r_hi=1.02),
                dict(da_slots=0.10, r_lo=0.95, r_hi=1.00)):
        p2 = dict(pre)
        relax_free(p2, segs, cx, cy, hw, hh, clear=clear,
                   step_r=10.0, step_a=0.018, sweeps=sweeps,
                   r_min=hw + hh + 18.0, floor=floor, obs=obs,
                   lane_w=0.05, fan_w=4.0, fan_min_deg=24.0, **_kw)
        h, _tg = spoke_hits(p2, E, cx, cy, hh, lane)
        g = _gap_of(p2)
        if h == 0 and g >= clear - 6.0:
            break
    if h != 0 or g < clear - 6.0:
        p2 = dict(pre)                        # 退回构造解：不舒展，但没干涉
        h, _tg = spoke_hits(p2, E, cx, cy, hh, lane)
        g = _gap_of(p2)

    if h > 0 or g < 15.0:
        if __import__("os").environ.get("NL_DEBUG"):
            _lv2 = sorted(lv.values())
            print("[dbg]     deep_refine 交不出：穿线 %d 净空 %.1f "
                  "退档张数 %d 最深 %.2fR"
                  % (h, g, sum(1 for z in pids if idx[z] > 0), _lv2[0]),
                  flush=True)
        return None
    for i in range(n):
        for j in range(i + 1, n):
            u, v = p2[pids[i]], p2[pids[j]]
            if abs(u[0] - v[0]) < 2.0 * hw - 4.0 \
                    and abs(u[1] - v[1]) < 2.0 * hh - 4.0:
                return None
    for p in pids:
        pos[p] = p2[p]
    return dict(cross=h, gap=round(g, 1), rout=round(rout2, 0),
                inner=[p for p in pids if idx[p] > 0],
                k=round(min(lv.values()), 2))


def relax_outward(pos, edges, cx, cy, hw, hh, clear=12.0, max_out=0.22,
                  rounds=30, pinned=()):
    """只沿半径向外让：把「被直线穿过」的卡往外挪一点，角度一分不动。

    为什么不用 avoid：avoid 允许「径向内/外 + 切向」三种位移一起用力，多
    条边互相拉扯，圆环被推成高低不平、卡还挤成一串——用户一眼看出「比手
    摆的差那么多」主要就是这个。

    这里限制成单一自由度：卡片只沿自己的极角往外走，最多 max_out 倍半径。
    于是①角间距/环序完全不变，视觉上仍是等角分布的正圆，只是个别卡外沿
    几十像素；②被挡的卡外移，弦到它的距离同步变大（弦在环内侧，卡往外走
    = 让开），单调收敛，不会来回弹。

    返回 (剩余压卡数, 最大外移量)。
    """
    pids = list(pos)
    fixed = set(pinned)
    bp = {p: max(1.0, math.hypot(pos[p][0] - cx, pos[p][1] - cy)) for p in pids}
    ba = {p: math.atan2(pos[p][1] - cy, pos[p][0] - cx) for p in pids}

    def out(p, need):
        cur = math.hypot(pos[p][0] - cx, pos[p][1] - cy)
        rr = min(bp[p] * (1.0 + max_out), cur + need * 0.9)
        if rr <= cur + 0.01:
            return 0.0
        pos[p] = (cx + math.cos(ba[p]) * rr, cy + math.sin(ba[p]) * rr)
        return rr - cur

    worst = 0.0
    for _ in range(rounds):
        dirty = 0
        for a, b in edges:
            if a not in pos or b not in pos or a == b:
                continue
            for c in pids:
                if c == a or c == b:
                    continue
                g = seg_rect_gap(pos[a], pos[b], pos[c], hw, hh)
                if g >= clear:
                    continue
                dirty += 1
                need = clear - g
                if c not in fixed:
                    worst = max(worst, out(c, need))
                else:                        # 挡路的卡被用户钉住 → 两端往外让
                    for e2 in (a, b):
                        if e2 not in fixed:
                            worst = max(worst, out(e2, need * 0.7))
        if not dirty:
            break
    left = len(card_hits(edges, pos, hw, hh, clear))
    return left, worst


def avoid(pos, edges, cx, cy, hw, hh, slot_da=0.3, base=None, pinned=(),
          me_r=0.0, clear=12.0, rounds=36, tan_ratio=0.35,
          rad_out=0.26, rad_in=0.20):
    """就地微调 pos：让每条直线的两端连线躲开其他卡片与圆心「我」。

    只做「卡位内」的小幅移动（切向 ≤ tan_ratio 个卡位、径向 −16%~+22%
    半径），族序与环结构不变；pinned（用户手拖过）的卡片不动，改为推它的
    对端来让线躲开。

    返回 (剩余干涉数, 最大位移) —— 供测试与日志用。
    """
    pids = list(pos)
    if base is None:
        base = dict(pos)
    fixed = set(pinned)
    clear = clear + 0.6          # 数值余量：别把收敛停在净空刚好等于阈值的边界上
    bp = {}
    for p in pids:
        r = math.hypot(base[p][0] - cx, base[p][1] - cy)
        a = math.atan2(base[p][1] - cy, base[p][0] - cx)
        bp[p] = (max(1.0, r), a)

    def clamp(p, x, y):
        """把新位置夹回基准卡位附近（切向/径向分轴限幅）"""
        br, ba = bp[p]
        rx, ry = math.cos(ba), math.sin(ba)
        tx, ty = -ry, rx
        dx, dy = x - base[p][0], y - base[p][1]
        dr = dx * rx + dy * ry
        dt = dx * tx + dy * ty
        lim_t = max(40.0, slot_da * br * tan_ratio)
        dr = max(-rad_in * br, min(rad_out * br, dr))
        dt = max(-lim_t, min(lim_t, dt))
        return (base[p][0] + rx * dr + tx * dt,
                base[p][1] + ry * dr + ty * dt)

    def push(p, ux, uy, need):
        if p in fixed:
            return False
        x, y = pos[p][0] + ux * need, pos[p][1] + uy * need
        pos[p] = clamp(p, x, y)
        return True

    worst_move = 0.0
    for _ in range(rounds):
        dirty = 0
        for a, b in edges:
            if a not in pos or b not in pos:
                continue
            A, B = pos[a], pos[b]
            # ① 三角形/扇区里的第三张卡：被直线擦到就一起让——卡让一大半，
            #    线段整体平移一小半（两端同向挪），合力把净空补上；只推卡
            #    容易撞到径向位移上限，只挪线会把整条边推歪。
            for p in pids:
                if p == a or p == b:
                    continue
                g = seg_rect_gap(A, B, pos[p], hw, hh)
                if g >= clear:
                    continue
                dirty += 1
                need = (clear - g)
                # 让开方向：线段→卡片中心的垂线；几乎重合时沿径向外推
                qx, qy = _foot(A, B, pos[p])
                vx, vy = pos[p][0] - qx, pos[p][1] - qy
                d = math.hypot(vx, vy)
                if d < 1e-3:
                    vx, vy = pos[p][0] - cx, pos[p][1] - cy
                    d = math.hypot(vx, vy) or 1.0
                ux, uy = vx / d, vy / d
                push(p, ux, uy, need * 0.6)
                for e2 in (a, b):
                    if e2 in fixed:
                        continue
                    pos[e2] = clamp(e2, pos[e2][0] - ux * need * 0.35,
                                    pos[e2][1] - uy * need * 0.35)
                A, B = pos[a], pos[b]
            # ② 直线切过圆心「我」：两端各反向转一点，让弦离圆心更远
            if me_r > 0:
                g = seg_gap_to_point(pos[a], pos[b], (cx, cy)) - me_r
                if g < clear:
                    need = clear - g
                    dirty += 1
                    va = (pos[a][0] - cx, pos[a][1] - cy)
                    vb = (pos[b][0] - cx, pos[b][1] - cy)
                    ra = math.hypot(*va) or 1.0
                    rb = math.hypot(*vb) or 1.0
                    # A 逆时针、B 顺时针各转 δ（弦到圆心的距离 ≈ R·sinδ）
                    for p, r, sgn in ((a, ra, 1.0), (b, rb, -1.0)):
                        if p in fixed:
                            continue
                        ang = math.atan2(pos[p][1] - cy, pos[p][0] - cx)
                        delta = math.asin(min(0.95, (need + 2.0) / r)) * 0.5
                        ang += sgn * delta
                        x = cx + r * math.cos(ang)
                        y = cy + r * math.sin(ang)
                        if p in bp:
                            pos[p] = clamp(p, x, y)
        # ③ 卡片互不压住（分轴最小位移推开）
        for i, a in enumerate(pids):
            for b in pids[i + 1:]:
                ax, ay = pos[a]
                bx, by = pos[b]
                dx, dy = bx - ax, by - ay
                ox = (2 * hw + 14.0) - abs(dx)
                oy = (2 * hh + 14.0) - abs(dy)
                if ox <= 0 or oy <= 0:
                    continue
                dirty += 1
                sx = 1.0 if dx >= 0 else -1.0
                sy = 1.0 if dy >= 0 else -1.0
                if ox <= oy:
                    mv, ux, uy = ox * 0.5, sx, 0.0
                else:
                    mv, ux, uy = oy * 0.5, 0.0, sy
                if b not in fixed:
                    x, y = pos[b][0] + ux * mv, pos[b][1] + uy * mv
                    pos[b] = clamp(b, x, y)
                if a not in fixed:
                    x, y = pos[a][0] - ux * mv, pos[a][1] - uy * mv
                    pos[a] = clamp(a, x, y)
        if not dirty:
            break
    # ④ 回拉：被多条线同时拽、漂得离卡位太远的卡（实测有卡漂 100+px），
    #    只要回退不增加整体亏空就往基准位置退——它漂出去最容易撞进别人的弦。
    def deficit():
        s = 0.0
        for a2, b2 in edges:
            if a2 not in pos or b2 not in pos:
                continue
            for q in pids:
                if q == a2 or q == b2:
                    continue
                g2 = seg_rect_gap(pos[a2], pos[b2], pos[q], hw, hh)
                if g2 < clear:
                    s += (clear - g2) ** 2
        return s

    for _ in range(3):
        cur = deficit()
        moved = False
        for p in pids:
            if p in fixed:
                continue
            d = math.hypot(pos[p][0] - base[p][0], pos[p][1] - base[p][1])
            if d < 0.15 * bp[p][0]:
                continue
            old = pos[p]
            for t in (0.35, 0.6):
                pos[p] = clamp(p, old[0] + (base[p][0] - old[0]) * t,
                               old[1] + (base[p][1] - old[1]) * t)
                val = deficit()
                if val <= cur + 1e-6:
                    cur = val
                    moved = True
                    break
                pos[p] = old
        if not moved:
            break
    # ⑤ 收尾精修：前几轮是「各让各的」，卡片可能在互相退让中撞进另一条
    #    直线。这里改成「只在整体干涉量下降时才接受」的贪心：对每条残留
    #    干涉试几个候选位移（挡住线的卡沿垂线/径向让、或整条线平移），算全局
    #    亏空，只保留真正变好的那一步。
    for _ in range(4):
        bad = [c for c in card_hits(edges, pos, hw, hh, clear)
               if c[2] not in fixed]
        if not bad:
            break
        cur = deficit()
        improved = False
        for a, b, p, g in bad[:16]:
            need = (clear - g) * 0.9
            A, B = pos[a], pos[b]
            qx, qy = _foot(A, B, pos[p])
            vx, vy = pos[p][0] - qx, pos[p][1] - qy
            d = math.hypot(vx, vy) or 1.0
            ux, uy = vx / d, vy / d
            rx, ry = pos[p][0] - cx, pos[p][1] - cy
            rr = math.hypot(rx, ry) or 1.0
            cands = [
                ("card", p, ux, uy, need),
                ("card", p, ux, uy, need * 1.8),
                ("card", p, rx / rr, ry / rr, need),
                ("card", p, rx / rr, ry / rr, need * 1.8),
                ("card", p, -rx / rr, -ry / rr, need),
                ("card", p, -uy, ux, need * 0.8),
                ("card", p, uy, -ux, need * 0.8),
                ("line", a, -ux, -uy, need * 0.6),
                ("line", b, -ux, -uy, need * 0.6),
            ]
            best = None
            for kind, who, dx2, dy2, mag in cands:
                if who in fixed:
                    continue
                if kind == "card":
                    old = pos[who]
                    pos[who] = clamp(who, old[0] + dx2 * mag, old[1] + dy2 * mag)
                    val = deficit()
                    if val < cur - 1e-6 and (best is None or val < best[0]):
                        best = (val, who, pos[who], None, None)
                    pos[who] = old
                else:
                    other = b if who == a else a
                    if other in fixed:
                        continue
                    oa, ob = pos[who], pos[other]
                    pos[who] = clamp(who, oa[0] + dx2 * mag, oa[1] + dy2 * mag)
                    pos[other] = clamp(other, ob[0] + dx2 * mag,
                                       ob[1] + dy2 * mag)
                    val = deficit()
                    if val < cur - 1e-6 and (best is None or val < best[0]):
                        best = (val, who, pos[who], other, pos[other])
                    pos[who], pos[other] = oa, ob
            if best:
                val, who, npos, other, opos2 = best
                pos[who] = npos
                if other is not None:
                    pos[other] = opos2
                cur = val
                improved = True
        if not improved:
            break
    for p in pids:
        worst_move = max(worst_move,
                         math.hypot(pos[p][0] - base[p][0],
                                    pos[p][1] - base[p][1]))
    left = len(card_hits(edges, pos, hw, hh, clear))
    return left, worst_move


def _foot(a, b, p):
    """点 p 在直线 ab 上的垂足"""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return (ax, ay)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2
    return (ax + dx * t, ay + dy * t)


def separate_cards(pos, cx, cy, hw, hh, gap=8.0, rounds=120, pinned=(),
                   spread=0.0):
    """保底分离：只保证「任意两张卡不重叠」，不碰线条代价。

    为什么需要它：人多 + 关系密时，`auto_layers` 会因「半径要退进下限」
    判结构性放不下，交回二维自由布局；而自由布局在 30 人 / 37 条边上也
    排不干净，最终画出「卡片压成一坨」的图 —— 用户点都点不中。这里做一遍
    纯几何分离，作为交付前的最后一道底线：宁可线乱，卡片不能叠。

    推开方向取**连心线**，位移 `need - d` 对半分给两张卡：
        need = 2*(hw*|ux| + hh*|uy|) + gap
    这个 need 是「两张矩形沿该方向的支撑半宽之和」，比用常数对角线更贴合
    实际轮廓（水平方向 154px、竖直 136px、45° 时 205px）。

    返回：实际跑了几轮（0 表示本来就不重叠）。
    """
    fixed = set(pinned)
    pids = list(pos)
    n = len(pids)
    if n < 2:
        return 0
    for r in range(rounds):
        moved = 0
        for i in range(n):
            a = pids[i]
            ax, ay = pos[a]
            for j in range(i + 1, n):
                b = pids[j]
                if a in fixed and b in fixed:
                    continue
                bx, by = pos[b]
                dx, dy = bx - ax, by - ay
                d = math.hypot(dx, dy)
                if d < 1e-6:
                    # 完全重合且位于中心时也必须有确定的非零分离方向。
                    ux, uy = (ax - cx), (ay - cy)
                    m = math.hypot(ux, uy)
                    ux, uy = (ux / m, uy / m) if m > 1e-9 else (1.0, 0.0)
                    d = 0.0
                else:
                    ux, uy = dx / d, dy / d
                need = 2.0 * (hw * abs(ux) + hh * abs(uy)) + gap
                if d >= need:
                    continue
                push = (need - d) / (1.0 if a in fixed or b in fixed else 2.0)
                if a not in fixed:
                    ax -= ux * push
                    ay -= uy * push
                    pos[a] = (ax, ay)
                if b not in fixed:
                    bx += ux * push
                    by += uy * push
                    pos[b] = (bx, by)
                moved += 1
        if not moved:
            return r + 1
    return rounds


def polish_geometry(pos, edges, cx, cy, hw, hh, clear=12.0,
                    sweeps=5, pinned=(), generations=(), generation_gap=None,
                    include_spokes=True, minimum_radius=None,
                    center_obstacle=False, preserve_spacing=0.0):
    """完整依赖的二维收尾：移动端点时，同时评价其所有出线扫过其它卡。

    不锁环位，不修改固定坐标；纯 Python、固定候选与轮数，缓存重绘不调用。
    压卡和卡片重叠优先，交叉只作为次级代价，不为减少交叉牺牲可读性。
    """
    ids = sorted(pos)
    fixed = set(pinned)
    edges = sorted({tuple(sorted((a, b))) for a, b in edges
                    if a in pos and b in pos and a != b})
    center = (cx, cy)
    segments = edges + ([(None, p) for p in ids] if include_spokes else [])
    incident = {p: [s for s in segments if p in s] for p in ids}
    others = {p: [s for s in segments if p not in s] for p in ids}
    constraints = sorted({(a, b) for a, b in generations
                          if a in pos and b in pos and a != b})
    margin = generation_gap if generation_gap is not None else 2 * max(hw, hh) + 30
    # 先建立代际可行域，再在同一可行域内优化净空；固定坐标始终优先。
    radii = {p: math.hypot(pos[p][0] - cx, pos[p][1] - cy) for p in ids}
    radial_floor = 180.0 if minimum_radius is None else max(0.0, minimum_radius)
    lower = {p: (radii[p] if p in fixed else radial_floor) for p in ids}
    upper = {p: (radii[p] if p in fixed else float("inf")) for p in ids}
    for _ in range(len(ids)):
        changed = False
        for a, b in constraints:
            if b not in fixed and lower[b] < lower[a] + margin:
                lower[b] = lower[a] + margin
                changed = True
            if a not in fixed and upper[a] > upper[b] - margin:
                upper[a] = upper[b] - margin
                changed = True
        if not changed:
            break
    for p in (() if preserve_spacing else ids):
        if p in fixed:
            continue
        r = max(lower[p], min(upper[p], radii[p]))
        angle = math.atan2(pos[p][1] - cy, pos[p][0] - cx)
        pos[p] = (cx + r * math.cos(angle), cy + r * math.sin(angle))
    for _ in range(0 if preserve_spacing else len(ids)):
        changed = False
        for a, b in constraints:
            ra = math.hypot(pos[a][0] - cx, pos[a][1] - cy)
            rb = math.hypot(pos[b][0] - cx, pos[b][1] - cy)
            if rb >= ra + margin - 1e-6 or b in fixed:
                continue
            angle = math.atan2(pos[b][1] - cy, pos[b][0] - cx)
            pos[b] = (cx + (ra + margin) * math.cos(angle),
                      cy + (ra + margin) * math.sin(angle))
            changed = True
        if not changed:
            break
    anchor = dict(pos)
    mean_radius = sum(math.hypot(x - cx, y - cy)
                      for x, y in pos.values()) / max(1, len(pos))
    # 代际约束按人预分组：cost 只关心与当前移动点相关的那几条。
    constraints_by = {p: [] for p in ids}
    for a, b in constraints:
        constraints_by[a].append((a, b))
        if b != a:
            constraints_by[b].append((a, b))

    def cost(p, q):
        x, y = q
        total = 0.0
        radius = math.hypot(x - cx, y - cy)
        for a, b in constraints_by[p]:
            ra = math.hypot(pos[a][0] - cx, pos[a][1] - cy)
            rb = math.hypot(pos[b][0] - cx, pos[b][1] - cy)
            old_violation = max(0.0, ra + margin - rb)
            new_violation = max(0.0, (radius if p == a else ra)
                                + margin - (radius if p == b else rb))
            if new_violation > old_violation + 1e-6:
                return float("inf")
            total += 1000.0 * new_violation ** 2
        def point(k):
            return center if k is None else (q if k == p else pos[k])
        def near_line(a, b, c):
            if (c[0] < min(a[0], b[0]) - hw - clear
                    or c[0] > max(a[0], b[0]) + hw + clear
                    or c[1] < min(a[1], b[1]) - hh - clear
                    or c[1] > max(a[1], b[1]) + hh + clear):
                return 0.0
            g = seg_rect_gap(a, b, c, hw, hh)
            return (max(0.0, clear - g) ** 2
                    + 80.0 * max(0.0, 2.0 - g) ** 2)
        for d in ids:
            if d == p:
                continue
            dx, dy = abs(x - pos[d][0]), abs(y - pos[d][1])
            if preserve_spacing and dx < 2 * hw + preserve_spacing - 1e-7 \
                    and dy < 2 * hh + preserve_spacing - 1e-7:
                return float("inf")
            overlap = min(2 * hw + 10 - dx, 2 * hh + 10 - dy)
            if overlap > 0:
                total += 160.0 * overlap * overlap
        if center_obstacle:
            overlap = min(2 * hw + 12 - abs(x - cx),
                          2 * hh + 12 - abs(y - cy))
            if overlap > 0:
                return float("inf")
        for a, b in others[p]:
            # others 里的段不含 p，但可能是「中心—某人」的连线，端点为 None。
            total += near_line(center if a is None else pos[a],
                               center if b is None else pos[b], q)
        for a, b in incident[p]:
            pa, pb = point(a), point(b)
            for d in ids:
                if d != a and d != b:
                    total += near_line(pa, pb, pos[d])
            if center_obstacle and a is not None and b is not None:
                total += near_line(pa, pb, center)
            # 只算一端随 p 移动的交叉，共享端点不是交叉。
            for u, v in others[p]:
                if a in (u, v) or b in (u, v):
                    continue
                if seg_hit(pa, pb,
                           center if u is None else pos[u],
                           center if v is None else pos[v]):
                    total += 1600.0
        floor_radius = max(180.0, 0.50 * mean_radius) \
            if minimum_radius is None else max(0.0, minimum_radius)
        total += 160.0 * max(0.0, floor_radius - radius) ** 2
        total += 0.0005 * ((x - anchor[p][0]) ** 2
                           + (y - anchor[p][1]) ** 2)
        return total

    moved = 0
    steps = (96.0, 48.0, 24.0, 12.0, 6.0) if include_spokes \
        else (192.0, 96.0, 48.0, 24.0, 12.0)
    for step in steps[:sweeps] * 2:
        for p in ids:
            if p in fixed:
                continue
            old = pos[p]
            best, best_cost = old, cost(p, old)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                           (-1, -1), (-1, 1), (1, -1), (1, 1)):
                q = (old[0] + dx * step, old[1] + dy * step)
                value = cost(p, q)
                if value < best_cost - 1e-7:
                    best, best_cost = q, value
            if best != old:
                pos[p] = best
                moved += 1
    return moved


def free_layout(pids, edges, cx, cy, hw, hh, pinned=(), generations=(), groups=()):
    """真实无向图的自由二维布局；固定预算、无依赖、与输入遍历顺序无关。

    pids 可为 ID 序列或已有坐标映射。pinned 通常为 {ID: (x, y)}；
    若只给固定 ID，必须由 pids 坐标映射提供原坐标。代际 (长辈, 子辈)
    仅限制这对亲属距中心的先后，不为其他人物分配圆环或角度槽位。
    """
    initial = dict(pids) if hasattr(pids, "items") else {}
    ids = sorted(set(pids), key=lambda p: (type(p).__name__, repr(p)))
    if not ids:
        return {}
    index = {p: i for i, p in enumerate(ids)}
    fixed = dict(pinned) if hasattr(pinned, "items") else {
        p: initial[p] for p in pinned if p in initial}
    missing = [p for p in pinned if p in index and p not in fixed]
    if missing:
        raise ValueError("固定人物必须提供原坐标：pinned 坐标映射或 pids 坐标映射")
    fixed = {p: tuple(q) for p, q in fixed.items() if p in index}
    pairs = sorted({tuple(sorted((index[a], index[b]))) for a, b in edges
                    if a in index and b in index and a != b})
    relations = [(ids[a], ids[b]) for a, b in pairs]
    adjacency = {p: [] for p in ids}
    for a, b in relations:
        adjacency[a].append(b)
        adjacency[b].append(a)
    constraints = sorted({(a, b) for a, b in generations
                          if a in index and b in index and a != b},
                         key=lambda e: (index[e[0]], index[e[1]]))
    sx, sy = 2.0 * hw + 36.0, 2.0 * hh + 36.0
    gap, margin = 12.0, 2.0 * max(hw, hh) + 30.0
    components, seen = [], set()
    # 明确分组只决定初始区域，跨圈真实边仍参与后续避让，不补造关系。
    for group in sorted(groups, key=lambda g: (g["kind"] != "家人", g["kind"], g["name"])):
        if group["kind"] != "家人" and not group.get("explicit"):
            continue
        component = sorted(set(group["members"]) & set(ids) - seen, key=index.get)
        if len(component) < 2:
            continue
        components.append(component)
        seen.update(component)
    for root in sorted(ids, key=lambda p: (-len(adjacency[p]), index[p])):
        if root in seen:
            continue
        component, queue = [], [root]
        seen.add(root)
        for p in queue:
            component.append(p)
            for q in sorted(adjacency[p],
                            key=lambda q: (-len(adjacency[q]), index[q])):
                if q not in seen:
                    seen.add(q)
                    queue.append(q)
        components.append(component)

    # 各分组从矩形网格起步，弹簧只连接真实人物关系。
    # 分量自身质心提供很弱的平移锚定，没有指向全局圆心的径向吸力。
    blocks = []
    for component in components:
        count = len(component)
        cols = max(1, int(math.ceil(math.sqrt(count * sy / sx))))
        local = {p: [((i % cols) - (cols - 1) / 2.0) * sx,
                     (i // cols) * sy + ((i % cols) % 2) * sy * 0.17]
                 for i, p in enumerate(component)}
        members = set(component)
        subedges = [(a, b) for a, b in relations if a in members and b in members]
        rounds = 100 if count <= 60 else 45
        for turn in range(rounds):
            force = {p: [0.0, 0.0] for p in component}
            for i, a in enumerate(component):
                ax, ay = local[a]
                for b in component[i + 1:]:
                    dx = (local[b][0] - ax) / sx
                    dy = (local[b][1] - ay) / sy
                    distance = math.hypot(dx, dy)
                    if distance < 1e-8:
                        dx, dy, distance = 1.0, 0.37, math.hypot(1.0, 0.37)
                    power = min(1.6, 0.28 / (distance * distance))
                    fx, fy = dx / distance * power * sx, dy / distance * power * sy
                    force[a][0] -= fx
                    force[a][1] -= fy
                    force[b][0] += fx
                    force[b][1] += fy
            for a, b in subedges:
                dx = (local[b][0] - local[a][0]) / sx
                dy = (local[b][1] - local[a][1]) / sy
                distance = math.hypot(dx, dy) or 1.0
                power = 0.14 * (distance - 1.10)
                fx, fy = dx / distance * power * sx, dy / distance * power * sy
                force[a][0] += fx
                force[a][1] += fy
                force[b][0] -= fx
                force[b][1] -= fy
            mx = sum(local[p][0] for p in component) / count
            my = sum(local[p][1] for p in component) / count
            cap = 0.24 * (1.0 - turn / rounds) + 0.025
            compact = 0.025 + 0.004 * math.sqrt(count)
            for p in component:
                fx = force[p][0] - compact * (local[p][0] - mx)
                fy = force[p][1] - compact * (local[p][1] - my)
                scale = min(1.0, cap / max(1e-8, math.hypot(fx / sx, fy / sy)))
                local[p][0] += fx * scale
                local[p][1] += fy * scale
        left = min(q[0] for q in local.values()) - hw - 24.0
        top = min(q[1] for q in local.values()) - hh - 24.0
        width = max(q[0] for q in local.values()) + hw + 24.0 - left
        height = max(q[1] for q in local.values()) + hh + 24.0 - top
        blocks.append((component, local, left, top, width, height))

    # 矩形装箱让无联系分量各占一块平面，不将分量中心摆成圆。
    desired_ratio = 1.5
    baseline = max(max(b[4] for b in blocks),
                   max(b[5] for b in blocks) * desired_ratio,
                   math.sqrt(sum(b[4] * b[5] for b in blocks) * desired_ratio))
    widths = {baseline * factor for factor in (0.85, 1.0, 1.15, 1.35)}
    widths.add(sum(b[4] for b in blocks[:2]))
    packed = []
    for target_width in sorted(widths):
        trial, cursor_x, cursor_y, row_height, used_width = {}, 0.0, 0.0, 0.0, 0.0
        for component, local, left, top, width, height in blocks:
            if cursor_x and cursor_x + width > target_width:
                cursor_x, cursor_y, row_height = 0.0, cursor_y + row_height, 0.0
            for p in component:
                trial[p] = (cursor_x + local[p][0] - left,
                            cursor_y + local[p][1] - top)
            cursor_x += width
            used_width = max(used_width, cursor_x)
            row_height = max(row_height, height)
        used_height = cursor_y + row_height
        # 同时约束画布比例与留白，有限候选避免宽度阈值造成整块纵向堆叠。
        score = used_width * used_height * (1.0 + 0.7 * abs(
            math.log(used_width / used_height / desired_ratio)))
        packed.append((score, target_width, trial))
    pos = min(packed, key=lambda item: item[:2])[2]
    mx = (min(q[0] for q in pos.values()) + max(q[0] for q in pos.values())) / 2.0
    my = (min(q[1] for q in pos.values()) + max(q[1] for q in pos.values())) / 2.0
    pos = {p: (x + cx - mx, y + cy - my) for p, (x, y) in pos.items()}
    # 含固定成员的分量先整体平移到固定成员附近，再精确保留每个固定坐标。
    for component in components:
        pins = [p for p in component if p in fixed]
        if pins:
            dx = sum(fixed[p][0] - pos[p][0] for p in pins) / len(pins)
            dy = sum(fixed[p][1] - pos[p][1] for p in pins) / len(pins)
            for p in component:
                pos[p] = (pos[p][0] + dx, pos[p][1] + dy)
    pos.update(fixed)

    # 固定后代向祖辈传播半径上界，代际拓扑顺序确保三代约束能一次满足。
    lower = {p: (math.hypot(fixed[p][0] - cx, fixed[p][1] - cy)
                 if p in fixed else 0.0) for p in ids}
    upper = {p: (lower[p] if p in fixed else float("inf")) for p in ids}
    parents = {p: [] for p in ids}
    children = {p: [] for p in ids}
    for a, b in constraints:
        parents[b].append(a)
        children[a].append(b)
    for _ in range(len(ids)):
        changed = False
        for a, b in constraints:
            if b not in fixed and lower[b] < lower[a] + margin:
                lower[b] = lower[a] + margin
                changed = True
            if a not in fixed and upper[a] > upper[b] - margin:
                upper[a] = upper[b] - margin
                changed = True
        if not changed:
            break
    todo = {p: len(parents[p]) for p in ids}
    order, ready = [], [p for p in ids if not todo[p]]
    while ready:
        ready.sort(key=lambda p: (-len(adjacency[p]), index[p]))
        p = ready.pop(0)
        order.append(p)
        for q in children[p]:
            todo[q] -= 1
            if not todo[q]:
                ready.append(q)
    order += [p for p in ids if p not in set(order)]
    placed = dict(fixed)
    obstacles = [(cx, cy)] + list(fixed.values())
    for p in order:
        if p in fixed:
            continue
        lo = max([lower[p]] + [math.hypot(placed[a][0] - cx, placed[a][1] - cy)
                               + margin for a in parents[p] if a in placed])
        hi = upper[p]
        desired = pos[p]

        def radial(q):
            dx, dy = q[0] - cx, q[1] - cy
            radius = math.hypot(dx, dy)
            bound = max(lo, min(hi, radius))
            if radius < 1e-8:
                return (cx + bound, cy)
            return (cx + dx * bound / radius, cy + dy * bound / radius)

        desired = radial(desired)

        def allowed(q):
            radius = math.hypot(q[0] - cx, q[1] - cy)
            if hi >= lo and (radius < lo - 1e-6 or radius > hi + 1e-6):
                return False
            return all(abs(q[0] - x) >= 2 * hw + gap - 1e-7
                       or abs(q[1] - y) >= 2 * hh + gap - 1e-7
                       for x, y in obstacles)

        candidates = [desired]
        if not allowed(desired):
            for x, y in obstacles:
                for dx in (-1, 1):
                    candidates.append(radial((x + dx * (2 * hw + gap), desired[1])))
                for dy in (-1, 1):
                    candidates.append(radial((desired[0], y + dy * (2 * hh + gap))))
                for dx in (-1, 1):
                    for dy in (-1, 1):
                        candidates.append(radial((x + dx * (2 * hw + gap),
                                                  y + dy * (2 * hh + gap))))
            # 有界网格兜底；有限上界时增加局部亲属可行域的角向采样。
            for ring in range(1, len(ids) + 2):
                for dx, dy in ((ring, 0), (-ring, 0), (0, ring), (0, -ring),
                               (ring, ring), (-ring, ring), (ring, -ring), (-ring, -ring)):
                    candidates.append(radial((desired[0] + dx * sx, desired[1] + dy * sy)))
            if hi < float("inf"):
                for radius in (lo, max(lo, (lo + hi) / 2.0), max(lo, hi)):
                    for i in range(64):
                        angle = i * 2.0 * math.pi / 64
                        candidates.append((cx + radius * math.cos(angle),
                                           cy + radius * math.sin(angle)))
            feasible = [q for q in candidates if allowed(q)]
            if feasible:
                desired = min(feasible, key=lambda q: ((q[0] - desired[0]) ** 2
                                                       + (q[1] - desired[1]) ** 2, q))
            else:
                # 互相矛盾的固定约束没有可行解，固定点仍优先，活动卡保证不重叠。
                desired = (max(x for x, y in obstacles) + sx,
                           desired[1])
        placed[p] = desired
        obstacles.append(desired)
    pos = {p: placed[p] for p in ids}
    work = len(ids) * len(relations) + len(relations) ** 2
    sweeps = min(4 if len(ids) <= 40 else 2,
                 max(0, 12000 // max(1, work)))
    polish_geometry(pos, relations, cx, cy, hw, hh, clear=12.0,
                    sweeps=sweeps, pinned=fixed,
                    generations=constraints, generation_gap=margin,
                    include_spokes=False, minimum_radius=0.0,
                    center_obstacle=True, preserve_spacing=gap)
    if work <= 3000 and len(ids) <= 40:
        for _ in range(4):
            if not card_hits(relations, pos, hw, hh, clear=2.0):
                break
            polish_geometry(pos, relations, cx, cy, hw, hh, clear=12.0,
                            sweeps=5, pinned=fixed, generations=constraints,
                            generation_gap=margin, include_spokes=False,
                            minimum_radius=0.0, center_obstacle=True,
                            preserve_spacing=gap)
    # 只精修拥挤分量：小型稀疏家庭、认识分量和手动固定点保持原位。
    crowded = set()
    for component in components:
        members = set(component)
        edge_count = sum(a in members and b in members for a, b in relations)
        if len(members) >= 4 and (len(members) >= 6 or edge_count >= len(members)):
            crowded.update(members)
    spoke_work = len(ids) * (len(relations) + len(ids)) + (len(relations) + len(ids)) ** 2
    spoke_sweeps = min(2, 12000 // max(1, spoke_work))
    if crowded - set(fixed) and spoke_sweeps:
        before = dict(pos)
        before_hits = len(card_hits(relations, pos, hw, hh, clear=0.0))
        before_crossings = count_crossings(relations, pos)[0]
        polish_geometry(pos, relations, cx, cy, hw, hh, clear=12.0,
                        sweeps=spoke_sweeps, pinned=set(ids) - crowded | set(fixed),
                        generations=constraints, generation_gap=margin,
                        include_spokes=True, minimum_radius=0.0,
                        center_obstacle=True, preserve_spacing=gap)
        # 中心线优化不得用新的事实线穿卡或交叉换取收益。
        if (len(card_hits(relations, pos, hw, hh, clear=0.0)) > before_hits
                or count_crossings(relations, pos)[0] > before_crossings):
            pos = before
    pos.update(fixed)
    return pos


def overlap_pairs(pos, hw, hh):
    """返回真正「矩形相交」的卡片对数量（轴对齐，留 2px 容差）"""
    pids = list(pos)
    cnt = 0
    for i in range(len(pids)):
        ax, ay = pos[pids[i]]
        for j in range(i + 1, len(pids)):
            bx, by = pos[pids[j]]
            if (abs(ax - bx) < 2.0 * hw - 2.0
                    and abs(ay - by) < 2.0 * hh - 2.0):
                cnt += 1
    return cnt
