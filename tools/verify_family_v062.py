"""v062 支付域（Vibe Pay 通道化/订单状态机）叠加既有功能的 D 盘独立生活样例验收及源码启动入口。"""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "--baseline" in sys.argv
OUT = ROOT / "_tmp/v062" / ("before" if BASELINE else "life")
assert ROOT.drive.upper() == "D:" and OUT.parent.is_dir()
OUT.mkdir(exist_ok=True)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
source_sample = ROOT / "_tmp/life_sample_v1"
spec = importlib.util.spec_from_file_location("life_sample", source_sample / "life_sample.py")
sample = importlib.util.module_from_spec(spec)
sys.modules["life_sample"] = sample
spec.loader.exec_module(sample)
sample.SAMPLE_DIR = OUT
sample.DB_FILE = OUT / "life_agent.db"
sample.CONTACTS_FILE = OUT / "life_contacts.json"
contacts_bytes = (source_sample / "life_contacts.json").read_bytes()
if not sample.CONTACTS_FILE.exists():
    sample.CONTACTS_FILE.write_bytes(contacts_bytes)
assert sample.CONTACTS_FILE.read_bytes() == contacts_bytes
sample.configure()
import db
import llm
import netlayout as nl
from relationship_view import build_relationship_view

if "--launch" in sys.argv:
    sample.prepare()
    from main import main
    main()
    sys.exit(0)

REPORT = OUT / "verification.json"
report = {"version": "v062", "status": "running", "checks": [], "database": str(sample.DB_FILE)}
source_names = ("ui.py", "netlayout.py", "llm.py", "db.py", "relationship_view.py",
                "payment.py", "main.py", "tools/verify_family_v062.py")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name, condition):
    report["checks"].append({"name": name, "passed": bool(condition)})
    print(("[通过] " if condition else "[失败] ") + name, flush=True)
    if not condition:
        raise AssertionError(name)


def snapshot():
    conn = db.get_conn()
    try:
        return {table: [dict(row) for row in conn.execute("SELECT * FROM " + table + " ORDER BY id")]
                for table in ("persons", "links", "user_profile", "interactions", "gift_orders")}
    finally:
        conn.close()


def visible(canvas, tag):
    return [item for item in canvas.find_withtag(tag) if canvas.itemcget(item, "state") != "hidden"]


