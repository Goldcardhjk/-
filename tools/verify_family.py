# -*- coding: utf-8 -*-
"""D盘独立库验证：亲属代际、圈子凸包和交互；支持 --smoke。"""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import runpy
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
parser = argparse.ArgumentParser()
parser.add_argument("--case", default="adjacent",
                    choices=["baseline", "adjacent", "three", "shared", "pinned", "conflict"])
parser.add_argument("--smoke", action="store_true")
parser.add_argument("--circles", action="store_true")
args = parser.parse_args()
OUT = ROOT / "_tmp" / "v049" / ("smoke" if args.smoke else ("circles" if args.circles else args.case))
OUT.mkdir(parents=True, exist_ok=True)
for key in ("TMP", "TEMP", "TMPDIR"):
    os.environ[key] = str(ROOT / "_tmp")
os.environ["PYTHONPYCACHEPREFIX"] = str(ROOT / "_tmp" / "pycache")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.chdir(ROOT)

# 禁止本次验证代码打开 C 盘文件。Windows 图形系统自身的 DLL/字体加载由系统管理。
def audit(event, values):
    if event == "sqlite3.connect" and values and values[0] != ":memory:":
        target = Path(os.fsdecode(values[0])).resolve()
        if OUT.resolve() not in target.parents:
            raise RuntimeError("验证禁止连接非隔离数据库：" + str(target))
    if event in ("open", "os.listdir", "os.scandir") and values:
        value = values[0]
        if isinstance(value, (str, bytes, os.PathLike)):
            path = os.fsdecode(value).replace("\\", "/")
            if path.upper().startswith("C:"):
                raise RuntimeError("验证禁止访问 C 盘：" + path)
sys.addaudithook(audit)

import db
db.BASE = str(OUT)
db.DATA_DIR = str(OUT / "data")
db.DB_PATH = str(OUT / "data" / "agent.db")
if not args.smoke:
    db.seed_if_empty = lambda conn: None
db.init_db()
import images_util
FONT = "D:/Adobe/Adobe Photoshop 2023/Required/PDFL/Resource/Fonts/AdobeHeitiStd-Regular.otf"
images_util._FONT_CANDIDATES = [(FONT, FONT)]
images_util._pil_font.cache_clear()

if args.smoke:
    import contextlib
    import io
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
import wechat_sandbox as ws
import netlayout as nl

contacts = ws.list_contacts()
if args.case in ("three", "shared", "pinned", "conflict"):
    contacts = [c for c in contacts if c["group"] == "家人"]
if args.case == "shared":
    # 明确的多长辈共享小辈：奶奶的子女叔叔、姑姑；堂弟是叔叔的儿子/姑姑的侄子。
    aunt = dict(next(c for c in contacts if c["name"] == "小姨"))
    aunt.update(name="姑姑", remark="叔叔的姐姐", intro_by="", links=[],
                kinship={"family": "演示家庭", "generation": 1,
                         "younger": [{"name": "堂弟小宇", "relation": "姑侄"}]})
    contacts = [c for c in contacts if c["name"] in ("奶奶", "叔叔", "堂弟小宇")]
    contacts.append(aunt)
    contacts[0] = dict(contacts[0])
    contacts[0]["kinship"] = {"family": "演示家庭", "generation": 0,
                              "younger": [{"name": "叔叔", "relation": "母子"},
                                          {"name": "姑姑", "relation": "母女"}]}

conn = db.get_conn()
conn.execute("DELETE FROM links")
conn.execute("DELETE FROM persons")
conn.commit()
for contact in contacts:
    db.save_person(ws.contact_to_person(contact))
persons = db.list_persons()
name2id = {p["name"]: p["id"] for p in persons}
extra = [(c["name"], b, r) for c in contacts for b, r in ws.contact_links(c)]
constraints = llm.family_constraints(persons)
family_links = llm.explicit_family_links(persons)
assert all(abs(llm.family_metadata(next(p for p in persons if p["name"] == e["a"]))["generation"]
               - llm.family_metadata(next(p for p in persons if p["name"] == e["b"]))["generation"]) == 1
           for e in family_links)
assert llm._mock_links([{"name": "甲", "relation_type": "家人"},
                        {"name": "乙", "relation_type": "家人"}]) == []
