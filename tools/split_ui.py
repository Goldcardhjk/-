# -*- coding: utf-8 -*-
"""一次性重构工具：把 ui.py（6300 行巨石）按新架构切成
ui_base.py（主题/部件/常量） + uipages/ 六个 mixin + 精简壳 ui.py。

切割原则（v0.63 路线图第一阶段）：
- 纯机械搬移：方法体一行不改，靠统一导入头保证名字原样可用；
- ast 精确拿每个方法的行号区间（多行签名/staticmethod 全兼容），
  逐块拷贝原文，无手抄风险；
- 对外契约不动：tools 里 132 处引用（App/NODE_W/ME_LINE_BASE/
  STARTUP_POPUP/_pick_icon/_wrap_by_width/App.CH_R_*…）全部保留在壳。

用法（在项目根运行一次）：
    python tools/split_ui.py --check   # 干跑：只列映射统计
    python tools/split_ui.py           # 实际写文件
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "ui.py")
PAGES = os.path.join(ROOT, "uipages")
os.makedirs(PAGES, exist_ok=True)

# 方法名 → 目标（缺名即报错，防漏网）
TARGETS = {
    # ---- 壳 App（表示层骨架 + 页签/菜单/全局编排） ----
    "shell": [
        "__init__", "_select_tab", "_sync_tabs", "_style", "_entry", "_btn",
        "_btn_face", "_btn_text", "_build_more_menu", "open_more_menu",
        "refresh_combos", "go_gift", "go_greet", "logout", "destroy",
        "_label",
    ],
    # ---- 关系网络页（画布/缩放/布局/交互） ----
    "net": [
        "build_network", "_toggle_panel", "_page_banner", "_ring_radius",
        "fit_view", "_on_wheel", "_do_zoom", "_pan_by", "_grow_world_to",
        "recenter_view", "_view_origin", "_l2s", "_s2l", "_on_middle_press",
        "_on_middle_drag", "_on_middle_release", "_draw_bg",
        "_place_vignette", "toggle_bg", "_web_items", "refresh_network",
        "reset_layout", "_separate_overlaps", "_toggle_all_links",
        "_draw_circle_regions", "_draw_circle_badges",
        "_update_circle_regions", "_draw_web", "_draw_relationship_lines",
        "_draw_relationship_labels", "_sync_me_connections", "_draw_me_node",
        "_start_anim", "_stop_anim", "_anim_step", "toggle_anim",
        "_force_positions", "_radial_positions", "_relationship_label_default",
        "_route_links", "_relationship_label_anchor", "_label_anchor",
        "_draw_legend", "_node_at", "_on_motion", "_render_node",
        "_set_relationship_focus", "_clear_links", "toggle_layout",
        "_draw_links_for", "_hover", "_on_canvas_resize", "on_press",
        "on_drag", "_edge_pan", "_begin_drag_lines", "_update_temp_lines",
        "_end_drag_lines", "on_release", "on_right_click", "change_avatar",
        "reset_node_pos",
    ],
    # ---- 右侧任务栏（概览/提醒/优先级/AI助手卡/快速补关系） ----
    "sidebar": [
        "_refresh_rank", "_row_hover", "_refresh_reminders",
        "_remind_act_greet", "_remind_act_gift", "_due_reminders",
        "popup_due_reminders", "_refresh_overview", "_chat_add",
        "_chat_send", "_chat_worker", "_chat_apply", "quick_link",
    ],
    # ---- 人脉卡片页 ----
    "cards": [
        "build_cards", "_pill", "_pill_update", "refresh_cards",
        "_make_card", "focus_new",
    ],
    # ---- 智能送礼页（含 Vibe Pay 支付流） ----
    "gift": [
        "build_gifting", "on_gift_person", "on_gift_select",
        "_on_pay_channel", "do_pay", "_vibe_pay_flow", "_open_pay_window",
        "refresh_orders",
    ],
    # ---- 祝福语页 ----
    "greet": [
        "build_greetings", "_gr_refresh_mode", "_gr_on_model",
        "_build_mass_panel", "_gr_update_tier_cost", "_gr_on_tier_model",
        "_gr_tier_reset", "_gr_rebuild_chips", "_gr_toggle",
        "_gr_sync_chips", "_gr_sel_all", "_gr_sel_none", "mass_greeting",
        "_gr_worker", "_gr_poll", "_gr_set_state", "_gr_done",
        "_gr_update_progress", "_gr_render_list", "_gr_show",
        "_quick_holiday", "gen_greeting", "copy_greeting",
        "copy_all_greetings",
    ],
    # ---- 绿泡泡连接器页 ----
    "conn": [
        "build_connector", "_conn_first_build", "_nudge_conn_view",
        "_filter_conn", "connect_sandbox", "_render_conn_grid",
        "_conn_card", "import_one", "import_all", "run_llm_judge",
        "_sync_sandbox_links",
    ],
    # ---- 弹窗/配置窗/连线对话框（跨页对话框集中） ----
    "dialogs": [
        "_dialog", "_ask", "_info", "_warn", "_error", "open_llm_config",
        "open_notes_import", "backup_data", "restore_data",
        "open_link_dialog", "toggle_linking", "start_linking_from",
        "cancel_linking", "_linking_click", "_link_at", "_link_menu",
        "confirm_delete_link", "record_contact", "open_profile",
        "confirm_delete", "_conn_after_person_delete", "open_memo",
        "open_slider", "open_pay_config",
    ],
}
# v0.63 场景卡 + 助手方法（外部会话新增，一并归位）
TARGETS["net"] += ["_set_scene", "_sync_scene_btns", "_pil_text",
                   "_make_collapsible"]
TARGETS["cards"] += ["_wheel"]

MIXIN_FILES = {
    "net": ("uipages/network_page.py", "NetPageMixin"),
    "sidebar": ("uipages/sidebar_panel.py", "SidebarMixin"),
    "cards": ("uipages/cards_page.py", "CardsPageMixin"),
    "gift": ("uipages/gift_page.py", "GiftPageMixin"),
    "greet": ("uipages/greet_page.py", "GreetPageMixin"),
    "conn": ("uipages/conn_page.py", "ConnPageMixin"),
    "dialogs": ("uipages/dialogs.py", "DialogsMixin"),
}

# 与 ui_base 契约一致：八个文件用同一套导入头（方法体一行不改的前提）
MIXIN_HEADER = '''# -*- coding: utf-8 -*-
"""{doc}（v0.63 从 ui.py 机械拆出，方法体原样未动）"""
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import json
import math
import os
import random
import queue
import time

import db
import scoring
from scoring import rank_all, compute_score, days_to_next_event
from gifts import recommend, schedule_and_pay
import greetings
from greetings import generate, HOLIDAY_TEMPLATES, craft_one
import wechat_sandbox
import images_util
import llm
import payment
import netlayout
from relationship_view import build_relationship_view

# 统一导入 ui_base 的主题常量与部件（星导含全部非下划线名；
# 下划线工具函数按需显式导入，保证方法体引用原样可用）
from ui_base import *                                   # noqa: F401,F403
from ui_base import (_mix, _rounded, _round_pts, _pick_icon,   # noqa: F401
                     _wrap_by_width, _score_color, clean_note,
                     _f, _split, _parse_imp)


class {cls}:
'''


def build_header(doc, cls):
    return MIXIN_HEADER.format(doc=doc, cls=cls)


def main(check_only=False):
    text = open(SRC, encoding="utf-8-sig").read()
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)

    app_cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "App")
    # App 的 docstring（类体第一句）保留在壳
    class_doc = (ast.get_docstring(app_cls) or "").strip()

    methods = []                       # (name, start_line, end_line) 1-based
    for node in app_cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            methods.append((node.name, start, node.end_lineno))
        else:
            # docstring 之外的类级常量/注释原位保留在壳
            pass
    methods.sort(key=lambda t: t[1])

    name2target = {}
    for tgt, names in TARGETS.items():
        for n in names:
            if n in name2target:
                sys.exit("方法 %s 重复归档" % n)
            name2target[n] = tgt
    missing = [m[0] for m in methods if m[0] not in name2target]
    if missing:
        sys.exit("未映射的方法：%s" % ", ".join(missing))
    unused = [n for n in name2target if n not in {m[0] for m in methods}]
    if unused:
        print("[warn] 映射里有但类里没有的方法：%s" % ", ".join(unused))

    # App 类体起始（docstring 之后）到文件尾如何分块：
    # 每个方法块 = [方法的装饰器首行, 下一个方法装饰器首行)；
    # 块尾夹的注释/空行随前一个方法走。类级常量行独立判给壳。
    class_start = app_cls.body[0].lineno if app_cls.body else app_cls.lineno
    # docstring 占一行；其后若紧跟方法，块从方法起。
    blocks = []
    for idx, (name, start, end) in enumerate(methods):
        blocks.append({"name": name, "start": start, "end": end})

    def seg(a, b):
        return "".join(lines[a - 1:b - 1])

    # ① ui_base.py：模块头到 class App 之前的全部内容（imports/主题/PageStack/部件）
    head = seg(1, app_cls.lineno)
    # ② shell：App 声明 + docstring + __init__ 等壳方法 + 类级常量（原位语义）
    # 类级常量（App.CH_R_LO/ME_R/FREE_* 等工具契约必须留在壳）：
    # 收集行号（含上方紧邻注释），从方法块中剥出来归壳。
    const_lines = set()
    for node in app_cls.body:
        if isinstance(node, ast.Assign):
            a, e = node.lineno, node.end_lineno
            top = a
            while top - 1 >= 1 and lines[top - 2].strip().startswith("#"):
                top -= 1
            const_lines.update(range(top, e + 1))

    def seg(a, b):
        return "".join(lines[a - 1:b - 1])

    def seg_filtered(a, b):
        return "".join(lines[r - 1] for r in range(a, b)
                        if r not in const_lines)

    # 重新组装：壳 = App 类结构（含常量原序）+ 壳方法（按原相对顺序）
    shell_pieces = []
    mixin_pieces = {k: [] for k in MIXIN_FILES}
    taken = set()
    # 方法块及其尾部跟随内容（剔除类级常量行）
    for idx, b in enumerate(blocks):
        nxt = blocks[idx + 1]["start"] if idx + 1 < len(blocks) \
            else len(lines) + 1
        chunk = seg_filtered(b["start"], nxt)
        tgt = name2target[b["name"]]
        if tgt == "shell":
            shell_pieces.append(chunk)
        else:
            mixin_pieces[tgt].append(chunk)
        taken.update(r for r in range(b["start"], nxt)
                     if r not in const_lines)
    # 类级常量行按原序（合并成一段文本写壳）
    const_text = "".join(lines[r - 1] for r in sorted(const_lines))
    # App 声明行 + 缩进基线
    cls_decl = "class App(SidebarMixin, NetPageMixin, CardsPageMixin, " \
               "GiftPageMixin, GreetPageMixin, ConnPageMixin, " \
               "DialogsMixin, tk.Tk):\n"
    shell_pre = seg(1, app_cls.lineno)          # 模块头（搬到 ui_base 的内容）
    shell_head = '''# -*- coding: utf-8 -*-
"""进步壹号 Agent —— 应用壳（v0.63 新结构）