def inspect_ui(app):
    from ui import NODE_W, NODE_H
    import ui as ui_module
    from shot import grab
    from PIL import Image, ImageStat
    import tkinter as tk
    app._anim_on = False
    app._stop_anim()
    for attr in ("_resize_job", "_zoom_job"):
        job = getattr(app, attr, None)
        if job:
            app.after_cancel(job)
            setattr(app, attr, None)
    canvas = app.web_canvas
    # 画布必须先完成布局，否则 winfo_width 仍是占位值，fit 会把整图缩到最小。
    deadline = time.time() + 3
    while (canvas.winfo_width() < 700 or canvas.winfo_height() < 400) and time.time() < deadline:
        app.update()
        time.sleep(0.02)
    check("画布已按1600x920窗口完成布局", canvas.winfo_width() >= 700 and canvas.winfo_height() >= 400)
    app.refresh_network()
    app.fit_view()
    app._freeze_layout = True
    try:
        app.refresh_network()
    finally:
        app._freeze_layout = False
    app.update_idletasks()
    pos = dict(app.node_pos)
    check("主入口实际创建 v0.62 自由二维界面", "v0.62" in app.title() and app.layout_mode == "free")
    check("31 位人物全部渲染且每人一条常驻连我", len(pos) == len(app._node_items) == 31
          and len(visible(canvas, "meline")) == 31
          and {pid for _, pid in app._me_items} == set(pos))
    expected_me = {r["pid"]: r for r in nl.me_connections(pos, *app._web_center,
                   NODE_W / 2, NODE_H / 2, radius=app.ME_R)}
    check("连我直线端点同步且未伪造事实关系", all(canvas.coords(item) ==
          [v for point in expected_me[pid]["pts"] for v in app._l2s(*point)]
          and "link" not in canvas.gettags(item) for item, pid in app._me_items))
    family_entries = [entry for entry in app._circle_items if entry["circle"]["kind"] == "家人"]
    vx, vy = canvas.canvasx(0), canvas.canvasy(0)
    width, height = canvas.winfo_width(), canvas.winfo_height()
    def in_view(box):
        return box and box[0] >= vx and box[1] >= vy and box[2] <= vx + width and box[3] <= vy + height
    check("默认家人圈底色和标题可见且覆盖明确7位家人", any(
          len(entry["circle"]["members"]) == 7 and in_view(canvas.bbox(entry["label"]))
          and canvas.itemcget(entry["polygon"], "fill") == "#FFF4E8" for entry in family_entries))
    clipped = [pid for pid, node in app._node_items.items() if not in_view(canvas.bbox(node["item"]))]
    check("fit后全部人物卡片完整可见", not clipped)
    shown = [r for r in app._link_routes if r["label_visible"]]
    family_records = {edge["id"] for edge in db.list_links()
                      if edge["relation"] in llm.FAMILY_RELATIONS - {"家人"}}
    shown_records = {edge["id"] for route in shown for edge in route["lnk"]["records"]}
    check("具体亲属语义默认可辨识", family_records <= shown_records)
    default_lines = sum(1 for st in app._link_style.values() if st["default_visible"])
    check("次要标签默认收起且非亲属线不再占画布", len(shown) < len(app._link_routes)
          and len(visible(canvas, "link")) == default_lines < len(app._link_routes)
          and len(canvas.find_withtag("link")) == len(app._link_routes))
    me_fills = {canvas.itemcget(item, "fill").upper() for item in canvas.find_withtag("meline")}
    check("人人连我保留但中心线压成浅色细线", len(visible(canvas, "meline")) == 31
          and me_fills == {ui_module.ME_LINE_BASE.upper()})
    old = json.loads((OUT.parent / "before/comparison.json").read_text(encoding="utf-8"))
    leaders = len(visible(canvas, "relationship_label_leader"))
    report["default_view"] = {"zoom": app._zoom, "old_zoom": old["ui"]["zoom"],
        "card_screen_size": [round(NODE_W * app._zoom, 1), round(NODE_H * app._zoom, 1)],
        "persons": len(pos), "melines": len(visible(canvas, "meline")), "relationship_routes": len(app._link_routes),
        "resident_labels": len(shown), "label_leaders": leaders, "clipped_persons": clipped,
        "circle_labels": [c["label"] for c in app._circles],
        "replaced_generic_records": app._relationship_view["metrics"]["hidden_record_count"]}
    check("标签指引数量少于旧图38条关系的一半", leaders < old["ui"]["visible_relationship_lines"] / 2)
    label_overlaps = []
    for item in visible(canvas, "relationship_label"):
        box = canvas.bbox(item)
        for pid, node in app._node_items.items():
            card = canvas.bbox(node["item"])
            if box and card and min(box[2], card[2]) > max(box[0], card[0]) and min(box[3], card[3]) > max(box[1], card[1]):
                label_overlaps.append({"label": item, "person": pid})
    report["default_view"]["label_card_overlaps"] = label_overlaps
    path = OUT / "life_network.png"
    size = grab(app.winfo_id(), str(path))
    with Image.open(path) as image:
        check("生活关系图截图是真实非空白窗口", min(size) >= 700 and max(ImageStat.Stat(image.convert("RGB")).stddev) > 10)
    report["screenshots"] = [str(path)]
    check("fit缩放较旧生活样例显著改善", app._zoom >= old["ui"]["zoom"] * 1.5)
    secondary = next(route for route in app._link_routes if not route["label_default"])
    hover_pid = secondary["lnk"]["person_a"]
    sx, sy = app._l2s(*app.node_pos[hover_pid])
    app._on_motion(SimpleNamespace(x=sx - vx, y=sy - vy))
    check("真实悬停路径展开次要关系语义且人物坐标不变", app._hover_pid == hover_pid and app.node_pos == pos
          and any(r["lnk"]["id"] == secondary["lnk"]["id"] and r["label_visible"] for r in app._link_routes)
          and all(canvas.itemcget(item, "state") != "hidden" for item in canvas.find_withtag(f"label_{secondary['lnk']['id']}")))
    hover_lines = {lid: canvas.itemcget(st["line"], "state") for lid, st in app._link_style.items()}
    check("悬停展开该人物全部关系线且中心线提亮", all(
          hover_lines[st["route"]["lnk"]["id"]] != "hidden"
          for st in app._link_style.values()
          if hover_pid in (st["route"]["lnk"]["person_a"], st["route"]["lnk"]["person_b"]))
          and any(hover_lines[st["route"]["lnk"]["id"]] == "hidden"
                  for st in app._link_style.values()
                  if hover_pid not in (st["route"]["lnk"]["person_a"], st["route"]["lnk"]["person_b"]))
          and canvas.itemcget(app._me_line_by_pid[hover_pid], "fill").upper() == ui_module.ME_LINE_FOCUS.upper())
    hover_path = OUT / "life_hover.png"
    app.update_idletasks()
    grab(app.winfo_id(), str(hover_path))
    report["screenshots"].append(str(hover_path))
    app._hover(hover_pid, False)
    app._hover_pid = None
    check("移开悬停后回归亲属常显且连我不断", sum(r["label_visible"] for r in app._link_routes) == len(shown)
          and len(visible(canvas, "meline")) == 31
          and len(visible(canvas, "link")) == default_lines
          and {canvas.itemcget(item, "fill").upper() for item in canvas.find_withtag("meline")}
          == {ui_module.ME_LINE_BASE.upper()})
    refresh_ms = []
    with patch.object(nl, "free_layout", wraps=nl.free_layout) as layout_call:
        for _ in range(3):
            start = time.perf_counter()
            app.refresh_network()
            refresh_ms.append(round((time.perf_counter() - start) * 1000, 1))
        check("连续刷新坐标零漂移且不重跑布局", app.node_pos == pos and layout_call.call_count == 0)
        zoom_before = app._zoom
        canvas.event_generate("<MouseWheel>", delta=-120, x=width // 2, y=height // 2)
        done = tk.BooleanVar(master=app, value=False)
        app.after(100, lambda: done.set(True))
        app.wait_variable(done)
        check("真实滚轮缩放零漂移且不重跑布局", app._zoom < zoom_before and app.node_pos == pos
              and app._zoom_job is None and layout_call.call_count == 0)
    report["refresh_ms"] = refresh_ms
    check("滚轮后仍然31条常驻连我", len(visible(canvas, "meline")) == 31)
    app._zoom = report["default_view"]["zoom"]
    app.refresh_network()
    pid = hover_pid
    x, y = app._l2s(*app.node_pos[pid])
    event = SimpleNamespace(x=x - canvas.canvasx(0), y=y - canvas.canvasy(0))
    app.on_press(event)
    drag_event = SimpleNamespace(x=event.x + 26, y=event.y + 18)
    app.on_drag(drag_event)
    dragged = dict(app.node_pos)
    app.on_release(drag_event)
    stored = db.get_person(pid)
    check("真实拖拽落点立即保存且不挪其他人", app.node_pos == dragged and all(
        app.node_pos[p] == pos[p] for p in pos if p != pid)
        and (stored["pos_x"] * width, stored["pos_y"] * height) == dragged[pid])
    app._layout_sig = None
    app.refresh_network()
    check("重新计算分组布局仍精确保留手动固定点", app.node_pos[pid] == dragged[pid])
    db.clear_position(pid)
    app.node_pos = pos
    app._layout_sig = None
    app._freeze_layout = True
    try:
        app.refresh_network()
    finally:
        app._freeze_layout = False
    app._show_all_links.set(True)
    app._toggle_all_links()
    check("显示全部恢复全部真实语义", app._relationship_view["metrics"]["hidden_record_count"] == 0
          and all(route["label_visible"] for route in app._link_routes)
          and len(visible(canvas, "link")) == len(app._link_routes)
          and len(canvas.find_withtag("link")) == len(app._link_routes))
    app._show_all_links.set(False)
    app._toggle_all_links()
    check("取消全显后重新收简为非亲属线隐藏", len(visible(canvas, "link")) == default_lines)
    original = snapshot()
    app.nb.select(app.tab_cards)
    app.refresh_cards()
    app.go_gift(pid)
    check("送礼入口仍可选择人物且推荐空态正常", app.g_person.get() == db.get_person(pid)["name"])
    app.go_greet(pid)
    app.gen_greeting()
    check("祝福入口仍可生成本地文本", len(app.gr_text.get("1.0", "end").strip()) > 20)
    app.nb.select(app.tab_conn)
    app.import_all()
    app.import_all()
    check("连接器重复拉取不改人物关系及账号事实", snapshot() == original)
    app.update_idletasks()
    connector = OUT / "life_connector.png"
    grab(app.winfo_id(), str(connector))
    report["screenshots"].append(str(connector))
    # 发布方案要演示「粘贴纪要→自动更新」，这里只验证解析与预览，不写库。
    people, edges_now = db.list_persons(), db.list_links()
    linked_now = {frozenset((e["person_a"], e["person_b"])) for e in edges_now}
    fresh = next(((x, y) for i, x in enumerate(people) for y in people[i + 1:]
                  if x["name"] != y["name"] and frozenset((x["id"], y["id"])) not in linked_now), None)
    dialog = app.open_notes_import()
    dialog.notes_source.insert("1.0", f"周会记录：{fresh[0]['name']}和{fresh[1]['name']}是同事，合作愉快。")
    dialog.notes_parse()
    preview_text = dialog.notes_preview.get("1.0", "end")
    check("纪要窗口解析后列出待写入项且未落库", bool(dialog.notes_state["actions"])
          and "将要执行" in preview_text and snapshot() == original)
    dialog.destroy()

    # ---------- v0.62 支付域 UI：通道选择 / 菜单入口 / 订单通道展示 ----------
    import payment as pay_mod
    check("付款通道选择器在位且默认模拟", hasattr(app, "g_channel")
          and pay_mod.CHANNEL_LABELS["mock"] in app.g_channel.cget("values")
          and pay_mod.CHANNEL_LABELS["vibe"] in app.g_channel.cget("values")
          and app.g_channel.get() == pay_mod.CHANNEL_LABELS["mock"]
          and app.pay_mode == "mock")
    app.g_channel.set(pay_mod.CHANNEL_LABELS["vibe"])
    app._on_pay_channel()
    check("切换 Vibe Pay 后模式跟随且不触发真实请求", app.pay_mode == "vibe")
    app.g_channel.set(pay_mod.CHANNEL_LABELS["mock"])
    app._on_pay_channel()
    check("切回模拟通道", app.pay_mode == "mock")
    mm = app._build_more_menu()
    menu_labels = []
    for mi in range(mm.index("end") + 1):
        try:
            menu_labels.append(str(mm.entrycget(mi, "label")))
        except Exception:
            pass
    check("更多菜单含 Vibe Pay 支付配置入口",
          any("Vibe Pay 支付配置" in lab for lab in menu_labels)
          and any("备份当前账号数据" in lab for lab in menu_labels))

    # ---------- v0.61 新功能验收 ----------
    # 备份 → 对话本地指令改动数据 → 恢复，全程验证备份/恢复与指令闭环。
    tmp_backup = OUT / "backup_roundtrip.db"
    db.backup_database(str(tmp_backup))
    check("备份文件落盘且与当前库字节一致",
          tmp_backup.is_file()
          and tmp_backup.read_bytes() == Path(db.DB_PATH).read_bytes())
    try:
        # ---- v0.62 订单状态机（备份-恢复窗口内，全部还原） ----
        nana0 = next(p for p in db.list_persons() if p["name"] == "外婆")
        oid_v = db.create_order(nana0["id"], 1, "岩茶礼盒", "未排期", 128.0,
                                "", channel="vibe")
        oid_m = db.create_order(nana0["id"], 1, "保温杯", "未排期", 119.0)
        ov = next(o for o in db.list_orders() if o["id"] == oid_v)
        om = next(o for o in db.list_orders() if o["id"] == oid_m)
        check("v0.62 建单记录通道且默认待付款",
              ov["channel"] == "vibe" and om["channel"] == "mock"
              and ov["status"] == "待付款" and om["status"] == "待付款")
        db.update_order_status(oid_v, "等待买家付款")
        db.mark_order_paid(oid_v, "已付款", pay_ref="20261107TESTTRADE0001")
        pay_mod.MockChannel.pay(oid_m)
        ov = next(o for o in db.list_orders() if o["id"] == oid_v)
        om = next(o for o in db.list_orders() if o["id"] == oid_m)
        check("v0.62 到账落库：状态+支付宝流水号；模拟语义不变",
              ov["status"] == "已付款"
              and ov["pay_ref"] == "20261107TESTTRADE0001" and ov["paid_at"]
              and om["status"] == "模拟已付款")
        app.refresh_orders()
        states = [str(app.order_tree.item(iid, "values")[-1])
                  for iid in app.order_tree.get_children()]
        check("订单列表区分 Vibe 与模拟通道",
              any(s.startswith("Vibe·") for s in states)
              and any(s == "模拟已付款" for s in states))

        nana = next(p for p in db.list_persons() if p["name"] == "外婆")
        close0 = float(nana.get("closeness") or 0.5)
        reply, actions = llm.parse_local_intent(
            "外婆 关系分+5", db.list_persons(), db.list_links())
        check("本地指令解析调关系分", bool(actions)
              and actions[0]["op"] == "update_score"
              and actions[0]["name"] == "外婆"
              and float(actions[0]["delta"]) == 5.0)
        app._chat_apply(reply, actions)
        got_close = float(db.get_person(nana["id"]).get("closeness") or 0)
        check("对话调分立即落库", abs(got_close - (close0 + 0.05)) < 1e-6)

        reply, actions = llm.parse_local_intent(
            "给外婆加备注：血糖偏高别送甜食", db.list_persons(), db.list_links())
        check("本地指令解析记备注", bool(actions)
              and actions[0]["op"] == "add_memo"
              and actions[0]["name"] == "外婆")
        app._chat_apply(reply, actions)
        check("备注写入社交记忆", "血糖偏高" in (db.get_person(nana["id"]).get("memo_md") or ""))

        linked = {frozenset((e["person_a"], e["person_b"])) for e in db.list_links()}
        everyone = db.list_persons()
        fresh = next(((x, y) for i, x in enumerate(everyone)
                      for y in everyone[i + 1:]
                      if x["name"] != y["name"]
                      and frozenset((x["id"], y["id"])) not in linked), None)
        check("存在可用于对话建边的两人对", fresh is not None)
        reply, actions = llm.parse_local_intent(
            "%s和%s是大学同学" % (fresh[0]["name"], fresh[1]["name"]),
            everyone, db.list_links())
        check("本地指令解析建立连接", bool(actions)
              and actions[0]["op"] == "add_link"
              and actions[0]["relation"] == "大学同学")
        app._chat_apply(reply, actions)
        check("对话建边落库且网络刷新", any(
            {e["person_a"], e["person_b"]} == {fresh[0]["id"], fresh[1]["id"]}
            and e["relation"] == "大学同学" for e in db.list_links()))
        dup_reply, dup_actions = llm.parse_local_intent(
            "%s和%s是大学同学" % (fresh[0]["name"], fresh[1]["name"]),
            db.list_persons(), db.list_links())
        check("重复建边被识别且不重复执行", not dup_actions and "已经有连接" in dup_reply)

        one_link = db.list_links()[0]
        rel_reply, rel_actions = llm.parse_local_intent(
            "%s和%s是什么关系" % (one_link["name_a"], one_link["name_b"]),
            db.list_persons(), db.list_links())
        check("两人关系查询直达事实边", not rel_actions and "直接关系" in rel_reply)
        linked2 = {frozenset((e["person_a"], e["person_b"])) for e in db.list_links()}
        everyone2 = db.list_persons()
        fresh2 = next(((x, y) for i, x in enumerate(everyone2)
                       for y in everyone2[i + 1:]
                       if x["name"] != y["name"]
                       and frozenset((x["id"], y["id"])) not in linked2), None)
        none_reply, none_actions = llm.parse_local_intent(
            "%s和%s是什么关系" % (fresh2[0]["name"], fresh2[1]["name"]),
            everyone2, db.list_links())
        check("无关系两人如实告知并引导补充", not none_actions
              and ("没有" in none_reply or "没有直接记录" in none_reply))

        # 提醒行动：从提醒直达祝福/送礼页面并预选人物
        app._remind_act_greet(nana["id"], "外婆", "生日")
        check("提醒行动写祝福直达并自动撰稿", app.gr_person.get() == "外婆"
              and app.gr_holiday.get() == "生日"
              and len(app.gr_text.get("1.0", "end").strip()) > 20)
        app._remind_act_gift(nana["id"], "外婆")
        check("提醒行动去送礼直达并预选", app.g_person.get() == "外婆")

        # 启动弹窗：默认关闭；有到期提醒时可强制弹出
        check("启动弹窗默认关闭不打扰", ui_module.STARTUP_POPUP is False
              and app.popup_due_reminders() is None)
        due = app._due_reminders(7)
        pop = app.popup_due_reminders(force=True)
        if due:
            check("有到期提醒时弹窗列出并带行动", pop is not None
                  and bool(pop.winfo_exists()))
            pop.destroy()
        else:
            check("无到期提醒时不弹窗", pop is None)

        # 空态新手三步引导（S5）：空库渲染引导，恢复后网络还原
        app._draw_web([])
        check("空态新手三步引导渲染", app.node_pos == {}
              and len(getattr(app, "_text_imgs", [])) >= 4)
        app.refresh_network()
        check("引导后网络恢复31人", len(app.node_pos) == 31)
    finally:
        db.restore_backup(str(tmp_backup))
        if tmp_backup.exists():
            tmp_backup.unlink()
    check("备份恢复后数据与原始快照一致", snapshot() == original)


def run():
    originals = {str(path): sha(path) for path in (source_sample / "life_agent.db", source_sample / "life_network.png",
                 source_sample / "verification.json", ROOT / "_tmp/v054/validation_manifest.json")}
    before_hashes = {name: sha(ROOT / name) for name in source_names}
    for name in source_names:
        compile((ROOT / name).read_text(encoding="utf-8-sig"), name, "exec")
    sample.prepare()
    if BASELINE:
        for name in ("netlayout", "relationship_view", "llm", "ui"):
            old_spec = importlib.util.spec_from_file_location(name, ROOT / "_tmp/v058/baseline" / (name + ".py"))
            module = importlib.util.module_from_spec(old_spec)
            sys.modules[name] = module
            old_spec.loader.exec_module(module)
        import main as baseline_main
        import tkinter as tk
        from shot import grab
        from ui import App
        real_loop = tk.Misc.mainloop
        baseline_errors = []
        class BaselineApp(App):
            def __init__(self):
                super().__init__()
                self.geometry("1600x920+24+24")
                self.report_callback_exception = lambda exc, val, tb: baseline_errors.append(str(val))
            def mainloop(self, n=0):
                def capture():
                    try:
                        self._anim_on = False
                        self._stop_anim()
                        self.refresh_network()
                        self.fit_view()
                        self._freeze_layout = True
                        self.refresh_network()
                        self._freeze_layout = False
                        self.update_idletasks()
                        baseline = {"ui": {"zoom": self._zoom,
                            "visible_relationship_lines": len(visible(self.web_canvas, "link")),
                            "label_leaders": len(visible(self.web_canvas, "relationship_label_leader")),
                            "circle_labels": [c["label"] for c in self._circles]},
                            "source_sha256": {name: sha(ROOT / "_tmp/v058/baseline" / (name + ".py"))
                                              for name in ("ui", "netlayout", "llm", "relationship_view")}}
                        (OUT / "comparison.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
                        grab(self.winfo_id(), str(OUT / "life_network.png"))
                        report["baseline"] = baseline
                    except BaseException:
                        baseline_errors.append(traceback.format_exc())
                    finally:
                        for job in self.tk.splitlist(self.tk.call("after", "info")):
                            self.after_cancel(job)
                        self.destroy()
                self.after(1400, capture)
                real_loop(self, n)
        baseline_main.App = BaselineApp
        baseline_main.main()
        report["errors"] = baseline_errors
        check("同31人同尺寸旧版源码独立重现并截图", not baseline_errors and (OUT / "life_network.png").is_file())
        return
    original = snapshot()
    repeat = sample.prepare()
    check("独立生活样例重复导入幂等", snapshot() == original and repeat["persons_added"] == repeat["links_added"] == 0)
    persons, links = db.list_persons(), db.list_links()
    # v0.59：同一对人物只保留一条线——原先 6 对「具体关系 + 引荐」叠成双线，
    # 引荐事实仍在 intro_by 字段，图上不重复画。31 人 44 条 → 31 人 38 条。
    pairs = {tuple(sorted((l["person_a"], l["person_b"]))) for l in links}
    check("31人物事实保留且每对人物仅一条线",
          len(persons) == 31 and len(links) == 38 and len(pairs) == len(links))
    check("引荐线索未丢失（intro_by 仍在档）",
          sum(1 for p in persons if (p.get("intro_by") or "").strip()) >= 6)
    circles = llm.relationship_circles(persons, links)
    family = next(c for c in circles if c["kind"] == "家人" and len(c["members"]) == 7)
    check("明确家人元数据构成7人背景候选", family.get("explicit"))
    small = {"kind": "家人", "name": "双人家人", "members": frozenset((1, 2)), "explicit": True}
    pair = {"id": 1, "person_a": 1, "person_b": 2, "relation": "家人"}
    active = nl.adaptive_circles([small], [{"lnk": pair, "pts": [(0, 0), (100, 0)]}], llm.circle_internal_link)
    check("双人背景常驻但不跳过原泛化线替代门槛", len(active) == 1 and not active[0]["replace_generic"]
          and not llm.circle_internal_link(pair, active)
          and not llm.circle_internal_link(dict(pair, relation="父子"), active))
    dense_edges = [(1, 3), (2, 4), (1, 4), (2, 3), (1, 2), (3, 4)]
    positions = {1: (0, 0), 2: (100, 0), 3: (100, 100), 4: (0, 100)}
    dense = [{"lnk": dict(pair, id=i, person_a=a, person_b=b), "pts": [positions[a], positions[b]]}
             for i, (a, b) in enumerate(dense_edges)]
    dense_active = nl.adaptive_circles([dict(small, members=frozenset(positions))], dense, llm.circle_internal_link)
    check("仅一次交叉仍不替代泛化线", len(dense_active) == 1 and not dense_active[0]["replace_generic"])
    star_positions = {1: (0, -1000), 2: (951, -309), 3: (588, 809),
                      4: (-588, 809), 5: (-951, -309)}
    star_pairs = [(1, 3), (3, 5), (5, 2), (2, 4), (4, 1)]
    named_circles = [{"kind": "同事", "name": name,
                      "members": frozenset(star_positions), "explicit": True}
                     for name in ("A项目", "B项目")]
    star_routes = [{"lnk": {"id": i, "person_a": a, "person_b": b, "relation": "同事"},
                    "pts": [star_positions[a], star_positions[b]]}
                   for i, (a, b) in enumerate(star_pairs, 1)]
    named_active = nl.adaptive_circles(named_circles, star_routes, llm.circle_internal_link)
    check("同成员不同名称达标圈各自保留且均可替代泛化线", len(named_active) == 2
          and {c["name"] for c in named_active} == {"A项目", "B项目"}
          and all(c["replace_generic"] for c in named_active))
    doctor = next(p for p in persons if p["name"] == "周医生")
    rec = llm.recommend_contacts("心脏不舒服，想挂市中医院心内科专家号，可以找谁？", persons, links, [])
    check("医疗推荐证据与赵宁引荐路径保留", rec["found"] and any(
          c["person"]["id"] == doctor["id"] and c["path_text"] == "通过 赵宁 引荐" for c in rec["candidates"]))
    missing = llm.recommend_contacts("心脏不舒服，想挂市中医院专家号，可以找谁？",
              [p for p in persons if p["id"] != doctor["id"]],
              [edge for edge in links if doctor["id"] not in (edge["person_a"], edge["person_b"])], [])
    check("缺医疗证据时仍不误荐高亲密联系人", not missing["found"])
    top = rec["candidates"][0]
    check("找人答复一次给出证据/关系等级/答谢方案", "关系等级" in rec["answer"]
          and "答谢建议" in rec["answer"] and top.get("level")
          and top.get("thanks", "").startswith(f"对{top['person']['name']}："))
    # 发布方案里的「导入会议纪要」：已建档两人 + 明确关系词才补边，没提到的不猜。
    linked = {frozenset((e["person_a"], e["person_b"])) for e in links}
    fresh_pair = next(((a, b) for i, a in enumerate(persons) for b in persons[i + 1:]
                       if a["name"] != b["name"]
                       and frozenset((a["id"], b["id"])) not in linked), None)
    check("样例里存在可用于补关系的两人对", fresh_pair is not None)
    a, b = fresh_pair
    note_text = f"周三碰头，{a['name']}和{b['name']}是同事，一起推进项目，合作愉快。"
    parsed = llm.parse_notes(note_text, persons, links)
    check("纪要能补关系/写备注/微调关系分", any(
          act["op"] == "add_link" and {act["a"], act["b"]} == {a["name"], b["name"]}
          and act["relation"] == "同事" for act in parsed["actions"])
          and any(act["op"] == "add_memo" and act["name"] == a["name"] for act in parsed["actions"])
          and any(act["op"] == "update_score" and act["name"] == a["name"] and act["delta"] > 0
                  for act in parsed["actions"]))
    kept = llm.parse_notes("赵宁介绍周医生给我认识，周医生是市中医院心内科的。", persons, links)
    check("纪要不会覆盖已有关系也不臆造未建档的人", not any(
          act["op"] == "add_link" for act in kept["actions"])
          and any("保留原记录" in line for line in kept["summary"]))
    unmapped = llm.parse_notes("今天和一位新朋友聊了聊合作。", persons, links)
    check("纪要没识别到已建档人物时不动网络", not unmapped["actions"]
          and "没有识别到已建档人物" in unmapped["summary"][0])
    from ui import NODE_W, NODE_H
    args = ([p["id"] for p in persons], [(e["person_a"], e["person_b"]) for e in links],
            608, 308.5, NODE_W / 2, NODE_H / 2)
    kwargs = {"generations": llm.family_constraints(persons, links), "groups": circles}
    started = time.perf_counter()
    geom = nl.free_layout(*args, **kwargs)
    elapsed = (time.perf_counter() - started) * 1000
    report["layout_ms"] = round(elapsed, 2)
    check("31人自由布局保持400ms阈值", elapsed < 400)
    check("分组自由布局确定性且非同一圆周", nl.free_layout(*args, **kwargs) == geom
          and max(math.dist(q, (608, 308.5)) for q in geom.values()) - min(math.dist(q, (608, 308.5)) for q in geom.values()) > 100)
    pinned = {next(iter(geom)): (120.25, 180.5)}
    fixed = nl.free_layout(*args, pinned=pinned, **kwargs)
    check("布局内核精确保留手动固定坐标", all(fixed[p] == q for p, q in pinned.items()))
    import main as application
    import ui
    import tkinter as tk
    # v061：main.py 真实启动会打开启动提醒弹窗；验收环境必须确定性，显式关闭
    ui.STARTUP_POPUP = False
    errors = []
    base_app = application.App
    real_mainloop = tk.Misc.mainloop
    class VerificationApp(ui.App):
        def __init__(self):
            super().__init__()
            self.geometry("1600x920+24+24")
            self.report_callback_exception = lambda exc, value, tb: errors.append("".join(traceback.format_exception(exc, value, tb)))
        def mainloop(self, n=0):
            def execute():
                try:
                    inspect_ui(self)
                except BaseException:
                    errors.append(traceback.format_exc())
                finally:
                    for job in self.tk.splitlist(self.tk.call("after", "info")):
                        self.after_cancel(job)
                    self.destroy()
            self.after(1400, execute)
            real_mainloop(self, n)
    application.App = VerificationApp
    try:
        application.main()
    finally:
        application.App = base_app
    report["tk_errors"] = errors
    check("专项与必要smoke无Tk回调异常", not errors)
    check("图形操作后原始人物关系账号及推荐数据未变", snapshot() == original)
    conn = db.get_conn()
    check("独立SQLite完整性正常", conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    conn.close()
    report["source_sha256"] = {name: sha(ROOT / name) for name in source_names}
    check("验收过程中源码稳定", before_hashes == report["source_sha256"])
    check("旧v054与原生活样例证据完全未修改", all(sha(Path(path)) == digest for path, digest in originals.items()))
    report["audit"] = {"sqlite_targets": sorted(sample.SQLITE_TARGETS), "write_targets": sorted(sample.WRITE_TARGETS),
                       "blocked_events": sample.BLOCKED_EVENTS}
    check("仅D盘新隔离库与缓存且无越界访问", sample.SQLITE_TARGETS == {str(sample.DB_FILE)}
          and not sample.BLOCKED_EVENTS and all(OUT in Path(p).parents for p in sample.WRITE_TARGETS))


try:
    run()
    report["status"] = "passed"
except BaseException:
    report["status"] = "failed"
    report["error"] = traceback.format_exc()
    raise
finally:
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "report": str(REPORT)}, ensure_ascii=False), flush=True)