# API 返回的无依据家庭边也必须被过滤。
old_mode, old_api = llm.mode, llm._api_judge
llm.mode = lambda: "api"
llm._api_judge = lambda *a: {"links": [{"a": "甲", "b": "乙", "relation": "家人"}]}
assert not llm.judge_all([{"name": "甲"}, {"name": "乙"}])["links"]
llm.mode, llm._api_judge = old_mode, old_api

if args.case == "baseline":
    for module in ("llm", "netlayout", "ui"):
        path = ROOT / "_tmp" / "v046_baseline" / (module + ".py")
        spec = importlib.util.spec_from_file_location(module, path)
        obj = importlib.util.module_from_spec(spec)
        sys.modules[module] = obj
        spec.loader.exec_module(obj)
    generator = sys.modules["llm"]
else:
    generator = llm
links = generator._mock_links(persons, extra)
for link in links:
    if link["a"] in name2id and link["b"] in name2id:
        db.add_link(name2id[link["a"]], name2id[link["b"]], link["relation"],
                    link["note"], link["score"])
if args.circles:
    # 仅在隔离库造历史记录：同人对高分旧泛化边不能盖过具体项目边。
    conn.executemany(
        "INSERT INTO links (person_a, person_b, relation, note, score) VALUES (?,?,'合作',?,?)",
        [(name2id["王教授"], name2id["李博士"], "历史旧合作样例", 99),
         (name2id["王教授"], name2id["赵总"], "历史旧合作样例", 97)])
    conn.commit()
    # 新建别名须复用真实旧ID，原relation保持不变；新记录统一规范名。
    a, b = name2id["李博士"], name2id["张老师"]
    legacy_id = conn.execute(
        "INSERT INTO links (person_a, person_b, relation, note, score) VALUES (?,?,'合作',?,?)",
        (a, b, "别名去重旧记录", 51)).lastrowid
    conn.commit()
    before_count = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    assert db.add_link(b, a, "合作伙伴", "更新备注", score=83) == legacy_id
    assert db.add_link(a, b, "合作", "更新备注", score=84) == legacy_id
    assert conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == before_count
    row = conn.execute("SELECT relation,note,score FROM links WHERE id=?", (legacy_id,)).fetchone()
    assert tuple(row) == ("合作", "更新备注", 84)
    conn.execute("DELETE FROM links WHERE id=?", (legacy_id,))
    conn.commit()
    normalized_id = db.add_link(a, b, "合作", score=71)
    assert conn.execute("SELECT relation FROM links WHERE id=?", (normalized_id,)).fetchone()[0] == "合作伙伴"
    assert db.add_link(b, a, "合作伙伴", score=72) == normalized_id
    conn.execute("DELETE FROM links WHERE id=?", (normalized_id,))
    conn.commit()
for name, score in generator._mock_my_scores(persons).items():
    db.set_degree(name2id[name], score["closeness"], score["sentiment"])

from ui import App, NODE_W, NODE_H
# 构建后在真实尺寸显式计时，取消异步尺寸刷新，避免混入启动预览。
app = App()
if not args.circles:
    app._show_all_links.set(True)
app.geometry("1600x920+40+40")
app.nb.select(app.tab_net)
app.update()
if getattr(app, "_resize_job", None):
    app.after_cancel(app._resize_job)
    app._resize_job = None
app.layout_mode = "radial"
app._layout_sig = None
app._freeze_layout = False
if args.case in ("pinned", "conflict"):
    db.set_position(name2id["奶奶"], 0.75, 0.55)
    if args.case == "conflict":
        db.set_position(name2id["叔叔"], 0.55, 0.55)
t0 = time.perf_counter()
app.refresh_network()
first_ms = (time.perf_counter() - t0) * 1000
pos = dict(app.node_pos)
center = app._web_center
hw, hh = NODE_W / 2, NODE_H / 2
radii = {p: math.dist(q, center) for p, q in pos.items()}
routes = app._link_routes
edges = sorted({tuple(sorted((r["lnk"]["person_a"], r["lnk"]["person_b"]))) for r in routes})
segments = [(r["pts"][0], r["pts"][1], r["lnk"]["person_a"], r["lnk"]["person_b"]) for r in routes]
card_hits = [(a, b, p) for u, v, a, b in segments for p in pos
             if p not in (a, b) and nl.seg_rect_gap(u, v, pos[p], hw, hh) < 0]