新结构（见 架构设计.md）：
    ui_base.py             主题常量 + 通用部件 + PageStack + 工具函数
    uipages/network_page   关系网络页（画布/布局/缩放/交互）
    uipages/sidebar_panel  右侧任务栏（概览/提醒/优先级/AI 助手/快速补线）
    uipages/cards_page     人脉卡片页
    uipages/gift_page      智能送礼页（含 Vibe Pay 支付流）
    uipages/greet_page     祝福语页
    uipages/conn_page      绿泡泡连接器页
    uipages/dialogs        弹窗/配置窗/连线对话框
    本文件：App 壳 —— 组合页面 mix-in、顶栏/页签、全局菜单与跨页入口。
对外契约不变：App / NODE_W / NODE_H / ME_LINE_BASE / ME_LINE_FOCUS /
STARTUP_POPUP / _pick_icon / _wrap_by_width 等仍可 `from ui import`。
"""

from ui_base import *                                   # noqa: F401,F403
from ui_base import (_mix, _rounded, _round_pts, _pick_icon,   # noqa: F401
                     _wrap_by_width, _score_color, clean_note,
                     _f, _split, _parse_imp)
from uipages.network_page import NetPageMixin
from uipages.sidebar_panel import SidebarMixin
from uipages.cards_page import CardsPageMixin
from uipages.gift_page import GiftPageMixin
from uipages.greet_page import GreetPageMixin
from uipages.conn_page import ConnPageMixin
from uipages.dialogs import DialogsMixin

'''
    shell_class = cls_decl
    if class_doc:
        shell_class += '    """%s"""\n\n' % class_doc
    shell_class += const_text
    shell_class += "\n"
    shell_body = "".join(shell_pieces)
    shell = shell_head + shell_class + shell_body
    if not shell_body.endswith("\n"):
        shell += "\n"

    ui_base = head                                   # 原 imports 里的
    # 壳文件不需要重复 ui_base 的方法导入——head 已含 imports/主题/部件全家

    docs = {
        "net": "关系网络页 mix-in",
        "sidebar": "右侧任务栏 mix-in",
        "cards": "人脉卡片页 mix-in",
        "gift": "智能送礼页 mix-in",
        "greet": "祝福语页 mix-in",
        "conn": "绿泡泡连接器页 mix-in",
        "dialogs": "跨页弹窗 mix-in",
    }
    stats = {k: len(v) for k, v in mixin_pieces.items()}
    stats["shell"] = len(shell_pieces)
    for k, v in stats.items():
        print("%-8s 方法 %d 段" % (k, v))
    print("类级常量行 %d，App 方法总数 %d" % (len(const_lines), len(methods)))
    if check_only:
        print("（--check：未写文件）")
        return

    with open(os.path.join(ROOT, "ui_base.py"), "w", encoding="utf-8") as f:
        f.write(ui_base)
    with open(SRC, "w", encoding="utf-8") as f:
        f.write(shell)
    for key, (path, cls) in MIXIN_FILES.items():
        body = "".join(mixin_pieces[key])
        with open(os.path.join(ROOT, path), "w", encoding="utf-8") as f:
            f.write(build_header(docs[key], cls))
            f.write(body if body.endswith("\n") else body + "\n")
        print("写出", path)
    print("写出 ui_base.py / ui.py")


if __name__ == "__main__":
    main("--check" in sys.argv)
