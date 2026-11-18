# -*- coding: utf-8 -*-
"""v054 真实关系图隔离验证；数据、截图、指标仅写项目 _tmp/v054。"""
import encodings.utf_8_sig  # 审计钩子启用前加载，避免运行时再读 C 盘字节码缓存
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SMOKE = "--smoke" in sys.argv
OUT = ROOT / "_tmp" / "v054" / ("smoke" if SMOKE else "adaptive")
assert ROOT.is_dir() and (ROOT / "_tmp").is_dir(), "D盘项目父目录必须存在"
OUT.mkdir(parents=True, exist_ok=True)
for key in ("TEMP", "TMP", "TMPDIR"):
    os.environ[key] = str(ROOT / '_tmp' / 'v054')
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.chdir(ROOT)


def audit(event, values):
    if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'):
        raise RuntimeError('隔离验收禁止外部网络或启动外部程序：' + event)
    if event == "sqlite3.connect" and values and values[0] != ":memory:":
        target = Path(os.fsdecode(values[0])).resolve()
        if OUT.resolve() not in target.parents:
            raise RuntimeError("禁止连接非隔离数据库：" + str(target))
    if event in ("open", "os.listdir", "os.scandir") and values:
        value = values[0]
        if isinstance(value, (str, bytes, os.PathLike)):
            path = os.fsdecode(value).replace("\\", "/")
            if path.upper().startswith("C:"):
                raise RuntimeError("禁止访问 C 盘：" + path)


sys.addaudithook(audit)
source_names = ("ui.py", "relationship_view.py", "netlayout.py", "llm.py", "db.py",
                "tools/verify_adaptive.py", "tools/smoke.py")
source_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                 for name in source_names}
(OUT / "source_hashes.json").write_text(json.dumps(source_hashes, indent=2), encoding="utf-8")
for name in source_names:
    compile((ROOT / name).read_text(encoding="utf-8-sig"), str(ROOT / name), "exec")
import db

db.BASE = str(OUT)
db.DATA_DIR = str(OUT / "data")
db.DB_PATH = str(OUT / "data" / "agent.db")
# v0.68：账号表同步隔离——ACCOUNTS_PATH 在 import 时固化指向真实
# data/accounts.json，漏改会让 list_accounts() 读到真实账号而误亮
# 账号入口层（2026-11-12 冒烟翻车根因）。
db.ACCOUNTS_PATH = str(OUT / "data" / "accounts.json")
if not SMOKE:
    db.seed_if_empty = lambda conn: None
db.init_db()
import images_util

FONT = "D:/Adobe/Adobe Photoshop 2023/Required/PDFL/Resource/Fonts/AdobeHeitiStd-Regular.otf"
images_util._FONT_CANDIDATES = [(FONT, FONT)]
images_util._pil_font.cache_clear()
if SMOKE:
    import contextlib
    import io
    import runpy
    transcript = io.StringIO()
    try:
        with contextlib.redirect_stdout(transcript):
            runpy.run_path(str(ROOT / "tools" / "smoke.py"), run_name="__main__")
    finally:
        text = transcript.getvalue()
        (OUT / "smoke.txt").write_text(text, encoding="utf-8")
        print(text)
    sys.exit(0)
import llm
import netlayout as nl
from relationship_view import build_relationship_view
import wechat_sandbox as ws
from ui import App, NODE_W, NODE_H
import shot

checks = []
result = {}