cross = sum(nl.seg_hit(u, v, x, y)
            for i, (u, v, a, b) in enumerate(segments)
            for x, y, c, d in segments[i+1:] if not {a, b} & {c, d})
spokes = sum(nl.seg_hit(u, v, center, q)
             for u, v, a, b in segments for p, q in pos.items() if p not in (a, b))
violations = [(a, b) for a, b in constraints if radii[b] < radii[a] + 184 - 1e-5]
fixed = {p["id"]: (p["pos_x"] * app.web_canvas.winfo_width(),
                   p["pos_y"] * app.web_canvas.winfo_height())
         for p in db.list_persons() if p.get("pos_x") is not None}
assert all(math.dist(pos[p], q) < 1e-6 for p, q in fixed.items())
if args.case != "baseline" and args.case != "conflict":
    assert not violations, violations
expected_links = [e for e in db.list_links() if not args.circles
                  or not llm.circle_internal_link(e, app._circles)]
assert len(edges) == len({tuple(sorted((e["person_a"], e["person_b"]))) for e in expected_links})
assert all(len(r["pts"]) == 2 for r in routes), "关系线必须为直线两端点"
refresh_times = []
reused = True
for _ in range(3):
    t0 = time.perf_counter()
    app.refresh_network()
    refresh_times.append((time.perf_counter() - t0) * 1000)
    reused = reused and app.node_pos == pos
    if args.case != "baseline":
        assert app.node_pos == pos, "刷新位置变化"
app._zoom *= 0.85
app._draw_web()
reused = reused and app.node_pos == pos
if args.case != "baseline":
    assert app.node_pos == pos, "缩放位置变化"
# 截图与指标对应同一份首次布局。
app.node_pos = pos
app._freeze_layout = True
app.fit_view()
app._freeze_layout = True
app.refresh_network()
app.update_idletasks()
import shot
shot.grab(app.winfo_id(), str(OUT / "network.png"))
result = {"case": args.case, "persons": len(persons), "edges": len(edges),
          "family_edges": sum(e["relation"] in llm.FAMILY_RELATIONS for e in links),
          "overlap_pairs": nl.overlap_pairs(pos, hw, hh), "card_hits": len(card_hits),
          "line_crossings": cross, "spoke_crossings": spokes,
          "generation_violations": len(violations),
          "minimum_gap_px": nl.min_gap(pos, edges, list(pos), hw, hh),
          "first_ms": round(first_ms, 1),
          "refresh_ms": [round(t, 1) for t in refresh_times],
          "pinned_preserved": True, "refresh_zoom_reused": reused,
          "radii": {p["name"]: round(radii[p["id"]], 2) for p in persons},
          "violations": violations}
(OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
if args.circles:
    from types import SimpleNamespace
    original = [dict(e) for e in db.list_links()]
    assert "合作伙伴" in db.RELATION_LINK_TYPES and "合作" not in db.RELATION_LINK_TYPES
    assert "重要合作伙伴" in db.RELATION_TYPES and llm.REL_BASE["重要合作伙伴"] == 78
    partner_records = [e for e in original if e["relation"] in {"合作", "合作伙伴"}]
    assert {e["relation"] for e in partner_records} == {"合作", "合作伙伴"}
    assert all(llm.circle_internal_link(e, app._circles) for e in partner_records)
    assert any(c["kind"] == "合作伙伴" and len(c["members"]) == 3 for c in app._circles)
    assert all("合作 ·" not in c["label"] for c in app._circles)
    assert not any(r["lnk"]["id"] in {e["id"] for e in partner_records}
                   for r in app._link_routes)
    for rel in ("夏令营课程项目", "包装采购交易"):
        assert any(r["lnk"]["relation"] == rel for r in app._link_routes), rel
    # 同等级伙伴没有明确边/元数据时，既不补边也不成圈。
    unrelated = [{"id": 1, "name": "甲", "relation_type": "重要合作伙伴"},
                 {"id": 2, "name": "乙", "relation_type": "重要合作伙伴"}]
    assert not llm._mock_links(unrelated) and not llm.relationship_circles(unrelated, [])
    assert llm._mock_links(unrelated, [("甲", "乙", "合作"),
                                      ("乙", "甲", "合作伙伴")])[0]["relation"] == "合作伙伴"
    assert len(llm._mock_links(unrelated, [("甲", "乙", "合作"),
                                          ("乙", "甲", "合作伙伴")])) == 1
    metadata_people = [dict(p, notes=llm.CIRCLE_MARK + json.dumps(
        [{"kind": "合作" if p["id"] == 1 else "合作伙伴", "name": "明确合作组"}],
        ensure_ascii=False)) for p in unrelated]
    assert llm.relationship_circles(metadata_people, [])[0]["kind"] == "合作伙伴"
    old_mode, old_api = llm.mode, llm._api_judge
    try:
        llm.mode = lambda: "api"
        llm._api_judge = lambda *a: {"links": [{"a": "甲", "b": "乙", "relation": "合作"}]}
        assert llm.judge_all(unrelated, [("甲", "乙", "合作")])["links"][0]["relation"] == "合作伙伴"
    finally:
        llm.mode, llm._api_judge = old_mode, old_api
    result.update(partner_legacy_records=len([e for e in partner_records if e["relation"] == "合作"]),
                  partner_new_records=len([e for e in partner_records if e["relation"] == "合作伙伴"]),
                  partner_generic_hidden=True, partner_metadata_compatible=True,
                  partner_no_category_inference=True, partner_mock_api_normalized=True,
                  specific_project_transaction_preserved=True)
    t0 = time.perf_counter()
    for _ in range(100):
        app._update_circle_regions(name2id["蔡经理"])
    result["hull_update_ms"] = round((time.perf_counter() - t0) * 10, 3)
    memberships = [e["members"] for e in app._circles]
    stable_positions = dict(app.node_pos)
    member = name2id["蔡经理"]
    untouched = {e["polygon"]: e["hull"] for e in app._circle_items
                 if member not in e["circle"]["members"]}
    app._update_circle_regions(member)
    assert all(e["hull"] is untouched[e["polygon"]] for e in app._circle_items
               if e["polygon"] in untouched), "拖动不应重算其他圈子的凸包"
    # 区域部分越过左边/上边界时，标签仍须完整可见。
    circle = next(e for e in app._circle_items if member in e["circle"]["members"])
    original_hull = [app._l2s(*p) for p in circle["hull"]]
    c = app.web_canvas
    vx, vy = c.canvasx(0), c.canvasy(0)
    dx = vx - min(p[0] for p in original_hull) - 12
    dy = vy - min(p[1] for p in original_hull) - 12
    for shift_x, shift_y in ((dx, 0), (0, dy), (dx, dy)):
        app.node_pos = dict(stable_positions)
        for p in circle["circle"]["members"]:
            x, y = stable_positions[p]
            app.node_pos[p] = (x + shift_x / app._zoom, y + shift_y / app._zoom)
        app._draw_web()
        entry = next(e for e in app._circle_items if member in e["circle"]["members"])
        box = c.coords(entry["background"])
        assert vx <= box[0] < box[2] <= vx + c.winfo_width(), box
        assert vy <= box[1] < box[3] <= vy + c.winfo_height(), box
        legend = c.bbox("legend")
        assert not (box[0] < legend[2] and box[2] > legend[0]
                    and box[1] < legend[3] and box[3] > legend[1]), (box, legend)
    app.update_idletasks()
    shot.grab(app.winfo_id(), str(OUT / "boundary.png"))
    app.node_pos = stable_positions
    app._draw_web()
    result["partial_region_labels_visible"] = True
    result["unaffected_hulls_reused"] = True
    overlap_pos = dict(app.node_pos)
    app.node_pos[name2id["小美"]] = app.node_pos[name2id["孙秘书"]]
    app._freeze_layout = True
    app._draw_web()
    assert [e["members"] for e in app._circles] == memberships
    label_boxes = [app.web_canvas.bbox(e["background"]) for e in app._circle_items]
    assert not any(a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
                   for i, a in enumerate(label_boxes) for b in label_boxes[i+1:])
    app.update_idletasks()
    shot.grab(app.winfo_id(), str(OUT / "overlap.png"))
    app.node_pos = overlap_pos
    app._draw_web()
    app._freeze_layout = False
    result["overlap_memberships_preserved"] = True
    result["overlap_labels_separated"] = True
    assert llm._mock_links([{"name": "甲", "relation_type": "同事"},
                            {"name": "乙", "relation_type": "同事"}]) == []
    assert not llm.relationship_circles(
        [{"id": 1, "name": "甲", "relation_type": "同事"},
         {"id": 2, "name": "乙", "relation_type": "同事"}], [])
    family_sets = [c["members"] for c in app._circles if c["kind"] == "家人"]
    assert not any({name2id["奶奶"], name2id["小姨"]} <= m for m in family_sets)
    assert any(c["kind"] == "同学" for c in app._circles)
    assert any(c["kind"] == "同事" and len(c["members"]) == 3 for c in app._circles)
    for e in original:
        if e["relation"] in {"母子", "母女", "父子", "叔侄", "上下级"}:
            assert any({e["person_a"], e["person_b"]} ==
                       {r["lnk"]["person_a"], r["lnk"]["person_b"]} for r in app._link_routes)
    # 边链不能误推成三人相识；具体边不能被同圈背景吞掉。
    sample_people = [{"id": i, "name": str(i)} for i in (1, 2, 3)]
    sample_edges = [{"person_a": 1, "person_b": 2, "relation": "同事"},
                    {"person_a": 2, "person_b": 3, "relation": "同事"}]
    sample_circles = llm.relationship_circles(sample_people, sample_edges)
    assert len(sample_circles) == 2 and all(len(c["members"]) == 2 for c in sample_circles)
    assert not llm.circle_internal_link({"person_a": 1, "person_b": 3,
                                         "relation": "同事"}, sample_circles)
    assert not llm.circle_internal_link({"person_a": 1, "person_b": 2,
                                         "relation": "上下级"}, sample_circles)
    partner_chain = [dict(e, relation="合作" if i == 0 else "合作伙伴")
                     for i, e in enumerate(sample_edges)]
    chain_circles = llm.relationship_circles(sample_people, partner_chain)
    assert len(chain_circles) == 2 and all(len(c["members"]) == 2 for c in chain_circles)
    assert all(c["kind"] == "合作伙伴" for c in chain_circles)
    assert not llm.circle_internal_link({"person_a": 1, "person_b": 3,
                                         "relation": "合作伙伴"}, chain_circles)
    result["partner_chain_not_merged"] = True
    hull = nl.card_hull([(0, 0), (200, 100)], 77, 40, 22)
    for x, y in [(x + dx, y + dy) for x, y in [(0, 0), (200, 100)]
                 for dx in (-77, 77) for dy in (-40, 40)]:
        assert all((b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]) >= 0
                   for a, b in zip(hull, hull[1:] + hull[:1]))
    fine_positions = dict(app.node_pos)
    layout_ids = {e["id"] for e in app._layout_links}
    app._show_all_links.set(True)
    t0 = time.perf_counter()
    app._toggle_all_links()
    toggle_ms = (time.perf_counter() - t0) * 1000
    assert app.node_pos == fine_positions
    assert {e["id"] for e in app._layout_links} == layout_ids
    assert len(app._link_routes) == len({tuple(sorted((e["person_a"], e["person_b"]))) for e in original})
    assert any(db.normalize_link_relation(r["lnk"]["relation"]) == "合作伙伴"
               for r in app._link_routes)
    assert any(r["lnk"]["relation"] == "夏令营课程项目" for r in app._link_routes)
    legacy_pair = {name2id["王教授"], name2id["李博士"]}
    assert next(r["lnk"]["relation"] for r in app._link_routes
                if {r["lnk"]["person_a"], r["lnk"]["person_b"]} == legacy_pair) == "夏令营课程项目"
    result["legacy_generic_does_not_override_specific"] = True
    result["partner_all_links_restored"] = True
    from unittest.mock import patch
    class ChatTextProbe:
        configure = lambda *a, **kw: None
        get = lambda *a: ""
    class ChatProbe:
        chat_text = ChatTextProbe()
        _chat_add = lambda *a: None
        refresh_network = lambda *a: None
    chat_edge = next(e for e in original if e["relation"] == "合作")
    with patch.object(db, "add_link", return_value=chat_edge["id"]) as chat_add_link:
        app._chat_apply.__func__(ChatProbe(), "测试", [
            {"op": "add_link", "a": chat_edge["name_a"], "b": chat_edge["name_b"],
             "relation": "合作", "score": chat_edge["score"]}])
        assert chat_add_link.call_args.kwargs == {"score": chat_edge["score"]}
        assert chat_add_link.call_args.args[2] == "合作伙伴"
    result["chat_link_score_keyword"] = True
    result["partner_alias_dedup"] = True
    app.update_idletasks()
    shot.grab(app.winfo_id(), str(OUT / "all_links.png"))
    pill_texts = []
    original_pill = images_util.smooth_pill
    def capture_pill(text, *a, **kw):
        pill_texts.append(text)
        return original_pill(text, *a, **kw)
    try:
        images_util.smooth_pill = capture_pill
        app._draw_links_for(name2id["王教授"])
        assert any(text.startswith("合作伙伴 ") for text in pill_texts), pill_texts
        assert not any(text.startswith("合作 ") for text in pill_texts), pill_texts
        app.update_idletasks()
        shot.grab(app.winfo_id(), str(OUT / "legacy_label.png"))
    finally:
        images_util.smooth_pill = original_pill
        app._clear_links()
    result["legacy_hover_label_normalized"] = True
    app._show_all_links.set(False)
    app._toggle_all_links()
    assert app.node_pos == fine_positions
    member = name2id["蔡经理"]
    entry = next(e for e in app._circle_items if member in e["circle"]["members"])
    before_hull = app.web_canvas.coords(entry["polygon"])
    before_others = {p: q for p, q in app.node_pos.items() if p != member}
    x, y = app.node_pos[member]
    sx, sy = app._l2s(x, y)
    c = app.web_canvas
    ex, ey = sx - c.canvasx(0), sy - c.canvasy(0)
    app._drag = {"pid": member, "moved": False, "node_x0": x, "node_y0": y,
                 "press_x": sx, "press_y": sy}
    app._panning = None
    app.on_drag(SimpleNamespace(x=ex + 110, y=ey + 65))
    assert app.web_canvas.coords(entry["polygon"]) != before_hull
    assert all(app.node_pos[p] == q for p, q in before_others.items())
    # 色块必须在关系线、临时线、卡片下方。
    stack = list(c.find_all())
    assert max(stack.index(i) for i in c.find_withtag("circle_region")) < min(
        stack.index(i) for tag in ("link", "dragtmp") for i in c.find_withtag(tag))
    app.update_idletasks()
    shot.grab(app.winfo_id(), str(OUT / "dragging.png"))
    dragged = dict(app.node_pos)
    app.on_release(SimpleNamespace(x=ex + 110, y=ey + 65))
    assert app.node_pos == dragged
    for _ in range(2):
        app.refresh_network()
        assert app.node_pos == dragged
    app._zoom *= 1.1
    app._draw_web()
    assert app.node_pos == dragged
    saved = db.get_person(member)
    assert saved["pos_x"] is not None and saved["pos_y"] is not None
    assert [dict(e) for e in db.list_links()] == original
    web_items = app._web_items
    app._web_items = lambda: []
    app._draw_web([])
    assert not app._all_links_cache and not app._layout_links and not app._link_routes
    app._show_all_links.set(True)
    app._toggle_all_links()
    assert not app.node_pos and not app._link_routes
    app._show_all_links.set(False)
    app._toggle_all_links()
    app._web_items = web_items
    app.node_pos = dragged
    app._freeze_layout = True
    app._draw_web()
    assert app.node_pos == dragged
    result["empty_network_caches_cleared"] = True
    result.update(circles=[{"label": e["label"], "members": sorted(e["members"])} for e in app._circles],
                  hidden_records=len(original) - len(layout_ids),
                  toggle_ms=round(toggle_ms, 1), toggle_coordinates_stable=True,
                  drag_hull_updated=True, drag_other_positions_stable=True,
                  pinned_saved=True, database_links_unchanged=True)
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False), flush=True)
app.destroy()