def check(name, condition):
    if not condition:
        result["checks"] = len(checks)
        result["passed"] = checks
        result["failure"] = name
        (OUT / "failure.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        raise AssertionError(name)
    checks.append(name)
    print("[通过] " + name, flush=True)


def relationship_metrics(app, name, relationships=True):
    canvas = app.web_canvas
    routes = list(app._link_routes)
    visible_lines = canvas.find_withtag("link")
    labels = canvas.find_withtag("relationship_label")
    radiations = canvas.find_withtag("meline")
    check(name + "每个可见人物一条常驻到我连接", len(radiations) == len(app.node_pos)
          and {pid for _, pid in app._me_items} == set(app.node_pos)
          and all(canvas.itemcget(item, "state") != "hidden" for item in radiations))
    expected = nl.me_connections(app.node_pos, *app._web_center, NODE_W / 2, NODE_H / 2,
                                 radius=app.ME_R)
    by_pid = {route['pid']: route for route in expected}
    check(name + "连接路径与当前人物和缩放坐标同步", len(app._me_items) == len(by_pid) and all(
        pid in by_pid and canvas.coords(line) ==
        [v for point in by_pid[pid]['pts'] for v in app._l2s(*point)]
        for line, pid in app._me_items))
    check(name + "中心连接保持裁边直线且不含事实关系tag", all(
        len(canvas.coords(line)) == 4 and 'link' not in canvas.gettags(line)
        for line, _ in app._me_items))
    center_hits = sum(nl.seg_rect_gap(*route['pts'], point, NODE_W / 2, NODE_H / 2) < 0
                      for route in expected for pid, point in app.node_pos.items()
                      if pid != route['pid'])
    if not relationships:
        return dict(radiation_lines=len(radiations), center_line_card_hits=center_hits)
    check(name + "所有真实route线常显", len(visible_lines) == len(routes)
          and all(canvas.itemcget(item, "state") != "hidden" for item in visible_lines))
    check(name + "每对真实关系都有常驻标签", len(labels) >= len(routes)
          and all(canvas.itemcget(item, "state") != "hidden" for item in labels))
    texts = [route["lnk"].get("label") or route["lnk"].get("relation") for route in routes]
    check(name + "标签表达真实关系语义", all(text and not text.isdigit() for text in texts))
    return dict(radiation_lines=len(radiations), center_line_card_hits=center_hits, actual_routes=len(routes),
                visible_relation_lines=len(visible_lines), resident_label_items=len(labels),
                resident_label_texts=texts)


def label_card_metrics(app, name):
    canvas = app.web_canvas
    overlaps = []
    for item in canvas.find_withtag("relationship_label"):
        box = canvas.bbox(item)
        if box is None:
            continue
        for pid, node in app._node_items.items():
            card = canvas.bbox(node["item"])
            if card and (min(box[2], card[2]) > max(box[0], card[0])
                         and min(box[3], card[3]) > max(box[1], card[1])):
                overlaps.append(dict(label_item=item, person_id=pid,
                                     label_box=box, card_box=card,
                                     tags=canvas.gettags(item)))
    (OUT / 'label_card_diagnostics.json').write_text(
        json.dumps(dict(stage=name, zoom=app._zoom, overlaps=overlaps), ensure_ascii=False, indent=2),
        encoding='utf-8')
    check(name + "常驻语义标签不遮人物卡", not overlaps)
    return overlaps


def real_wheel(app, name):
    before = dict(app.node_pos)
    old_zoom = app._zoom
    with patch.object(nl, "free_layout", wraps=nl.free_layout) as layout_call, \
            patch.object(app, "refresh_network", wraps=app.refresh_network) as refresh_call:
        app.web_canvas.event_generate("<MouseWheel>", delta=-120,
                                      x=app.web_canvas.winfo_width() // 2,
                                      y=app.web_canvas.winfo_height() // 2)
        done = __import__("tkinter").BooleanVar(master=app, value=False)
        app.after(70, lambda: done.set(True))
        app.wait_variable(done)
        app.update()
        check(name + "真实滚轮延时刷新已执行", app._zoom_job is None and refresh_call.call_count >= 1)
        check(name + "真实滚轮坐标零漂移", app.node_pos == before)
        check(name + "真实滚轮不调用布局", layout_call.call_count == 0)
        check(name + "真实滚轮缩放已生效", app._zoom < old_zoom)
        relationship_metrics(app, name + '滚轮后')
        return dict(layout_calls=layout_call.call_count, refresh_calls=refresh_call.call_count,
                    drift=0, zoom_before=old_zoom, zoom_after=app._zoom)


def edge(a, b, relation="同事", ident=0, score=70):
    return dict(id=ident, person_a=a, person_b=b, relation=relation, score=score)


def people(n, kind="同事", metadata=True):
    return [dict(id=i, name="成员" + str(i), relation_type=kind,
                 notes=(llm.CIRCLE_MARK + json.dumps([dict(kind=kind, name="明确测试组")],
                                                     ensure_ascii=False)) if metadata else "")
            for i in range(1, n + 1)]


def ring(n):
    return {i + 1: (500 + 330 * math.cos(2 * math.pi * i / n),
                    450 + 330 * math.sin(2 * math.pi * i / n)) for i in range(n)}


def activate(ps, links, positions):
    helper = SimpleNamespace(_zoom=1.0)
    helper._relationship_label_anchor = lambda *args: App._relationship_label_anchor(helper, *args)
    view = build_relationship_view(ps, links, show_all=True,
                                   normalize_relation=db.normalize_link_relation)
    routes = App._route_links(helper, view['display_links'], positions, 500, 450)
    return nl.adaptive_circles(llm.relationship_circles(ps, links), routes,
                               llm.circle_internal_link)


projection_persons = [dict(id=pid, relation_type="家人", closeness=0.95) for pid in range(1, 5)]
projection_links = [edge(1, 2, "父子", ident=1), edge(2, 1, "引荐", ident=2),
                    edge(1, 2, "父子", ident=3), edge(2, 3, "合作", ident=4),
                    edge(3, 2, "合作伙伴", ident=5), edge(1, 99, "朋友", ident=6),
                    edge(2, 2, "朋友", ident=7)]
projection_snapshot = json.dumps([projection_persons, projection_links], ensure_ascii=False)
projection = build_relationship_view(projection_persons, projection_links, show_all=True,
                                     normalize_relation=db.normalize_link_relation)
check("投影对反向多记录关系聚合语义而保留事实记录", projection["labels_by_pair"][(1, 2)] == "父子 · 引荐"
      and len(projection["visible_links"]) == 5)
check("投影历史合作别名仅合并显示语义", projection["labels_by_pair"][(2, 3)] == "合作伙伴")
check("投影孤立人物保留且不凭评分虚构我与人物边", projection["person_ids"] == (1, 2, 3, 4)
      and projection["metrics"]["visible_pair_count"] == 2
      and projection["metrics"]["isolated_person_count"] == 1)
check("投影过滤悬空与自环且不修改输入", projection["metrics"]["valid_record_count"] == 5
      and json.dumps([projection_persons, projection_links], ensure_ascii=False) == projection_snapshot)
check("展示聚合每对一条且保留全部事实副本",
      len(projection['display_links']) == projection['metrics']['visible_pair_count']
      and sum(len(e['records']) for e in projection['display_links']) == len(projection['visible_links'])
      and projection['display_links'][0]['label'] == '父子 · 引荐')
projection_circle = [{'members': {1, 2, 3}}]
simplified_projection = build_relationship_view(
    projection_persons, projection_links, projection_circle,
    is_circle_internal=lambda link, circles: link['relation'] in ('合作', '合作伙伴'),
    normalize_relation=db.normalize_link_relation)
check("圈简化仅隐藏泛化事实而具体多语义完整保留",
      simplified_projection['metrics']['hidden_record_count'] == 2
      and simplified_projection['display_links'][0]['label'] == '父子 · 引荐')
all_projection = build_relationship_view(
    projection_persons, projection_links, projection_circle, show_all=True,
    is_circle_internal=lambda *args: (_ for _ in ()).throw(AssertionError('全线不应分类')),
    normalize_relation=db.normalize_link_relation)
check("show_all恢复事实但不恢复评分辐射或孤立虚构关系",
      all_projection == projection
      and all(4 not in (e['person_a'], e['person_b']) for e in all_projection['display_links'])
      and json.dumps([projection_persons, projection_links], ensure_ascii=False) == projection_snapshot)
# 行事件预算可中断无界回归，不会让两张重合固定卡挂死验收。
anchor_positions = {1: (500.0, 450.0), 2: (500.0, 450.0)}
anchor_snapshot = dict(anchor_positions)
anchor_steps = [0]


def bound_anchor(frame, event, arg):
    if event == 'line' and frame.f_code.co_name == '_relationship_label_anchor':
        anchor_steps[0] += 1
        if anchor_steps[0] > 10000:
            raise AssertionError('重合端点标签锚点超过有界执行预算')
    return bound_anchor


sys.settrace(bound_anchor)
try:
    anchor, anchor_box = App._relationship_label_anchor(
        SimpleNamespace(_zoom=1.0), edge(1, 2, '父子'),
        anchor_positions[1], anchor_positions[2], anchor_positions, 500, 450, [])
finally:
    sys.settrace(None)
check('两固定端点重合标签锚点有界返回且不移动卡片',
      all(math.isfinite(v) for v in (*anchor, *anchor_box))
      and anchor_positions == anchor_snapshot)
result['coincident_anchor'] = dict(anchor=anchor, bounds=anchor_box, steps=anchor_steps[0])
check("阈值集中为4人5对实际关系2处内部交叉",
      (nl.CIRCLE_MIN_MEMBERS, nl.CIRCLE_MIN_EDGES, nl.CIRCLE_MIN_CROSSINGS) == (4, 5, 2))
for n in (2, 3):
    ps = people(n)
    links = [edge(a, b) for a in range(1, n + 1) for b in range(a + 1, n + 1)]
    check(str(n) + "人即使有元数据和全部实际边也不激活", not activate(ps, links, ring(n)))
ps = people(4)
four_edges = [edge(1, 2), edge(2, 3), edge(3, 4), edge(4, 1)]
check("4人不足5对真实关系不激活", not activate(ps, four_edges, ring(4)))
check("历史合作别名和反向重复不能凑边数",
      not activate(people(4, "合作伙伴"),
                   [dict(e, relation="合作") for e in four_edges]
                   + [edge(e["person_b"], e["person_a"], "合作伙伴") for e in four_edges], ring(4)))
star = [edge(i, (i + 1) % 5 + 1, ident=i) for i in range(1, 6)]
perimeter = [edge(i, i % 5 + 1) for i in range(1, 6)]
check("达到人数边数但正常无交叉时保留直线", not activate(people(5), perimeter, ring(5)))
active = activate(people(5), star, ring(5))
check("泛化星形实际边交叉时激活", len(active) == 1 and active[0]["crossings"] >= 2)
check("仅具体项目边交叉时不激活",
      not activate(people(5), [dict(e, relation="具体项目") for e in star], ring(5)))
mixed_active = activate(people(5), star + [dict(e, relation="具体项目") for e in star], ring(5))
check("混合人对泛化语义能激活但不虚报具体边交叉消失",
      len(mixed_active) == 1 and mixed_active[0]['generic_crossings'] >= 2
      and mixed_active[0]['replaced_crossings'] == 0)
check("身份或元数据不能虚构实际边", not activate(people(8), [], ring(8)))
check("孤立元数据成员不凑人数", not activate(people(5), four_edges, ring(5)))
chain = [edge(i, i + 1, "合作" if i % 2 else "合作伙伴") for i in range(1, 6)]
check("无明确同圈元数据的合作链不合并", not activate(people(6, "合作伙伴", False), chain, ring(6)))
check("历史合作与合作伙伴元数据兼容",
      bool(activate(people(5, "合作"), [dict(e, relation="合作伙伴") for e in star], ring(5))))
three_gen = nl.free_layout([1, 2, 3], [(1, 2), (2, 3)], 500, 450,
                           NODE_W / 2, NODE_H / 2, generations=[(1, 2), (2, 3)])
check("三代链按长辈到晚辈递增且无卡重叠", all(
    math.dist(three_gen[a], (500, 450)) < math.dist(three_gen[b], (500, 450))
    for a, b in [(1, 2), (2, 3)]) and not nl.overlap_pairs(three_gen, NODE_W / 2, NODE_H / 2))

conn = db.get_conn()


def clear_data():
    conn.execute("DELETE FROM links")
    conn.execute("DELETE FROM interactions")
    conn.execute("DELETE FROM persons")
    conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('persons', 'links')")
    conn.commit()


clear_data()
contacts = ws.list_contacts()
for contact in contacts:
    db.save_person(ws.contact_to_person(contact))
ps = db.list_persons()
ids = {p["name"]: p["id"] for p in ps}
extra = [(c["name"], b, r) for c in contacts for b, r in ws.contact_links(c)]
for e in llm._mock_links(ps, extra):
    db.add_link(ids[e["a"]], ids[e["b"]], e["relation"], e["note"], e["score"])
for name, score in llm._mock_my_scores(ps).items():
    db.set_degree(ids[name], score["closeness"], score["sentiment"])
# 互动写入仅针对本套件隔离库，异常后必须回滚且连接可继续使用。
from datetime import date as calendar_date
interaction_pid = ps[0]['id']
interaction_id = db.add_interaction(interaction_pid, ' 电话 ', '隔离备注：一次联系')
interaction_rows = db.list_interactions(interaction_pid)
check('互动新增返回ID并保存类型备注及默认日期', len(interaction_rows) == 1
      and interaction_rows[0]['id'] == interaction_id
      and interaction_rows[0]['type'] == '电话'
      and interaction_rows[0]['note'] == '隔离备注：一次联系'
      and interaction_rows[0]['date'] == calendar_date.today().isoformat())
db.add_interaction(interaction_pid, '见面', date='2026-10-31')
check('互动指定日期及默认空备注原样保存', any(
    row['date'] == '2026-10-31' and row['note'] == '' for row in db.list_interactions(interaction_pid)))
for invalid in ('', '  ', None, 12):
    try:
        db.add_interaction(interaction_pid, invalid)
    except ValueError:
        pass
    else:
        check('无效互动类型应拒绝', False)
try:
    db.add_interaction(-999, '电话')
except ValueError:
    pass
else:
    check('缺失人物互动应拒绝', False)
check('互动无效参数不写入且事务关闭', len(db.list_interactions()) == 2 and not conn.in_transaction)


class FaultConnection:
    def __init__(self, stage):
        self.stage = stage
        self.rolled_back = self.closed = False

    def execute(self, sql, params):
        if self.stage == 'insert' and sql.startswith('INSERT INTO interactions'):
            raise RuntimeError('隔离注入插入异常')
        return conn.execute(sql, params)

    def commit(self):
        if self.stage == 'commit':
            raise RuntimeError('隔离注入提交异常')
        conn.commit()

    def rollback(self):
        self.rolled_back = True
        conn.rollback()

    def close(self):
        self.closed = True
        conn.close()


for failure_stage in ('insert', 'commit'):
    fault_conn = FaultConnection(failure_stage)
    with patch.object(db, 'get_conn', return_value=fault_conn):
        try:
            db.add_interaction(interaction_pid, '异常测试')
        except RuntimeError:
            pass
        else:
            check('异常应传播' + failure_stage, False)
    check('互动' + failure_stage + '异常回滚并关闭代理且无悬挂事务',
          fault_conn.rolled_back and fault_conn.closed and not conn.in_transaction
          and len(db.list_interactions()) == 2)
conn.execute('DELETE FROM interactions')
conn.commit()
app = App()
app.geometry("1600x920+40+40")
app.nb.select(app.tab_net)
app.update()
if getattr(app, "_resize_job", None):
    app.after_cancel(app._resize_job)
    app._resize_job = None
check("新建应用版本识别v0.69", "v0.69" in app.title())
check("新建应用默认自由二维布局", app.layout_mode == "free")
app._layout_sig = None
app._freeze_layout = False
started = time.perf_counter()
with patch.object(app, "_radial_positions", side_effect=AssertionError("free调用旧圆周入口")), \
        patch.object(nl, "auto_layers", side_effect=AssertionError("free调用旧分环入口")), \
        patch.object(nl, "free_layout", wraps=nl.free_layout) as free_call:
    app.refresh_network()
    first_ms = (time.perf_counter() - started) * 1000
    layout_args = free_call.call_args
    check("free重建只调用新布局一次且绕过旧圆周分环", free_call.call_count == 1)
positions = dict(app.node_pos)
args, kwargs = layout_args
layout_input = dict(pids=list(args[0]), edges=list(args[1]), cx=args[2], cy=args[3],
                    hw=args[4], hh=args[5], pinned=kwargs.get("pinned", {}),
                    generations=kwargs.get("generations", []), positions=positions)
(OUT / "layout_input.json").write_text(json.dumps(layout_input, ensure_ascii=False, indent=2), encoding="utf-8")
print("[布局输入] " + str(OUT / "layout_input.json"), flush=True)
all_links = [dict(e) for e in db.list_links()]
sample_view = build_relationship_view(ps, all_links, show_all=True,
                                      normalize_relation=db.normalize_link_relation)
routes = app._route_links(sample_view['display_links'], positions, *app._web_center)
check('30人展示24对且39条事实不丢失', len(sample_view['display_links']) == 24
      and sum(len(e['records']) for e in sample_view['display_links']) == len(all_links))
segments = [(r["pts"][0], r["pts"][1], {r["lnk"]["person_a"], r["lnk"]["person_b"]}) for r in routes]
crossings = sum(nl.seg_hit(a, b, c, d) for i, (a, b, pair) in enumerate(segments)
                for c, d, other in segments[i + 1:] if not pair & other)
card_hits = sum(nl.seg_rect_gap(a, b, positions[p], NODE_W / 2, NODE_H / 2) < 0
                for a, b, pair in segments for p in positions if p not in pair)
refresh_ms = []
for _ in range(3):
    started = time.perf_counter()
    app.refresh_network()
    refresh_ms.append(round((time.perf_counter() - started) * 1000, 1))
    check("30人重复刷新坐标保持" + str(len(refresh_ms)), app.node_pos == positions)
check("30人正常样例不因两三人候选圈出现色块", not app._circles)
check("30人全量实际边参与布局", len(app._layout_links) == len(all_links))
check("30人关系保持直线", all(len(r["pts"]) == 2 for r in app._link_routes))
result["sample30"] = dict(persons=len(ps), unique_edges=len(routes), records=len(all_links),
                          active_circles=len(app._circles), first_ms=round(first_ms, 1), refresh_ms=refresh_ms,
                          card_overlaps=nl.overlap_pairs(positions, NODE_W / 2, NODE_H / 2),
                          card_hits=card_hits, line_crossings=crossings,
                          **relationship_metrics(app, "30人"))
radii = [math.hypot(x - app._web_center[0], y - app._web_center[1])
         for x, y in positions.values()]
center = app._web_center
center_overlaps = [p for p, (x, y) in positions.items()
                   if abs(x - center[0]) < NODE_W / 2 + 35
                   and abs(y - center[1]) < NODE_H / 2 + 35]
result["sample30"].update(center_card_overlaps=center_overlaps)
result["sample30"]["radius_min"] = round(min(radii), 1)
result["sample30"]["radius_max"] = round(max(radii), 1)
result["sample30"]["radius_levels"] = len({round(r / 10) for r in radii})
result["sample30"]["layout_bounds"] = [round(value, 1) for value in (
    min(x for x, y in positions.values()) - NODE_W / 2,
    min(y for x, y in positions.values()) - NODE_H / 2,
    max(x for x, y in positions.values()) + NODE_W / 2,
    max(y for x, y in positions.values()) + NODE_H / 2)]
(OUT / "initial_metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print("[30人指标] " + json.dumps(result["sample30"], ensure_ascii=False), flush=True)
check("30人39条真实记录投影为24对实际关系", len(ps) == 30 and len(all_links) == 39 and len(routes) == 24)
check("30人卡片零重叠", result["sample30"]["card_overlaps"] == 0)
check("30人实际连线零穿卡", card_hits == 0)
check("30人卡片不限制在同圆周", max(radii) - min(radii) > NODE_H)
generation_pairs = kwargs.get("generations", [])
check("实际亲属长辈半径严格小于晚辈", all(
    math.dist(positions[a], center) < math.dist(positions[b], center)
    for a, b in generation_pairs))
result["sample30"]["generation_constraints"] = len(generation_pairs)
result["sample30"]["wheel"] = real_wheel(app, "30人")
app.fit_view()
app._freeze_layout = True
app.refresh_network()
app._freeze_layout = False
app.update()
shot.grab(app.winfo_id(), str(OUT / "default_30.png"))
c = app.web_canvas
vx, vy = c.canvasx(0), c.canvasy(0)
view_clipped = []
view_margins = []
for pid, (x, y) in positions.items():
    sx, sy = app._l2s(x, y)
    left, right = sx - vx - NODE_W * app._zoom / 2, sx - vx + NODE_W * app._zoom / 2
    top, bottom = sy - vy - NODE_H * app._zoom / 2, sy - vy + NODE_H * app._zoom / 2
    view_margins.append((left, c.winfo_width() - right, top, c.winfo_height() - bottom))
    if left < 0 or right > c.winfo_width() or top < 0 or bottom > c.winfo_height():
        view_clipped.append(pid)
result["sample30"].update(fit_zoom=round(app._zoom, 4), fit_clipped=view_clipped,
                          canvas_size=[c.winfo_width(), c.winfo_height()],
                          viewport_margins=[round(min(v[i] for v in view_margins), 1) for i in range(4)])
(OUT / "initial_metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
check("30人适配视图后全部卡片完整可见", not view_clipped)
legend = app._legend_frame
legend_box = (legend.winfo_rootx(), legend.winfo_rooty(),
              legend.winfo_rootx() + legend.winfo_width(),
              legend.winfo_rooty() + legend.winfo_height())
canvas_box = (c.winfo_rootx(), c.winfo_rooty(),
              c.winfo_rootx() + c.winfo_width(), c.winfo_rooty() + c.winfo_height())
legend_overlap = (min(legend_box[2], canvas_box[2]) > max(legend_box[0], canvas_box[0])
                  and min(legend_box[3], canvas_box[3]) > max(legend_box[1], canvas_box[1]))
result["sample30"].update(legend_box=legend_box, legend_canvas_overlap=legend_overlap)
check("紧凑图例存在且位于人物画布之外", bool(legend.winfo_ismapped())
      and legend.winfo_height() <= 60 and not legend_overlap)
check("30人显示与缩放不修改实际关系数据", [dict(e) for e in db.list_links()] == all_links)
result["sample30"]["label_card_overlaps"] = label_card_metrics(app, "30人适配后")

# 与修改前相同输入比较，验证局部中心线优化和稀疏分量保护。
import importlib.util
baseline_spec = importlib.util.spec_from_file_location('v053_geometry', ROOT / '_tmp/v054/baseline/netlayout.py')
baseline_geometry = importlib.util.module_from_spec(baseline_spec)
baseline_spec.loader.exec_module(baseline_geometry)
started = time.perf_counter()
new_geometry = nl.free_layout(*args, **kwargs)
geometry_ms = (time.perf_counter() - started) * 1000
old_geometry = baseline_geometry.free_layout(*args, **kwargs)
adjacency = {p: set() for p in positions}
for a, b in args[1]:
    adjacency[a].add(b)
    adjacency[b].add(a)
components, seen = [], set()
for p in sorted(adjacency):
    if p in seen:
        continue
    component = [p]
    seen.add(p)
    for q in component:
        for neighbor in sorted(adjacency[q] - seen):
            seen.add(neighbor)
            component.append(neighbor)
    components.append(set(component))
protected = set().union(*(members for members in components
    if len(members) < 4 or (len(members) < 6 and
       sum(len(adjacency[p]) for p in members) // 2 < len(members))))
def center_hits_at(pos):
    return sum(nl.seg_rect_gap(*r['pts'], point, NODE_W / 2, NODE_H / 2) < 0
               for r in nl.me_connections(pos, *center, NODE_W / 2, NODE_H / 2)
               for p, point in pos.items() if p != r['pid'])
old_hits, new_hits = center_hits_at(old_geometry), center_hits_at(new_geometry)
check('30人密集簇中心线穿卡较v053至少减半', new_hits <= old_hits // 2)
check('30人稀疏分量相对v053坐标零漂移', all(new_geometry[p] == old_geometry[p] for p in protected))
check('30人自由布局耗时保持400ms阈值', geometry_ms < 400)
check('30人新布局确定性且与界面一致', new_geometry == positions)
result['geometry_comparison'] = dict(before_center_hits=old_hits, after_center_hits=new_hits,
    layout_ms=round(geometry_ms, 1), protected_ids=sorted(protected), protected_drift=0)

# 蓝色合作伙伴、绿色同事两组均为泛化+引荐混合事实，右侧仅稀疏认识关系。
clear_data()
fixture_groups = []
for kind in ('合作伙伴', '同事'):
    group = []
    for p in people(5, kind):
        record = ws.contact_to_person(dict(name=kind + p['name'], relation_type=kind,
                                          closeness=0.7, sentiment=0.7))
        record.update(notes=p['notes'])
        group.append(db.save_person(record))
    fixture_groups.append(group)
    for i, a in enumerate(group):
        for b in group[i + 1:]:
            db.add_link(a, b, kind, score=70)
            db.add_link(a, b, '引荐', score=55)
sparse_ids = [db.save_person(ws.contact_to_person(dict(name='稀疏人物' + str(i),
    relation_type='认识', closeness=0.2, sentiment=0.5))) for i in range(3)]
db.add_link(sparse_ids[0], sparse_ids[1], '认识', score=30)
mixed_original = [dict(e) for e in db.list_links()]
app._layout_sig = None
app._freeze_layout = False
app._show_all_links.set(False)
started = time.perf_counter()
app.refresh_network()
mixed_first_ms = (time.perf_counter() - started) * 1000
check('蓝绿混合引荐密集簇均激活真实圈子', {c['kind'] for c in app._circles} == {'合作伙伴', '同事'})
check('混合簇保留全部20对引荐和稀疏认识直线', len(app._link_routes) == 21
      and sum(r['lnk']['label'] == '引荐' for r in app._link_routes) == 20)
check('混合簇交叉统计不虚报消失', all(c['replaced_crossings'] == 0 for c in app._circles))
mixed_positions = dict(app.node_pos)
mixed_default = relationship_metrics(app, '蓝绿混合簇默认')
app.fit_view()
app._freeze_layout = True
app.refresh_network()
app._freeze_layout = False
app.update()
label_card_metrics(app, '蓝绿混合簇适配后')
check('偏移标签具有连回原关系直线的引导线', all('leader' in st
    and len(app.web_canvas.coords(st['leader'])) == 4 for st in app._link_style.values()))
shot.grab(app.winfo_id(), str(OUT / 'dense_mixed_default.png'))
app._show_all_links.set(True)
app._toggle_all_links()
check('混合簇显示全部恢复41条真实记录和全部语义',
      sum(len(r['lnk']['records']) for r in app._link_routes) == 41
      and sum('引荐' in r['lnk']['label'] and ('同事' in r['lnk']['label'] or '合作伙伴' in r['lnk']['label'])
              for r in app._link_routes) == 20)
check('混合簇切换坐标和数据库事实均不改变', app.node_pos == mixed_positions
      and [dict(e) for e in db.list_links()] == mixed_original)
app.update()
shot.grab(app.winfo_id(), str(OUT / 'dense_mixed_all.png'))
result['dense_mixed'] = dict(first_ms=round(mixed_first_ms, 1), records=41,
    circles=[dict(kind=c['kind'], members=sorted(c['members']), crossings=c['crossings'],
                  replaced_crossings=c['replaced_crossings']) for c in app._circles],
    default=mixed_default, all_record_count=41)
app._show_all_links.set(False)

# 用真实隔离记录构造明确六人同圈完全图，先进行正常有界布局，再触发背景。
clear_data()
for p in people(6, "合作伙伴"):
    record = ws.contact_to_person(dict(name=p["name"], relation_type="重要合作伙伴", closeness=0.7, sentiment=0.7))
    record.update(notes=p["notes"], relation_type="重要合作伙伴")
    db.save_person(record)
ps = db.list_persons()
pids = [p["id"] for p in ps]
for i, a in enumerate(pids):
    for b in pids[i + 1:]:
        db.add_link(a, b, "合作伙伴", score=70)
legacy_id = conn.execute("INSERT INTO links(person_a,person_b,relation,note,score) VALUES(?,?,'合作','历史记录',90)",
                         (pids[0], pids[1])).lastrowid
conn.commit()
before = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
check("db.add_link历史合作别名复用旧ID", db.add_link(pids[1], pids[0], "合作", score=88) in
      [row[0] for row in conn.execute("SELECT id FROM links WHERE person_a=? AND person_b=?",
                                     (pids[0], pids[1]))])
check("db.add_link别名反向更新不新增记录", conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == before)
# 一条具体项目记录始终显示，剩余泛化交叉仍足以构成背景。
db.add_link(pids[0], pids[1], "联合研发项目", score=45)
original = [dict(e) for e in db.list_links()]
app._layout_sig = None
app._freeze_layout = False
started = time.perf_counter()
app.refresh_network()
result["complex_first_ms"] = round((time.perf_counter() - started) * 1000, 1)
check("六人复杂图完成正常布局后激活色块", bool(app._circles))
result['complex_initial_show_all'] = bool(app._show_all_links.get())
check('默认复杂圈简化泛化事实且保留具体关系', not app._show_all_links.get()
      and len(app._link_routes) == 1
      and app._link_routes[0]['lnk']['label'] == '联合研发项目')
app.update()
shot.grab(app.winfo_id(), str(OUT / 'complex_default.png'))
result['complex_default'] = relationship_metrics(app, '复杂圈默认')
app._show_all_links.set(True)
app._toggle_all_links()
complex_view = build_relationship_view(ps, original, app._circles, show_all=True,
                                      normalize_relation=db.normalize_link_relation)
check('复杂圈17条事实聚合为15对展示且语义不丢失', len(complex_view['display_links']) == 15
      and len(complex_view['visible_links']) == 17
      and sum(len(e['records']) for e in complex_view['display_links']) == 17
      and any('联合研发项目' in e['label'] and '合作伙伴' in e['label']
              for e in complex_view['display_links']))
check("复杂圈全部15对真实关系常显", len(app._link_routes) == 15)
check("复杂圈泛化与具体实际关系均保留", any(
    llm.circle_internal_link(r["lnk"], app._circles) for r in app._link_routes))
check("具体项目低分事实聚合语义仍保留", any('联合研发项目' in r['lnk']['label']
      and any(record['relation'] == '联合研发项目' for record in r['lnk']['records'])
      for r in app._link_routes))
check("布局缓存保持全量记录", app._layout_links == original)
complex_positions = dict(app.node_pos)
app.fit_view()
app._freeze_layout = True
app.refresh_network()
app._freeze_layout = False
app.update()
shot.grab(app.winfo_id(), str(OUT / "complex_adaptive.png"))
result["complex"] = dict(unique_edges=15, records=len(original), displayed_edges=len(app._link_routes),
                         **relationship_metrics(app, "复杂圈"),
                         active=[dict(kind=z["kind"], members=sorted(z["members"]),
                                      edge_count=z["edge_count"], crossings=z["crossings"],
                                      replaced_crossings=z["replaced_crossings"]) for z in app._circles])
result["complex"]["label_card_overlaps"] = label_card_metrics(app, "复杂圈适配后")
active_signature = [(c["kind"], c["members"], c["crossings"]) for c in app._circles]
app._show_all_links.set(True)
app._toggle_all_links()
check("显示全部连线恢复15对实际关系", len(app._link_routes) == 15)
check("切换全部连线不重排", app.node_pos == complex_positions)
check("全线切换激活快照不循环变化", [(c["kind"], c["members"], c["crossings"]) for c in app._circles] == active_signature)
app.update()
shot.grab(app.winfo_id(), str(OUT / "complex_all_lines.png"))
app._show_all_links.set(False)
app._toggle_all_links()
check('关闭全线后复杂圈只显示具体项目一对', len(app._link_routes) == 1
      and app._link_routes[0]['lnk']['label'] == '联合研发项目')
result['complex_simplified'] = relationship_metrics(app, '复杂圈简化')
app.update()
shot.grab(app.winfo_id(), str(OUT / 'complex_simplified.png'))
member = pids[2]
x, y = app.node_pos[member]
sx, sy = app._l2s(x, y)
c = app.web_canvas
ex, ey = sx - c.canvasx(0), sy - c.canvasy(0)
entry = next(e for e in app._circle_items if member in e["circle"]["members"])
hull_before = c.coords(entry["polygon"])
app._drag = dict(pid=member, moved=False, node_x0=x, node_y0=y, press_x=sx, press_y=sy)
app._panning = None
app.on_drag(SimpleNamespace(x=ex + 60, y=ey + 35))
relationship_metrics(app, '拖动过程中', relationships=False)
check("拖动过程中色块跟随卡片", c.coords(entry["polygon"]) != hull_before)
check("拖动过程中不反复切换激活", [(z["kind"], z["members"], z["crossings"]) for z in app._circles] == active_signature)
check("拖动其他坐标不变", all(app.node_pos[p] == q for p, q in complex_positions.items() if p != member))
app.on_release(SimpleNamespace(x=ex + 60, y=ey + 35))
dragged = dict(app.node_pos)
saved = db.get_person(member)
check("拖动位置已存为pinned", saved["pos_x"] is not None and saved["pos_y"] is not None)
after_drag = [(z["kind"], z["members"], z["crossings"]) for z in app._circles]
for _ in range(2):
    app.refresh_network()
    check("松手刷新复用坐标" + str(_), app.node_pos == dragged)
    check("松手刷新背景不循环" + str(_), [(z["kind"], z["members"], z["crossings"]) for z in app._circles] == after_drag)
result["pinned_wheel"] = real_wheel(app, "固定卡")
check("缩放不重排坐标", app.node_pos == dragged)
check("缩放不改变激活快照", [(z["kind"], z["members"], z["crossings"]) for z in app._circles] == after_drag)
app._layout_sig = None
app.refresh_network()
persisted_position = (saved["pos_x"] * c.winfo_width(), saved["pos_y"] * c.winfo_height())
pinned_drift = math.dist(app.node_pos[member], dragged[member])
result["pinned_rebuild"] = dict(before=dragged[member], after=app.node_pos[member],
                                persisted=persisted_position, drift=pinned_drift)
(OUT / "pinned_rebuild.json").write_text(json.dumps(result["pinned_rebuild"], indent=2), encoding="utf-8")
check("强制重建精确保留持久化固定坐标", app.node_pos[member] == persisted_position)
check("强制重建拖拽位置仅有浮点舍入误差", pinned_drift < 1e-9)
check("所有数据记录未删除修改", [dict(e) for e in db.list_links()] == original)
check("复杂圈人物数据完整", {p["id"] for p in db.list_persons()} == set(pids))
result["after_pinned"] = relationship_metrics(app, "固定刷新后")

# 用隔离事实和替代菜单验证聚合菜单捕获每一条真实ID，不进入模态弹出。
class CaptureMenu:
    created = []

    def __init__(self, *args, **kwargs):
        self.commands = []
        self.created.append(self)

    def add_command(self, **kwargs):
        self.commands.append(kwargs)

    def add_separator(self):
        pass

    def add_cascade(self, **kwargs):
        self.cascade = kwargs['menu']

    def tk_popup(self, *args):
        pass

    def grab_release(self):
        pass


menu_ids = []
for a, b, relation, note in [(pids[0], pids[1], '父子', '保留父子'),
                              (pids[1], pids[0], '引荐', '仅删第二条'),
                              (pids[0], pids[1], '引荐', '不同方向备注')]:
    menu_ids.append(conn.execute(
        'INSERT INTO links(person_a,person_b,relation,note,score) VALUES(?,?,?,?,?)',
        (a, b, relation, note, 50)).lastrowid)
conn.commit()
menu_records = [r for r in db.list_links() if r['id'] in menu_ids]
menu_view = build_relationship_view(ps, menu_records, show_all=True)
menu_link = menu_view['display_links'][0]
menu_helper = SimpleNamespace(_link_style={menu_link['id']: {'route': {'lnk': menu_link}}},
                              open_profile=lambda pid: None,
                              confirm_delete_link=app.confirm_delete_link)
with patch('ui.tk.Menu', CaptureMenu):
    App._link_menu(menu_helper, SimpleNamespace(x_root=0, y_root=0), menu_link['id'])
delete_commands = CaptureMenu.created[-1].commands
check('聚合菜单每条事实都可区分方向备注及ID', len(delete_commands) == 3
      and len({e['label'] for e in delete_commands}) == 3
      and all('ID ' + str(pid) in entry['label'] for pid, entry in zip(menu_ids, delete_commands)))
with patch.object(app, '_ask', return_value=False):
    delete_commands[1]['command']()
check('聚合关系拒绝确认不删除任何记录',
      {r['id'] for r in db.list_links() if r['id'] in menu_ids} == set(menu_ids))
with patch.object(app, '_ask', return_value=True):
    delete_commands[1]['command']()
remaining_menu_records = [r for r in db.list_links() if r['id'] in menu_ids]
check('聚合菜单选择第二条仅删除指定ID且父子保留',
      {r['id'] for r in remaining_menu_records} == {menu_ids[0], menu_ids[2]}
      and any(r['relation'] == '父子' for r in remaining_menu_records))
for pid in menu_ids:
    conn.execute('DELETE FROM links WHERE id=?', (pid,))
conn.commit()
app.refresh_network()
check('删除菜单验收清理后原事实完整', db.list_links() == original)
for label_item in app.web_canvas.find_withtag('relationship_label'):
    tags = app.web_canvas.gettags(label_item)
    check('聚合语义胶囊具有关系右键命中tag', any(tag.startswith('link_') for tag in tags))
    point = app.web_canvas.coords(label_item)
    if len(point) == 2:
        check('偏移胶囊锚点可命中关系菜单对象', app._link_at(*point) is not None)
# 维护闭环只操作隔离库；显式保存一次，取消、预览与复制均不记为联系。
contact_positions = dict(app.node_pos)
contact_count = len(db.list_interactions())
contact_win = app.record_contact(member)
check('联系表单打开不写互动且默认今天', len(db.list_interactions()) == contact_count
      and contact_win._contact_fields['date'].get() == calendar_date.today().isoformat())
contact_win._cancel_contact()
check('联系表单取消不写互动', len(db.list_interactions()) == contact_count)
contact_win = app.record_contact(member)
fields = contact_win._contact_fields
fields['date'].delete(0, 'end')
fields['date'].insert(0, '2026-02-30')
with patch.object(app, '_error') as error_call:
    contact_win._save_contact()
    check('联系表单无效日期拒绝且不写', error_call.call_count == 1
          and len(db.list_interactions()) == contact_count and contact_win.winfo_exists())
fields['date'].delete(0, 'end')
fields['date'].insert(0, '2026-10-31')
fields['type'].set(' ')
with patch.object(app, '_error') as error_call:
    contact_win._save_contact()
    check('联系表单空类型拒绝且不写', error_call.call_count == 1
          and len(db.list_interactions()) == contact_count)
fields['type'].set('见面')
fields['note'].insert('1.0', '隔离表单显式确认备注')
contact_win._save_contact()
contact_rows = db.list_interactions(member)
check('联系表单确认仅保存一次且归属日期类型备注正确', len(db.list_interactions()) == contact_count + 1
      and len(contact_rows) == 1 and contact_rows[0]['date'] == '2026-10-31'
      and contact_rows[0]['type'] == '见面' and contact_rows[0]['note'] == '隔离表单显式确认备注')
check('联系保存刷新不移动固定及其他人物', app.node_pos == contact_positions)
with patch.object(db, 'list_interactions', wraps=db.list_interactions) as history_call:
    app.open_profile(member)
    check('资料卡读取已保存联系历史', any(call.args == (member,) for call in history_call.call_args_list))
for child in app.winfo_children():
    if getattr(child, '_contact_pid', None) == member:
        child.destroy()
check('缺失人物不创建联系表单', app.record_contact(-999) is None)
# 两个提醒显示相同姓名，点击回调仍各自保留真实pid。
reminder_items = [dict(pid=pid, name='同名提醒', _p=dict(birthday=calendar_date.today().isoformat(),
                  important_dates='[]')) for pid in pids[:2]]
app._refresh_reminders(reminder_items)
app.update()
with patch.object(app, 'open_profile') as profile_call:
    for row in app.remind_host.winfo_children():
        row.event_generate('<Button-1>', x=5, y=5)
        app.update()
    check('同名提醒逐行按真实pid跳转', [call.args[0] for call in profile_call.call_args_list] == pids[:2])
with patch('ui.craft_one', return_value=('隔离祝福预览', 'local')), \
        patch.object(app, 'clipboard_clear'), patch.object(app, 'clipboard_append'):
    app.go_greet(member)
    app.gr_holiday.set('生日')
    app.gen_greeting()
    app.copy_greeting()
    app.go_gift(member)
check('祝福预览复制与礼物导航均不自动记录联系', len(db.list_interactions()) == contact_count + 1)
app.nb.select(app.tab_net)
# 同一份实际星形边，改为其邻接循环顺序的五边形便可消除交叉。
plain = ring(5)
uncrossed = {pid: plain[i + 1] for i, pid in enumerate((1, 3, 5, 2, 4))}
check("同一实际边集交叉消失撤销背景", not activate(people(5), star, uncrossed))
facts_before = [dict(e) for e in db.list_links()]
items = app._web_items()[:3]
for item, score in zip(items, (95, 5, 0)):
    item['score'] = score
items[-1]['_p'] = dict(items[-1]['_p'], closeness=None, sentiment=None)
with patch.object(db, 'list_links', return_value=[]), patch.object(app, '_web_items', return_value=items):
    app._layout_sig = None
    app._draw_web(items)
    result['isolated_mixed_scores'] = relationship_metrics(app, '无links高低分未评分')
    check('无links孤立人物保持三人而无事实关系线', len(app.node_pos) == 3 and not app._link_routes)
    app._anim_on = True
    app._start_anim()
    app._anim_step()
    relationship_metrics(app, '动画帧后')
    app._stop_anim()
    app._anim_on = False
    app._on_canvas_resize(SimpleNamespace())
    relationship_metrics(app, '窗口缩放即时阶段', relationships=False)
    if app._resize_job:
        app.after_cancel(app._resize_job)
        app._resize_job = None
    app.update()
    shot.grab(app.winfo_id(), str(OUT / 'isolated_mixed_scores.png'))
check('中心连接不向隔离数据库新增links', [dict(e) for e in db.list_links()] == facts_before)
with patch.object(db, "list_links", return_value=[]):
    old_items = app._web_items
    app._web_items = lambda: []
    app._draw_web([])
    check("空图清空激活与全边缓存", not app._circles and not app._all_links_cache and not app._layout_links)
    check('空图无中心连线残留', not app._me_items and not app.web_canvas.find_withtag('meline'))
    app._web_items = old_items
app.layout_mode = "ring"
with patch.object(app, "_ask", return_value=True), \
        patch.object(app, "_radial_positions", side_effect=AssertionError("重置调用旧圆周入口")), \
        patch.object(nl, "auto_layers", side_effect=AssertionError("重置调用旧分环入口")):
    app.reset_layout()
check("自动重置回到free并绕过旧圆周分环", app.layout_mode == "free")
result["checks"] = len(checks)
result["passed"] = checks
(OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False), flush=True)
app.destroy()
