# -*- coding: utf-8 -*-
"""关系网络页 mix-in（v0.63 从 ui.py 机械拆出，方法体原样未动）"""
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


# ---------------- 拆分遗产修正 ----------------
def _ring_radius_chart(n, W, H):
    """辐射布局的环半径（布局与「自动缩放到看全」共用同一公式）。

    原 ui.py 的 _radial_positions 用类名 `App._ring_radius(...)` 调用本
    公式，为的是 `App._radial_positions(stub,…)` 冷调用
    （tools/check_v027.py）不依赖 self；挪进 mix-in 后类名不可用，公式
    提到模块级——self._ring_radius（静态方法转发）/ 模块直调两条路都通。
    """
    n = max(1, int(n))
    fit_r = max(70.0, min(W, H) / 2 - 70)
    return max(fit_r, n * (NODE_W + 26) / (2 * math.pi))


# v0.63 场景卡图标（ComfyUI 生成；缺失自动退化为纯文字钮）
_SCENE_ICONS = {"全部": "icon_net", "办公": "icon_scene_work",
                "学校": "icon_scene_school", "家庭": "icon_scene_home"}


class NetPageMixin:
    def build_network(self):
        """首页：左侧关系网络画布 + 右侧任务栏（可拖拽调宽、可隐藏）"""
        main = tk.Frame(self.tab_net, bg=BG)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        # PanedWindow：左右两栏，中间可拖拽调宽
        self._paned = ttk.PanedWindow(main, orient="horizontal")
        self._paned.pack(fill="both", expand=True)

        # ---------- 左：网络画布 ----------
        left = tk.Frame(self._paned, bg=BG)
        self._paned.add(left, weight=1)

        # v0.15：玻璃工具条卡（标题+人数居左 / 操作提示居中 / 动作组居右），
        # 替代原先裸 Frame 上文字与按钮混排的两行结构
        tcv, top_bar = rounded_card(left, pad=6, radius=12, parent_bg=BG)
        tcv.pack(fill="x", pady=(0, 8))
        self._label(top_bar, "关系网络", fg=CYAN, font=FONT_LG,
                    icon="icon_net").pack(side="left")
        self.net_count_lbl = self._label(top_bar, "", fg=MUTED, font=FONT_SM)
        self.net_count_lbl.pack(side="left", padx=12)
        hint_mid = tk.Frame(top_bar, bg=SURFACE)
        hint_mid.pack(side="left", expand=True)
        # v0.63：使用场景卡放顶部工具条（一级导航，不占 circle_bar 高度，
        # 画布尺寸与旧版一致，家人圈「标签在默认视口内」的验收不受影响）。
        self._label(hint_mid, "场景", fg=MUTED, font=FONT_SM).pack(side="left", padx=(0, 6))
        # v0.65：场景可自定义，卡片放在独立容器里，增删改后整栏重建即可，
        # 不需要重启程序。数据源是当前账号的 scenes 表。
        self._scene_bar = tk.Frame(hint_mid, bg=SURFACE)
        self._scene_bar.pack(side="left")
        self._scene_hint = self._label(hint_mid, "全部人物", fg=MUTED, font=FONT_XS)
        self._scene_hint.pack(side="left", padx=6)
        self._rebuild_scene_cards(refresh=False)
        self._panel_toggle = self._btn(top_bar, "面板", self._toggle_panel,
                                       icon="icon_panel")
        self._panel_toggle.pack(side="right", padx=4)
        self.more_btn = self._btn(top_bar, "更多", self.open_more_menu,
                                  icon="icon_more")
        self.more_btn.pack(side="right", padx=4)
        self.link_btn = self._btn(top_bar, "连线", self.toggle_linking, "green",
                                  icon="icon_link")
        self.link_btn.pack(side="right", padx=4)
        self._btn(top_bar, "加人", self.focus_new, "primary",
                  icon="icon_add").pack(side="right", padx=4)

        circle_bar = tk.Frame(left, bg=BG)
        circle_bar.pack(fill="x", pady=(0, 6))
        self._show_all_links = tk.BooleanVar(value=False)
        self._all_links_check = tk.Checkbutton(
            circle_bar, text="显示全部连线", variable=self._show_all_links,
            command=self._toggle_all_links, bg=BG, fg=TEXT,
            activebackground=BG, selectcolor=SURFACE, font=FONT_SM)
        self._all_links_check.pack(side="left")
        self._label(circle_bar, "浅线＝和我相连 · 亲属线常显 · 悬停人物展开其他关系",
                    bg=BG, fg=MUTED, font=FONT_XS).pack(side="left", padx=12)

        self.web_canvas = tk.Canvas(left, bg=SURFACE, highlightthickness=1,
                                    highlightbackground=BORDER, bd=0)
        self._legend_frame = tk.Frame(left, bg=BG)
        self._legend_frame.pack(side="bottom", fill="x", pady=(6, 0))
        self._draw_legend(self._legend_frame)
        self.web_canvas.pack(fill="both", expand=True)
        self.web_canvas.bind("<ButtonPress-1>", self.on_press)
        self.web_canvas.bind("<B1-Motion>", self.on_drag)
        self.web_canvas.bind("<ButtonRelease-1>", self.on_release)
        self.web_canvas.bind("<Button-3>", self.on_right_click)
        self.web_canvas.bind("<Motion>", self._on_motion)
        self.web_canvas.bind("<Configure>", self._on_canvas_resize)
        # 缩放 + 平移
        self._zoom = 1.0
        self._panning = None
        self._zoom_job = None          # 滚轮缩放合并（连续滚轮只重画一次，防卡顿）
        self._view_inited = False      # 视口是否已定位（首画/尺寸变化后居中）
        # scrollregion = 内容包围盒 (x0, y0, x1, y1)（内容坐标）。
        # v0.9.2：旧实现固定 ±300 边距，可把整个网络拖出视口；现包围盒即
        # 网络卡片范围，Tk 自动钳制 → 网络永远拖不出去，只有放大后卡片
        # 超出画布时才产生可平移范围（平移只为看全被裁掉的卡片）
        self._sr = (0.0, 0.0, 960.0, 700.0)
        self._bg_on = False            # 背景纹理开关（v0.10 默认关：地图式
        # 画板以内容为主；⚙更多→🎨背景 可开启点阵/柔光/压暗）
        self._bg_tile = None           # 点阵平铺小图（缓存，只生成一次）
        self._bg_vig = None            # 四角压暗小图（缓存）
        self._vig_items = []           # 四角压暗 image items（跟随视口）
        self.web_canvas.bind("<MouseWheel>", self._on_wheel)
        self.web_canvas.bind("<ButtonPress-2>", self._on_middle_press)
        self.web_canvas.bind("<B2-Motion>", self._on_middle_drag)
        self.web_canvas.bind("<ButtonRelease-2>", self._on_middle_release)
        self._web_size = None
        self._resize_job = None

        # ---------- 右：任务栏（可滚动，可隐藏，宽度可拖拽） ----------
        self._right_frame = tk.Frame(self._paned, bg=BG, width=300)
        self._paned.add(self._right_frame, weight=0)
        vsb_r = ttk.Scrollbar(self._right_frame, orient="vertical")
        vsb_r.pack(side="right", fill="y", padx=(12, 0))
        sc_r = tk.Canvas(self._right_frame, bg=BG, highlightthickness=0, bd=0, width=300)
        sc_r.pack(side="right", fill="y", expand=True)
        right = tk.Frame(sc_r, bg=BG, width=300)
        rwin = sc_r.create_window((0, 0), window=right, anchor="nw")
        sc_r.configure(yscrollcommand=vsb_r.set)

        def _rsync(_e=None):
            w = sc_r.winfo_width()
            if w < 8:
                return
            sc_r.itemconfigure(rwin, width=w)
            sc_r.configure(scrollregion=sc_r.bbox("all"))

        right.bind("<Configure>", _rsync)
        sc_r.bind("<Configure>", _rsync)

        def _rwheel_all(e):
            w = e.widget
            while w is not None:
                if w is right:
                    try:
                        sc_r.yview_scroll(int(-e.delta / 120), "units")
                    except Exception:
                        pass
                    return
                w = getattr(w, "master", None)
        self.bind_all("<MouseWheel>", _rwheel_all)

        # 卡片 1：近期提醒
        cv1, card1 = rounded_card(right, pad=10)
        cv1.pack(fill="x", pady=(0, 10))
        self.remind_host = tk.Frame(card1, bg=SURFACE)
        self._make_collapsible(card1, "近期提醒", CYAN, self.remind_host,
                              icon=_pick_icon("icon_remind", "icon_clock"))
        self.remind_host.pack(fill="x")

        # 卡片 2：网络概览
        cv3, card3 = rounded_card(right, pad=10)
        cv3.pack(fill="x", pady=(0, 10))
        self.stat_host = tk.Frame(card3, bg=SURFACE)
        self._make_collapsible(card3, "网络概览", ORANGE, self.stat_host,
                              icon=_pick_icon("icon_overview", "icon_chart"))
        self.stat_host.pack(fill="x")

        # 卡片：快速补关系
        cv4, card4 = rounded_card(right, pad=10)
        cv4.pack(fill="x", pady=(0, 10))
        ql = tk.Frame(card4, bg=SURFACE)
        row1 = tk.Frame(ql, bg=SURFACE); row1.pack(fill="x")
        self.ql_a = ttk.Combobox(row1, width=11, font=FONT_SM, style="Dark.TCombobox")
        self.ql_b = ttk.Combobox(row1, width=11, font=FONT_SM, style="Dark.TCombobox")
        self.ql_a.pack(side="left", padx=(0, 6))
        self.ql_b.pack(side="left")
        row2 = tk.Frame(ql, bg=SURFACE); row2.pack(fill="x", pady=(6, 0))
        self.ql_rel = ttk.Combobox(row2, values=db.RELATION_LINK_TYPES, width=9,
                                   font=FONT_SM, style="Dark.TCombobox")
        self.ql_rel.set("认识")
        self.ql_rel.pack(side="left", padx=(0, 6))
        self.ql_score = ttk.Spinbox(row2, from_=0, to=100, width=5, font=FONT_SM,
                                    style="Dark.TSpinbox")
        self.ql_score.set(50)
        self.ql_score.pack(side="left", padx=(0, 6))
        tk.Label(row2, text="关系分", bg=SURFACE, fg=MUTED,
                 font=FONT_SM).pack(side="left", padx=(0, 6))
        self._btn(row2, "建立", self.quick_link, "primary").pack(side="right")
        self._make_collapsible(card4, "快速补关系", PURPLE, ql, icon="icon_link")
        ql.pack(fill="x", pady=(0, 2))

        # 卡片 3：维护优先级
        cv2, card2 = rounded_card(right, pad=10)
        cv2.pack(fill="x")
        self.rank_host = tk.Frame(card2, bg=SURFACE)
        self._make_collapsible(card2, "维护优先级", GREEN, self.rank_host,
                              icon="icon_rank")
        self.rank_host.pack(fill="x")

        # 卡片 4：🤖 AI 助手（自然语言交互，让大模型帮你操作关系网络）
        cv5, card5 = rounded_card(right, pad=10)
        cv5.pack(fill="x", pady=(10, 0))
        head_row = tk.Frame(card5, bg=SURFACE)
        head_row.pack(fill="x", pady=(2, 8))
        # v0.12：AI 助手透明头像（ComfyUI 生成，18px；缺失回退 🤖 emoji）
        ai_av = images_util.ui_asset("ai_avatar", sub=4)
        if ai_av is not None:
            self._ai_av = ai_av            # 保持引用，防 GC
            tk.Label(head_row, image=ai_av, bg=SURFACE, bd=0).pack(side="left",
                                                                   padx=(0, 5))
            tk.Label(head_row, text="AI 助手", bg=SURFACE, fg=PURPLE,
                     font=FONT_MID, anchor="w").pack(side="left")
        else:
            tk.Label(head_row, text="AI 助手", bg=SURFACE, fg=PURPLE,
                     font=FONT_MID, anchor="w").pack(side="left")
        # v0.11.1：API 配置入口（内置腾讯混元免费预设）
        self._btn(head_row, "API", self.open_llm_config, "ghost",
                  icon="icon_more").pack(side="left", padx=6)
        # 模型选择下拉（按 llm.PRESETS 动态生成）
        self.chat_model = ttk.Combobox(head_row, width=15, font=FONT_XS,
                                       style="Dark.TCombobox", state="readonly",
                                       values=llm.model_choices())
        _cfg = llm.load_config()
        self.chat_model.set(_cfg.get("model") or "hunyuan-lite")
        self.chat_model.pack(side="right")
        # 消息区（可滚动）；bg 用玻璃混合色（与玻璃底纹视觉一致，v0.12）
        chat_wrap = tk.Frame(card5, bg=SURFACE)
        chat_wrap.pack(fill="both", expand=True)
        # v0.59：10 行装不下「找谁/依据/等级/岗位提示/答谢/核实」六段答复，
        # 顶部「优先找：某某」会被自动滚出视野；加高到 14 行一屏读完。
        self.chat_text = tk.Text(chat_wrap, bg=images_util.glass_tint_ui() or SURFACE2,
                                fg=TEXT,
                                font=FONT_SM, wrap="word", bd=0,
                                highlightthickness=0, height=18,
                                state="disabled", padx=8, pady=6)
        chat_vsb = ttk.Scrollbar(chat_wrap, command=self.chat_text.yview)
        self.chat_text.configure(yscrollcommand=chat_vsb.set)
        self.chat_text.pack(side="left", fill="both", expand=True)
        chat_vsb.pack(side="right", fill="y")
        self.chat_text.tag_config("user", foreground=CYAN, spacing1=4, spacing3=2)
        self.chat_text.tag_config("ai", foreground=TEXT, spacing1=4, spacing3=2)
        self.chat_text.tag_config("sys", foreground=MUTED, spacing1=2, spacing3=2)
        # 输入区
        in_row = tk.Frame(card5, bg=SURFACE)
        in_row.pack(fill="x", pady=(6, 0))
        self.chat_entry = tk.Entry(in_row, bg=SURFACE2, fg=TEXT,
                                  font=FONT_SM, bd=0, insertbackground=CYAN)
        self.chat_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        self.chat_entry.bind("<Return>", lambda e: self._chat_send())
        self._btn(in_row, "纪要", self.open_notes_import, "ghost",
                  icon="icon_cards").pack(side="right", padx=(0, 6))
        self._btn(in_row, "发送", self._chat_send, "primary").pack(side="right")
        # v0.59：欢迎语压成一行——两行占掉对话区，长答复就看不到末尾。
        self._chat_add("sys", "我是关系网络助手，直接说人话就行：找谁、补关系、写祝福。")

        self.refresh_network()

    # ---------- 右侧面板显示/隐藏 ----------
    def _toggle_panel(self):
        """显示/隐藏右侧任务栏"""
        try:
            if self._right_frame in self._paned.panes():
                self._paned.forget(self._right_frame)
                self._btn_text(self._panel_toggle, "隐藏")
            else:
                self._paned.add(self._right_frame, weight=0)
                self._btn_text(self._panel_toggle, "面板")
        except Exception:
            pass

    def _page_banner(self, parent, name):
        """功能页右上角装饰横幅（ComfyUI 生成，v0.11）；资产缺失返回 None

        v0.18：删除横幅左缘叠加的发光小图标（用户反馈「多放了一个图标」），
        只保留纯横幅。
        """
        captions = {
            "banner_cards": "人脉档案 / CONTACTS",
            "banner_gift": "心意推荐 / GIFT ASSISTANT",
            "banner_greet": "用心表达 / GREETINGS",
            "banner_conn": "连接与同步 / CONNECTIONS",
        }
        lbl = tk.Label(parent, text=captions.get(name, "工作空间"),
                       bg=BG, fg=MUTED, font=FONT_SM, pady=12)
        lbl.pack(side="right", padx=(12, 4))
        return lbl

    def _make_collapsible(self, parent, title_text, title_fg, content,
                          icon=None):
        """右栏卡片标题栏（v0.21 重做；v0.25 去掉流光侧条）

        用户反馈「这一类的任务栏还是有两个这个丑的重新做」——原设计
        标题栏右侧挂一枚立体 ghost「−」按钮，与图标、标题并排显得杂乱。
        重做后：
        - 折叠交互改为**整行可点**（标题/箭头/空白处 hand2 + Button-1），
          右侧只留一枚精致的雪佛龙箭头（展开 ⌄ / 折叠 ⌃）作状态指示；
        - 标题区底部 1px 淡分隔线，标题区 ↔ 内容区层次分离。
        v0.25（用户：「在卡片上加一个凸出来的方块像素条……这个很不好看」）：
        左缘流光侧条（v0.20）整体移除——那条 5px 竖条在圆角卡上会从
        左上角凸出来，像一个没对齐的滚动条块，纯装饰且违和。
        """
        head = tk.Frame(parent, bg=SURFACE)
        head.pack(side="top", fill="x", pady=(0, 0))
        title_lbl = self._label(head, title_text, fg=title_fg, font=FONT_MID,
                                bg=SURFACE, anchor="w", icon=icon)
        title_lbl.pack(side="left")
        # 折叠状态指示：雪佛龙箭头（展开朝下 ⌄ / 折叠朝上 ⌃）
        arrow = self._label(head, "⌄", fg=MUTED, font=FONT_MID, bg=SURFACE)
        arrow.pack(side="right", padx=(4, 2))
        # 标题区 ↔ 内容区淡分隔线
        tk.Frame(parent, bg=BORDER, height=1).pack(side="top", fill="x")
        state = {"s": "expanded"}

        def _toggle(*_a):
            if state["s"] == "expanded":
                content.pack_forget()
                state["s"] = "collapsed"
                arrow.config(text="⌃", fg=title_fg)
            else:
                content.pack(side="top", fill="x")
                state["s"] = "expanded"
                arrow.config(text="⌄", fg=MUTED)

        # 整行可点 + hand2 光标（标题栏任何一个角落都能折叠）
        for w in (head, title_lbl, arrow):
            w.bind("<Button-1>", _toggle)
            try:
                w.config(cursor="hand2")
            except Exception:
                pass

    # ---------- AI 助手聊天 ----------
    @staticmethod
    def _ring_radius(n, W, H):
        """辐射布局的环半径（布局与「自动缩放到看全」共用同一公式）"""
        return _ring_radius_chart(n, W, H)

    def fit_view(self, n=None):
        """把缩放调到「整个网络刚好落在可视区里」——自动布局后调用。

        按实际 node_pos 的矩形包围盒计算缩放，适用于自由二维及可选布局。
        人物较多时缩小到可视区，放大后仍可平移查看和拖动卡片。
        """
        c = self.web_canvas
        W = c.winfo_width()
        H = c.winfo_height()
        if W < 240 or H < 200:
            # 窗口还没完成布局时 winfo_width 只给 1，直接拿来算会把整张图压到
            # 最小缩放（人物缩成米粒且回不来）。退到窗口尺寸再兜一个常规画布。
            W = max(self.winfo_width(), 900)
            H = max(self.winfo_height(), 600)
        W, H = max(240, W), max(240, H)
        pos = getattr(self, "node_pos", None) or {}
        z = 1.0
        if len(pos) > 1:
            xs = [p[0] for p in pos.values()]
            ys = [p[1] for p in pos.values()]
            cx, cy = getattr(self, "_web_center", (W / 2, H / 2))
            xs.append(cx)
            ys.append(cy)
            for route in getattr(self, "_link_routes", []):
                if route["label_default"]:
                    xs.append(route["label_a"][0])
                    ys.append(route["label_a"][1])
            cw = (max(xs) - min(xs)) + NODE_W + 96
            ch = (max(ys) - min(ys)) + NODE_H + 88
            z = min(1.0, W / cw, H / ch)
            # 缩放后胶囊以屏幕字号重新避卡，需用目标缩放的标签范围复核。
            original_zoom = self._zoom
            try:
                for _ in range(3):
                    self._zoom = max(0.08, z)
                    routes = self._route_links(getattr(self, '_links_cache', []), pos, cx, cy)
                    xs = [p[0] for p in pos.values()] + [cx] + [r['label_a'][0] for r in routes if r['label_default']]
                    ys = [p[1] for p in pos.values()] + [cy] + [r['label_a'][1] for r in routes if r['label_default']]
                    z = min(z, W / (max(xs) - min(xs) + NODE_W + 120),
                            H / (max(ys) - min(ys) + NODE_H + 120))
            finally:
                self._zoom = original_zoom
        else:
            if n is None:
                try:
                    n = len(db.list_persons())
                except Exception:
                    n = 0
            R = self._ring_radius(n, W, H)
            z = min(1.0, (W - 90.0) / (2 * R + NODE_W),
                    (H - 64.0) / (2 * R + NODE_H))
        self._zoom = max(0.08, min(1.0, z))
        self._view_inited = False          # 让 _draw_web 按完整人物包围盒居中

    def _on_wheel(self, e):
        """鼠标滚轮：缩放（v0.10 地图式——整个网络含布局扩散度一起缩，
        像地图/Figma 画板：人多时缩小看全貌，放大看细节，随时可拖动）。
        节点布局坐标不随 zoom 变化（zoom 只在布局→世界映射时统一缩放），
        刷新时保持视口 → 天然以光标为锚。连续滚轮合并为一次重画防卡顿。"""
        factor = 1.12 if e.delta > 0 else 1 / 1.12
        new_zoom = self._zoom * factor
        if new_zoom < 0.08 or new_zoom > 2.5:
            return
        self._zoom = new_zoom
        if self._zoom_job:
            self.after_cancel(self._zoom_job)
        self._zoom_job = self.after(16, self._do_zoom)

    def _do_zoom(self):
        self._zoom_job = None
        self.refresh_network()

    def _pan_by(self, dx, dy):
        """像素级精确平移：1:1 跟随光标。

        v0.9.2 修复（用户反馈「中键拖动太快」）：xview_moveto 的 fraction
        是相对**整个 scrollregion 宽度**的，旧实现除以「可滚动范围」
        （scrollregion 宽 − 视口宽），导致实际位移被放大
        (W+2·pad)/(W+2·pad−vw) ≈ 3 倍——拖 100px 内容跑 300px。

        v0.29（用户反馈「画布为什么有空气墙，应该整个的范围都是可以移动的
        位置才行」）：视口要越过世界边界时，把世界朝那一侧**再长出去**
        （每次 300px 空地），于是画布没有硬边界——搬到哪就长到哪，像
        Figma 的无限画板。旧版 Tk 把视口钳在 scrollregion 内，拖两步就
        撞墙停住，这就是「空气墙」的来源。"""
        c = self.web_canvas
        vw, vh = c.winfo_width(), c.winfo_height()
        vx, vy = self._view_origin()
        tx, ty = vx - dx, vy - dy            # 目标视口左上角（世界系）
        x0, y0, x1, y1 = self._sr
        grew = False
        pad = 300.0
        if tx < x0:                          # 想往左看 → 世界向左长
            x0 = tx - pad
            grew = True
        if ty < y0:
            y0 = ty - pad
            grew = True
        if tx + vw > x1:                     # 想往右看 → 世界向右长
            x1 = tx + vw + pad
            grew = True
        if ty + vh > y1:
            y1 = ty + vh + pad
            grew = True
        if grew:
            self._sr = (x0, y0, x1, y1)
            c.configure(scrollregion=self._sr)
        tw = max(1.0, x1 - x0)               # scrollregion 总宽（内容坐标）
        th = max(1.0, y1 - y0)
        c.xview_moveto(min(1.0, max(0.0, (tx - x0) / tw)))
        c.yview_moveto(min(1.0, max(0.0, (ty - y0) / th)))
        self._place_vignette()

    def _grow_world_to(self, wx, wy, pad=300.0):
        """把世界扩到能装下世界坐标 (wx, wy)，视口保持不动。

        v0.29：卡片拖到哪，世界就长到哪——取代旧的「钳在画布 ±50%」硬墙。
        扩区时先记下视口左上角，重配 scrollregion 后按同一世界坐标复位，
        所以扩容本身不会让画面跳一下。"""
        sr = getattr(self, "_sr", None)
        if not sr:
            return
        x0, y0, x1, y1 = sr
        if x0 + pad <= wx <= x1 - pad and y0 + pad <= wy <= y1 - pad:
            return
        c = self.web_canvas
        vx, vy = self._view_origin()
        nx0, ny0 = min(x0, wx - pad), min(y0, wy - pad)
        nx1, ny1 = max(x1, wx + pad), max(y1, wy + pad)
        self._sr = (nx0, ny0, nx1, ny1)
        c.configure(scrollregion=self._sr)
        tw, th = max(1.0, nx1 - nx0), max(1.0, ny1 - ny0)
        c.xview_moveto(min(1.0, max(0.0, (vx - nx0) / tw)))
        c.yview_moveto(min(1.0, max(0.0, (vy - ny0) / th)))

    def recenter_view(self):
        """视图复位：把视口移回「我」（网络圆心），不动任何卡片位置。

        v0.29：画布不再有空气墙后，理论上能一直拖到无穷远——万一拖迷路
        站在空白处，这里一键回圆心（只改 scrollregion 的滚动偏移）。"""
        c = self.web_canvas
        if not getattr(self, "_web_center", None):
            return
        x0, y0, x1, y1 = self._sr
        mx, my = self._l2s(*self._web_center)
        vw, vh = c.winfo_width(), c.winfo_height()
        tw, th = max(1.0, x1 - x0), max(1.0, y1 - y0)
        c.xview_moveto(min(1.0, max(0.0, (mx - vw / 2 - x0) / tw)))
        c.yview_moveto(min(1.0, max(0.0, (my - vh / 2 - y0) / th)))
        self._place_vignette()
        self.status_var.set("视图已回到中心")

    # ---------- v0.10.1 三层坐标系（修正视口重复扣除） ----------
    # 布局系 L：node_pos/_web_center/力导向/分环都在这一层，画布像素、
    #          与 zoom 无关；人多时布局范围会超出画布（need_r）
    # 世界系 Wld = (L − 中心) × zoom + 中心 —— zoom 全缩（地图/Figma 式），
    #            布局视觉、卡片、连线、标签一起缩放
    # 画布系 S = 世界系 Wld —— canvas item 坐标即世界坐标，视口偏移完全
    #          由 Tk 滚动（scrollregion + xview/yview）承担
    # v0.10 的坑：_l2s 曾多减一次视口原点（item 放在 世界−视口），而 Tk
    # 滚动已含视口偏移 → 平移后一旦重绘，整网跳移「平移量」；_s2l 反向
    # 多加视口原点 → 平移后点击/拖拽全部命中错位。默认状态视口原点≈0
    # 所以当时截图与冒烟都没抓住（用户反馈「拖动之后就成这样了/手感不好」）。
    def _view_origin(self):
        """视口左上角在世界系的位置（= canvas 坐标，画布系即世界系）

        v0.25：改用 canvasx/canvasy 直接取——原实现用 self._sr × xview 分数
        反推，但 scrollregion 刚配置完、Tk 尚未应用新滚动位置时分数是旧的，
        图例会飘到画布中间（小网络下实测）。
        """
        c = self.web_canvas
        try:
            return (c.canvasx(0), c.canvasy(0))
        except Exception:
            return (0.0, 0.0)

    def _l2s(self, lx, ly):
        """布局坐标 → 画布 item 坐标（= 世界坐标，不减视口原点）"""
        cx, cy = self._web_center
        z = getattr(self, "_zoom", 1.0)
        return ((lx - cx) * z + cx, (ly - cy) * z + cy)

    def _s2l(self, sx, sy):
        """画布 item 坐标 → 布局坐标（= 世界坐标，不加视口原点）"""
        cx, cy = self._web_center
        z = max(1e-6, getattr(self, "_zoom", 1.0))
        return ((sx - cx) / z + cx, (sy - cy) / z + cy)

    def _on_middle_press(self, e):
        """中键按下：开始拖动画布"""
        self._panning = {"x": e.x, "y": e.y}
        self.web_canvas.config(cursor="fleur")

    def _on_middle_drag(self, e):
        """中键拖拽：平移画布视口（1:1 跟随）"""
        if not self._panning:
            return
        self._pan_by(e.x - self._panning["x"], e.y - self._panning["y"])
        self._panning["x"] = e.x
        self._panning["y"] = e.y

    def _on_middle_release(self, e):
        self._panning = None
        if not self._drag:
            self.web_canvas.config(cursor="")

    # ---------- 背景纹理层 ----------
    def _draw_bg(self, W, H, cx, cy, region, rmax):
        """背景：中心径向柔光（同心椭圆由外到内叠加，矢量）+ 淡点阵
        （4x 超采样可平铺小图，铺满整个可平移区域=scrollregion 世界系范围）
        + 淡同心环/放射纹 + 四角压暗（跟随视口）。
        v0.10：region 为 scrollregion（世界系）范围；item 坐标 = 世界系 −
        预期视口原点（视口保持时预期原点=旧原点，首画=内容居中），
        因此背景不随 zoom 缩放（网格感像地图）、平移不露底。
        v0.10 起默认关闭（⚙更多→🎨背景 可开）：地图式画板以内容为主。"""
        c = self.web_canvas
        c.delete("bg")
        c.configure(bg="#F8FAFD")
        z = getattr(self, "_zoom", 1.0)
        # 预期视口原点（世界系）：已初始化则沿用旧视口（refresh 保持视口），
        # 首画则内容居中
        if self._view_inited:
            evx, evy = self._view_origin()
        else:
            evx, evy = cx - W / 2, cy - H / 2
        rx0, ry0, rx1, ry1 = region
        # ① 径向柔光：外深内浅（#0D1420 → #162133），56 环颜色步进 <1/255 无接缝
        #    中心 = 布局中心的世界系位置，半径随 zoom（视觉上是同一团光缩放）
        bg0, glow = (13, 20, 32), (22, 33, 51)
        scx, scy = self._l2s(cx, cy)
        R = rmax * 1.35 * z
        N = 56
        for i in range(N, 0, -1):
            f = i / N
            t = (1 - f) ** 1.6
            col = "#%02X%02X%02X" % tuple(
                round(bg0[j] + (glow[j] - bg0[j]) * t) for j in range(3))
            rr = R * f
            c.create_oval(scx - rr, scy - rr, scx + rr, scy + rr,
                          fill=col, outline="", tags=("bg",))
        # ② 平铺纹理：优先 ComfyUI 星图（512x512 无缝，v0.11），
        #    缺失回退矢量点阵；只渲染一次，缓存复用，铺满 region
        if self._bg_tile is None:
            self._bg_tile = (images_util.bg_stars_tile_ui()
                             or images_util.bg_dot_tile())
        tile = self._bg_tile
        if tile is not None:
            ts = max(1, tile.width())
            gx = rx0
            while gx < rx1:
                gy = ry0
                while gy < ry1:
                    c.create_image(gx - evx, gy - evy, image=tile,
                                   anchor="nw", tags=("bg",))
                    gy += ts
                gx += ts
        # ③ 淡同心环 + 放射纹（雷达感，随柔光中心）
        for ri in range(3):
            R2 = rmax * (0.42 + 0.26 * ri) * z
            c.create_oval(scx - R2, scy - R2, scx + R2, scy + R2,
                          outline="#1E2D4A", width=1, tags=("bg",))
        for k in range(12):
            ang = 6.283185307 * k / 12
            c.create_line(scx, scy,
                          scx + rmax * z * math.cos(ang),
                          scy + rmax * z * math.sin(ang),
                          fill="#182742", width=1, tags=("bg",))
        # ④ 四角压暗（图片中心对准视口四角，随视口移动）
        self._vig_items = []
        if self._bg_vig is None:
            self._bg_vig = images_util.bg_corner_vig()
        if self._bg_vig is not None:
            for _ in range(4):
                self._vig_items.append(
                    c.create_image(0, 0, image=self._bg_vig, tags=("bg",)))
        self._place_vignette()

    def _place_vignette(self):
        """把四角压暗图摆到当前视口四角（缩放保持视口 → 天然跟随）"""
        if not getattr(self, "_vig_items", None):
            return
        c = self.web_canvas
        sx0, sy0, sx1, sy1 = self._sr
        tw = max(1.0, sx1 - sx0)
        th = max(1.0, sy1 - sy0)
        vx = c.xview()[0] * tw + sx0
        vy = c.yview()[0] * th + sy0
        vw = c.winfo_width() or 960
        vh = c.winfo_height() or 700
        for it, (px, py) in zip(self._vig_items,
                                ((vx, vy), (vx + vw, vy), (vx, vy + vh),
                                 (vx + vw, vy + vh))):
            c.coords(it, px, py)

    def toggle_bg(self):
        """⚙更多：开/关背景纹理（关掉后纯色底，最干净）"""
        self._bg_on = not self._bg_on
        if self._bg_on:
            self.refresh_network()
            self.status_var.set("背景纹理已开启（点阵 + 中心柔光）")
        else:
            self.web_canvas.delete("bg")
            self._vig_items = []
            self.status_var.set("背景纹理已关闭（纯色底）")

    # ---------- 网络数据 ----------
    def _rebuild_scene_cards(self, refresh=True):
        """按当前账号的 scenes 表重建顶部场景卡。

        v0.65：新增/改名/删除/排序后调用；场景被删导致当前选中失效时
        自动回到「全部」，并同步刷新网络与人脉卡片页的勾选项。
        """
        bar = getattr(self, "_scene_bar", None)
        if bar is None:
            return
        for w in bar.winfo_children():
            w.destroy()
        self._scene_btns = {}
        names = [db.BUILTIN_SCENE] + db.list_scenes()
        for sc in names:
            b = self._btn(bar, sc, lambda s=sc: self._set_scene(s),
                          icon=_SCENE_ICONS.get(sc))
            b.pack(side="left", padx=(0, 4))
            self._scene_btns[sc] = b
        # 管理入口：自定义场景、改删预设都从这里进
        self._btn(bar, "管理", self.open_scene_manager, "ghost",
                  icon="icon_slider").pack(side="left", padx=(2, 4))
        if self._scene not in names:
            self._scene = db.BUILTIN_SCENE
            db.save_scene(self._scene)
            if hasattr(self, "_scene_hint"):
                self._scene_hint.config(text="全部人物")
            self.status_var.set("场景已变更 · 已回到「全部」")
        self._sync_scene_btns()
        if refresh:
            self.refresh_network()
            try:
                self.refresh_cards()
            except Exception:
                pass

    def _set_scene(self, scene):
        """切换使用场景卡：过滤网络/提醒/概览/优先级，不退出登录"""
        if scene == getattr(self, "_scene", "全部"):
            return
        self._scene = scene
        self.selected_pid = None
        db.save_scene(scene)            # v0.63：按账号记住场景
        self._sync_scene_btns()
        if hasattr(self, "_scene_hint"):
            hint = ("全部人物 · 浅线＝和我相连 · 悬停展开其他关系"
                    if scene == "全部" else
                    f"只看「{scene}」的人 · 点「全部」回到整张网")
            self._scene_hint.config(text=hint)
        self.refresh_network()
        n = len(self._web_items())
        self.status_var.set(f"已切到「{scene}」场景 · 共 {n} 人" if scene != "全部"
                            else f"已回到全部 · 共 {n} 人")

    def _sync_scene_btns(self):
        """高亮当前场景卡（选中=primary 面图，未选=normal）"""
        cur = getattr(self, "_scene", "全部")
        for sc, b in getattr(self, "_scene_btns", {}).items():
            try:
                b._kind = "primary" if sc == cur else "normal"
                self._btn_face(b, sc)
            except Exception:
                pass

    def _web_items(self):
        # v0.21：一次 score_context() 批量预取，逐人 compute_score 零查库
        ctx = scoring.score_context()
        scene = getattr(self, "_scene", "全部")
        items = []
        for p in db.list_persons():
            if scene != "全部" and scene not in (p.get("scenes") or []):
                continue
            s, _, t = compute_score(p, ctx)
            items.append({"pid": p["id"], "name": p["name"], "score": s, "tier": t,
                          "avatar": p.get("avatar"), "intro_by": p.get("intro_by"),
                          "pos_x": p.get("pos_x"), "pos_y": p.get("pos_y"),
                          "_p": p})
        items.sort(key=lambda x: x["score"], reverse=True)
        return items

    def refresh_network(self):
        items = self._web_items()
        self._draw_web(items)
        # v0.59：人物集合变化（首次进入 / 拉取 / 增删）时按包围盒自动适配缩放，
        # 保证 16 人演示一进图就能全看到；拖动与手动缩放不会被重置。
        _c = self.web_canvas
        sig = (tuple(sorted(i["pid"] for i in items)),
               _c.winfo_width() // 120, _c.winfo_height() // 100)
        if sig != getattr(self, "_fit_sig", None):
            self._fit_sig = sig
            self.fit_view()
            self._draw_web(items)
        self._refresh_rank(items)
        self._refresh_reminders(items)
        self._refresh_overview(items)
        if hasattr(self, "net_count_lbl"):
            self.net_count_lbl.config(text=f"共 {len(items)} 位人物")
        if self.selected_pid and not any(i["pid"] == self.selected_pid for i in items):
            self.selected_pid = None

    def reset_layout(self):
        """清空所有自定义位置，回到自由二维布局"""
        if not self._ask("自动布局", "将清空所有手动固定位置并重新按人物关系自由排列，继续？"):
            return
        db.clear_all_positions()
        self.layout_mode = "free"
        self._layout_sig = None          # 位置已清空 → 强制重排（否则复用旧布局）
        self.refresh_network()           # 先按当前缩放画一次，拿到真实包围盒
        self.fit_view()                  # v0.30：再按包围盒缩放到能看全
        self.refresh_network()
        self.status_var.set("已重置为自动布局")

    def _separate_overlaps(self, pos, pinned=None, rounds=8):
        """把相互压住的卡片温和推开（就地修改 pos）

        矩形卡片重叠是分轴判定：|dx| < 卡宽 且 |dy| < 卡高 即视觉叠在一起。
        沿「重叠更小的轴」各推一半（位移最小、最不破坏用户手摆的意图），
        多轮迭代收敛。已经分开的卡零位移。

        v0.28：用户手动固定（拖拽过）的卡**永不被推**——用户诉求「每次就
        保存我调整的位置」。旧版把它也一起推，等于手一松位置就自己挪了
        几像素，看着像「又自动布局了」。现在只推非固定的那一侧。
        """
        sep_x = NODE_W + 18.0
        sep_y = NODE_H + 16.0
        fixed = set(pinned or ())
        pids = list(pos)
        for _ in range(rounds):
            dirty = False
            for i, a in enumerate(pids):
                ax, ay = pos[a]
                for b in pids[i + 1:]:
                    bx, by = pos[b]
                    dx, dy = bx - ax, by - ay
                    ox = sep_x - abs(dx)
                    oy = sep_y - abs(dy)
                    if ox <= 0 or oy <= 0:
                        continue
                    fa, fb = a in fixed, b in fixed
                    if fa and fb:       # 两个都是用户手摆的 → 尊重，别动
                        continue
                    dirty = True
                    sx = 1.0 if dx >= 0 else -1.0
                    sy = 1.0 if dy >= 0 else -1.0
                    if ox <= oy:            # 横向重叠更小 → 横向推开
                        push = ox * (0.5 if not (fa or fb) else 1.0)
                        if not fa:
                            ax -= sx * push
                        if not fb:
                            bx += sx * push
                    else:                   # 纵向重叠更小 → 纵向推开
                        push = oy * (0.5 if not (fa or fb) else 1.0)
                        if not fa:
                            ay -= sy * push
                        if not fb:
                            by += sy * push
                    pos[a] = (ax, ay)
                    pos[b] = (bx, by)
            if not dirty:
                break

    def _toggle_all_links(self):
        frozen = getattr(self, "_freeze_layout", False)
        self._freeze_layout = True
        try:
            self._draw_web()
        finally:
            self._freeze_layout = frozen
        hidden = getattr(self, "_relationship_view", {}).get("metrics", {}).get(
            "hidden_record_count", 0)
        self.status_var.set("全部关系线和语义已显示" if self._show_all_links.get()
                            else f"圈子合并 {hidden} 条泛化线；亲属常显，其他标签悬停查看")

    def _draw_circle_regions(self):
        c = self.web_canvas
        self._circle_items = []
        self._circle_badges = {}
        palette = [("#EAF3FD", "#7899C8"), ("#EAF6EF", "#72A58A"),
                   ("#F3EDFB", "#A08AC1"), ("#FFF3E5", "#BA986C"),
                   ("#FBEFF2", "#BC8D9A"), ("#EAF6F7", "#79A6AD")]
        for i, circle in enumerate(self._circles):
            fill, border = ("#FFF4E8", "#C49974") if circle["kind"] == "家人" else palette[i % len(palette)]
            polygon = c.create_polygon(0, 0, 1, 0, 1, 1, fill=fill,
                                       outline=border, width=1.2,
                                       tags=("circle_region",))
            title = circle["kind"] if circle["kind"] == "家人" else circle["name"]
            if title != circle["kind"] and circle["kind"] not in title:
                title = circle["kind"] + " · " + title
            text = title + f" · {len(circle['members'])}人"
            image = images_util.smooth_text(text, 9, TEXT, True)
            background = c.create_rectangle(0, 0, 1, 1, fill=fill, outline=border,
                                             tags=("circle_region",))
            if image is not None:
                self._text_imgs.append(image)
                label = c.create_image(0, 0, image=image, anchor="nw",
                                       tags=("circle_region",))
                width, height = image.width(), image.height()
            else:
                label = c.create_text(0, 0, text=text, anchor="nw", fill=TEXT,
                                      font=FONT_SM, tags=("circle_region",))
                box = c.bbox(label)
                width, height = box[2] - box[0], box[3] - box[1]
            self._circle_items.append({"circle": circle, "polygon": polygon,
                                       "background": background, "label": label,
                                       "size": (width, height)})
        self._update_circle_regions()

    def _draw_circle_badges(self):
        for pid in self.node_pos:
            memberships = [circle for circle in self._circles if pid in circle["members"]]
            if len(memberships) < 2:
                continue
            image = images_util.smooth_pill(" / ".join(dict.fromkeys(
                circle["kind"] for circle in memberships)), CYAN, size=8)
            if image is None:
                continue
            self._pill_imgs.append(image)
            item = self.web_canvas.create_image(
                0, 0, image=image, anchor="se", tags=("circle_badge", f"node_{pid}"))
            self._circle_badges[pid] = item
        self._update_circle_regions()

    def _update_circle_regions(self, pid=None):
        c = self.web_canvas
        boxes = []
        for p, (nx, ny) in self.node_pos.items():
            sx, sy = self._l2s(nx, ny)
            hw, hh = NODE_W * self._zoom / 2, NODE_H * self._zoom / 2
            boxes.append((sx - hw, sy - hh - 22, sx + hw, sy + hh))
        for entry in getattr(self, "_circle_items", []):
            circle = entry["circle"]
            # 凸包只取实际成员；视觉覆盖范围不参与成员识别。
            if pid is None or pid in circle["members"] or "hull" not in entry:
                # v0.60：凸包会跟着成员位置歪成斜四边形，几个圈叠在一起像斜条
                # 穿过画面。改成轴对齐包围框——同样是「这些人是一组」，
                # 但边框横平竖直，演示时一眼能读，不抢人物卡片的注意力。
                _pts = [self.node_pos[p] for p in circle["members"] if p in self.node_pos]
                if _pts:
                    _xs = [q[0] for q in _pts]
                    _ys = [q[1] for q in _pts]
                    _hx, _hy = NODE_W / 2 + 18, NODE_H / 2 + 20
                    _x0, _x1 = min(_xs), max(_xs)
                    _y0, _y1 = min(_ys), max(_ys)
                    entry["hull"] = [(_x0 - _hx, _y0 - _hy),
                                     (_x1 + _hx, _y0 - _hy),
                                     (_x1 + _hx, _y1 + _hy),
                                     (_x0 - _hx, _y1 + _hy)]
                else:
                    entry["hull"] = []
                c.coords(entry["polygon"], *[v for p in entry["hull"] for v in self._l2s(*p)])
            hull = entry["hull"]
            if not hull:
                continue
            # 可见区域的标签留在视口内；局部避让只移动标签，不移动人物。
            screen_hull = [self._l2s(*point) for point in hull]
            origin_x, origin_y = min(screen_hull, key=lambda p: (p[1], p[0]))
            w, h = entry["size"]
            origin_y -= h + 28
            vx, vy = c.canvasx(0), c.canvasy(0)
            vw, vh = c.winfo_width(), c.winfo_height()
            visible = (min(p[0] for p in screen_hull) < vx + vw
                       and max(p[0] for p in screen_hull) > vx
                       and min(p[1] for p in screen_hull) < vy + vh
                       and max(p[1] for p in screen_hull) > vy)
            candidates = set()
            for dx in range(-4, 5):
                for dy in range(-8, 9):
                    x, y = origin_x + dx * (w + 20), origin_y + dy * (h + 16)
                    if visible:
                        x = max(vx + 4, min(x, vx + vw - w - 16))
                        y = max(vy + 4, min(y, vy + vh - h - 12))
                    candidates.add((x, y))
            best_overlap = float("inf")
            for x, y in sorted(candidates, key=lambda p: (p[0] - origin_x) ** 2
                              + (p[1] - origin_y) ** 2):
                overlap = sum(max(0, min(x + w + 12, b[2] + 6) - max(x, b[0] - 6))
                              * max(0, min(y + h + 8, b[3] + 6) - max(y, b[1] - 6))
                              for b in boxes)
                if overlap < best_overlap:
                    best_overlap, chosen = overlap, (x, y)
                if not overlap:
                    break
            x, y = chosen
            boxes.append((x, y, x + w + 12, y + h + 8))
            c.coords(entry["background"], x, y, x + w + 12, y + h + 8)
            c.coords(entry["label"], x + 6, y + 4)
        # 所有标签在全部色块之上，仍低于关系线和卡片。
        for entry in getattr(self, "_circle_items", []):
            c.tag_raise(entry["background"], self._circle_items[-1]["polygon"])
            c.tag_raise(entry["label"], entry["background"])
        for p, item in getattr(self, "_circle_badges", {}).items():
            if pid is None or p == pid:
                x, y = self.node_pos[p]
                c.coords(item, *self._l2s(x + NODE_W / 2, y - NODE_H / 2 + 3))

    def _draw_web(self, items=None):
        c = self.web_canvas
        # 只删内容层、保留背景纹理层（bg）：缩放/刷新时背景零重绘，
        # 彻底消除每次滚轮都整幅重画 PIL 背景造成的卡顿
        for it in c.find_all():
            if "bg" not in c.gettags(it):
                c.delete(it)
        self.node_imgs = {}
        self._circle_items = []
        self._circle_badges = {}
        self._circles = []
        self._all_links_cache = []
        self._layout_links = []
        self._links_cache = []
        self._link_routes = []
        self._link_style = {}
        self._me_items = []
        self._me_routes = []
        self._relationship_view = {}
        self._card_imgs = {}      # pid -> 节点整卡 PhotoImage（保持引用）
        self._pill_imgs = []      # 连线胶囊 PhotoImage（保持引用）
        self._legend_imgs = []    # 图例圆点 PhotoImage（保持引用）
        self._text_imgs = []      # PIL 文字图片（保持引用）
        self.edges = {}
        self._node_colors = {}
        self._hover_pid = None
        if items is None:
            items = self._web_items()
        W = c.winfo_width() or 960
        H = c.winfo_height() or 700
        cx, cy = W / 2, H / 2
        z = self._zoom

        # 空状态引导
        if not items:
            c.delete("bg")
            empty_img = images_util.ui_asset("empty_net", sub=2)
            if empty_img is not None:
                self._empty_img = empty_img  # 保持引用，防 GC
                # 插画居中偏上，文字说明在插画下方
                c.create_image(cx, cy - 105, image=empty_img, anchor="c")
                ty = cy + 130
            else:
                c.create_text(cx, cy - 18, text="", fill=BORDER,
                              font=(UI_FONT, 46))
                ty = cy + 28
            self._pil_text(c, cx, ty, "关系网络还是空的", 12, MUTED, True)
            # v0.61（S5）：新手三步引导——空库时把「接下来做什么」讲清楚
            self._pil_text(c, cx, ty + 26,
                           "第一步：到「绿泡泡连接器」一键拉取好友（或点右上角「加人」手动录入）",
                           9, MUTED)
            self._pil_text(c, cx, ty + 46,
                           "第二步：用「连线」补上有人际依据的关系，或在 AI 助手里直接告诉我",
                           9, MUTED)
            self._pil_text(c, cx, ty + 66,
                           "第三步：回到这里看全景——悬停任意人物，展开他的全部关系",
                           9, MUTED)
            self.node_pos = {}
            self._stop_anim()
            # 空状态：内容即整个画布，不产生可平移区域
            c.configure(scrollregion=(0, 0, W, H))
            self._sr = (0.0, 0.0, float(W), float(H))
            self._place_vignette()
            return

        persons = [it.get("_p") or {"id": it["pid"]} for it in items]
        all_view = build_relationship_view(
            persons, db.list_links(), show_all=True,
            normalize_relation=db.normalize_link_relation)
        links = all_view["visible_links"]
        generations = llm.family_constraints(persons, links)
        circle_candidates = llm.relationship_circles(persons, links)
        self._all_links_cache = links
        # 明确圈子提供初始分组；全部实际边仍保留，固定点优先。
        # 节点位置：手动固定坐标优先；默认按实际人物关系自由二维排布。
        # 分数分环只作为用户手动选择的旧布局模式保留。
        pinned = {}
        for it in items:
            px, py = it.get("pos_x"), it.get("pos_y")
            if px is not None and py is not None:
                # 旧版像素坐标兼容（v0.10：布局可超画布，比例允许 >1，
                # 只有明显是像素值的才按旧版转换）。v0.29：阈值从 3 抬到
                # 20——现在卡片能拖到画布外很远，比例值 3.5 也可能是真的
                # （旧阈值会把它当像素值除一遍，位置直接飞到对面）
                if abs(px) > 20.0 or abs(py) > 20.0:
                    px, py = px / W, py / H
                # 坏位置自愈——只挡真正的脏数据（非数字 / 离谱到 12 屏外）。
                # v0.29：用户可以把卡拖到画布外的任意位置，那都是他要的位置，
                # 不能当脏数据清掉（旧版 -0.05~1.05、-0.62~1.62 都清过，
                # 表现就是「我调好的位置没保存」）
                try:
                    ok_num = (math.isfinite(float(px))
                              and math.isfinite(float(py)))
                except (TypeError, ValueError):
                    ok_num = False
                if not ok_num or not (-12.0 <= px <= 12.0 and -12.0 <= py <= 12.0):
                    try:
                        db.clear_position(it["pid"])
                    except Exception:
                        pass
                    continue
                # 比例坐标映射布局系（v0.10：不随 zoom——zoom 只在布局→
                # 世界映射时统一缩放，布局本身与 zoom 无关）
                pinned[it["pid"]] = (px * W, py * H)
        # v0.10.1：布局复用——图结构（人物/分数/连线）/画布尺寸/布局模式
        # 都没变时直接沿用现有布局：缩放、平移、刷新零抖动。旧实现每次
        # 刷新都重跑力导向（低温续跑也会让网络缓慢爬行几十 px，平移后
        # 重绘尤其明显——「拖动之后就成这样了」的体感来源之一）。
        # 结构签名变化（加人/删人/连线/改分/切布局/改窗口尺寸）才重排。
        # 尺寸量化到 16px 桶：状态栏文字长度等引起的画布瞬时微缩（~10px）
        # 不触发重排，避免钉子位置比例映射抖动
        cur_pids = sorted(it["pid"] for it in items)
        # v0.10：布局扩散度按人数增长——人少时适配画布（全貌），
        # 人多时半径超出画布（外环卡片不重叠），于是 zoom=1 就有内容
        # 溢出视口 → 随时可拖动；配合滚轮缩小即得「人多也方便观察」
        # （Rmax 同时供 _draw_bg 柔光半径用，复用/重排两条路径都要有）
        fit_r = max(70.0, min(W, H) / 2 - 70)
        need_r = len(items) * (NODE_W + 24) / 6.283185307 * 1.05
        Rmax = max(fit_r, need_r)
        sig = (self.layout_mode, W // 16, H // 16, tuple(cur_pids),
               tuple(sorted(generations)),
               tuple((g["kind"], g["name"], tuple(sorted(g["members"])))
                     for g in circle_candidates),
               (() if self.layout_mode == "free" else
                tuple(sorted((it["pid"], it["score"]) for it in items))),
               tuple(sorted((l["person_a"], l["person_b"], l.get("relation") or "")
                            for l in links)))
        pos = None
        # v0.28：拖拽保存后的这一次刷新「冻结布局」——位置以 node_pos 为唯一
        # 真源，不做任何推挤/避让/自愈。用户诉求（2026-10-28）：「为什么我
        # 现在调整位置他会自动重新布局，不要在我调整位置的时候自动重新布局」
        freeze = bool(getattr(self, "_freeze_layout", False))
        reuse = (sig == getattr(self, "_layout_sig", None)
                 and getattr(self, "node_pos", None)
                 and all(p in self.node_pos for p in cur_pids))
        if freeze and getattr(self, "node_pos", None) \
                and all(p in self.node_pos for p in cur_pids):
            reuse = True
        if reuse:
            pos = {p: self.node_pos[p] for p in cur_pids}
            # 炸开/叠合自愈（复用路径）：范围远超人数所需、或任意两卡重叠
            # → 放弃复用走重排。
            # v0.28：只统计「自动摆的」卡——用户手摆的卡靠近别人、或拖到画布
            # 外面，都是他的意图，拿它当「炸开」证据就会把他的布局整图推翻。
            if not freeze:
                loose = [pos[p] for p in pos if p not in pinned]
                # v0.29：全员都是用户亲手摆的（全 pinned）→ 没有「自动摆的
                # 证据」，不做自愈（旧版回退成全体判定，把他摆好的图判成
                # 炸开再重排，等于白摆）
                if loose and len(loose) > 1:
                    xs = [p[0] for p in loose]
                    ys = [p[1] for p in loose]
                    need_r0 = len(items) * (NODE_W + 24) / 6.283185307 * 1.05
                    blown = max(max(xs) - min(xs), max(ys) - min(ys)) > \
                        2.0 * (2 * (need_r0 + NODE_W / 2 + 24)) + 240
                    if not blown:
                        # v0.42：判「两张卡叠在一起」必须按矩形算。原来用
                        # 圆心距 < NODE_W*0.9 = 138.6px，可卡片是 154×136：
                        # 竖直方向隔 138.6px 根本不叠（136 才挨上），却被判
                        # 成叠 → 放弃复用 → 重跑布局。30 人时内圈挤，几乎每
                        # 次刷新都命中，于是「缩放/刷新要等 10~20 秒」。
                        for i in range(len(loose)):
                            xi, yi = loose[i]
                            for j in range(i + 1, len(loose)):
                                if (abs(xi - loose[j][0]) < NODE_W - 2.0
                                        and abs(yi - loose[j][1]) < NODE_H - 2.0):
                                    blown = True
                                    break
                            if blown:
                                break
                    if blown:
                        # v0.42：重排是确定性的——同一张图重算一遍还是同一批
                        # 重叠位置，纯粹白花时间。30 人 + 关系密时布局确实排
                        # 不开（半径压崩、卡片互相压），于是每次缩放/平移/刷新
                        # 都判「要重排」，每次 8~13 秒，界面等于卡死。改成：
                        # 同一张图只重排一次，之后接受现状（用户仍可手动拖）。
                        if getattr(self, "_rebuilt_sig", None) == sig:
                            pos = {p: self.node_pos[p] for p in cur_pids}
                        else:
                            pos = None
        rebuilt = pos is None
        if rebuilt:
            auto_items = [it for it in items if it["pid"] not in pinned]
            auto_items.sort(key=lambda x: x["score"], reverse=True)
            bands = {0: [], 1: [], 2: [], 3: []}
            for it in auto_items:
                s = it["score"]
                bands[0 if s >= 80 else 1 if s >= 65 else 2 if s >= 45 else 3].append(it)
            ring_pos = {}
            for ri in range(4):
                grp = bands[ri]
                if not grp:
                    continue
                radius = Rmax * (0.36 + 0.21 * ri)
                n = len(grp)
                for k, it in enumerate(grp):
                    ang = ri * 0.4 + 2 * math.pi * k / n
                    ring_pos[it["pid"]] = (cx + radius * math.cos(ang),
                                           cy + radius * math.sin(ang))
            if self.layout_mode == "free":
                pos = netlayout.free_layout(
                    cur_pids,
                    [(l["person_a"], l["person_b"]) for l in links],
                    cx, cy, NODE_W / 2.0, NODE_H / 2.0,
                    pinned=pinned, generations=generations, groups=circle_candidates)
            elif self.layout_mode == "smart":
                pos = self._force_positions(items, links, pinned, ring_pos,
                                            W, H, cx, cy)
            elif self.layout_mode == "radial":
                pos = self._radial_positions(items, pinned, W, H, cx, cy,
                                             links)
            else:
                pos = dict(pinned)
                pos.update(ring_pos)
            self._layout_sig = sig
            self._rebuilt_sig = sig
        # v0.28：下面三步都是「替用户挪卡」的自动调整，冻结（刚拖完保存）时
        # 一律跳过——否则手一松、卡片就被推开几像素，看着像又自动布局了
        if rebuilt and not freeze and self.layout_mode != "free":
            # v0.25：卡片去重叠——只推自动摆的卡；用户手摆的卡不动（v0.28）
            self._separate_overlaps(pos, pinned)
            # v0.31：直线避让分两套——
            # · 辐射布局（v0.33）：单卡沿射线一维坐标下降（relax_clear）——
            #   卡片不再被钉死在同一个半径上，可 20% 内收 / 35% 外凸，给线
            #   让出真实可穿行的通道。为什么换掉 relax_ring：
            #   ① relax_ring 是力式累积（多边投票后一次位移），净空目标一提高
            #      就冲过头再被推回来，来回拉锯；relax_clear 逐卡全量评估候选
            #      点、只接受代价下降，单调收敛。
            #   ② relax_ring 只看人物关系线；实际还画着「我→每个人」的辐射线，
            #      卡往内挪会挡住别人的辐射线。relax_clear 把辐射线也当障碍。
            #   ③ 加了卡间间隙项，不会为了躲线把两张卡挤成一张。
            #   实测 18 人：净空 12.8 → 28，交叉 0，半径 602~689（+14%）。
            # · 其它布局（力导向/分层）：沿用 avoid 的通用避让。
            # v0.34：擦卡权重从 120 降到 30（netlayout._GRAZE 同步改）。v0.33
            # 为了「跨距 2 一条都不留」，环序宁可把线拉到跨 8/9——正圆是一张
            # 卡没挪，可线全从圆心横穿，用户看到的正是「线乱飞」。现在让环序
            # 保留跨 2/3（划算才换），由这里的径向起伏（±14%）把卡片推开几十
            # 像素，线就从缝里穿过去了。实测真实 18 人：跨距 {1:15, 2:2, 4:2,
            # 5:1}、零交叉、净空 25.8、半径 561~609、邻位突跳 45px。
            if rebuilt and links and not generations:
                # v0.39：内圈张数只在「两层构造」成功时才有意义。以前失败
                # 分支不清空，会留着上一次（人数少时）的张数，调试看图时
                # 误以为这次也进了内圈。
                self._net_inner = None
                try:
                    # v0.42：同一对人的关系可能在库里有多条（大模型补关系时会
                    # 与已有那条不同名，add_link 按「关系名」去重，于是同一对
                    # 留下两条：比如 王教授—赵总 既有「合作伙伴」又有「引荐」）。
                    # 画的时候早就去重了（只画一条），但布局输入以前把 51 条
                    # 全塞进去，导致环序跨度代价按 51 条算（实测 408 vs 去重
                    # 后 95），白跑几轮还把半径压崩。这里按「无向对」去重。
                    e2, _seen_e = [], set()
                    for _l in links:
                        _a, _b = _l["person_a"], _l["person_b"]
                        _k = (_a, _b) if _a <= _b else (_b, _a)
                        if _k in _seen_e:
                            continue
                        _seen_e.add(_k)
                        e2.append((_a, _b))
                    if self.layout_mode == "radial":
                        segs = e2 + [(None, p) for p in pos]
                        hw = NODE_W / 2.0
                        hh = NODE_H / 2.0
                        # v0.36：两层构造（外圈 + 内圈）。用户 2026-10-28 的
                        # 判据是「长线不能横穿辐射线」，并认可「几张卡缩进
                        # 内圈」——他手调那张图正是这么做的。纯二维 gradient
                        # 下降做不到（要两端同时外推 200px），所以改成离散
                        # 选层 + 按弧长分角度，见 netlayout.layer_refine。
                        # v0.37：两层之外再加「逐卡半径上限」和「重排环序」，
                        # 四道逐级放宽，统一由 netlayout.auto_layers 调度；
                        # 全部失败才算放不下（人数 + 关系密度超出卡片尺寸）。
                        info = None
                        if not pinned:            # 有手摆坐标时不动用户的位置
                            _obs = []
                            # v0.40：auto_layers 对**初始环序**极其敏感——它内部
                            # 的「重排环序」是局部搜索，起点不同就落在不同盆地。
                            # 同一个 30 人图，从环序位置出发建不起来（退回二维
                            # 自由布局，32 处穿线 / 净空 9.3），从「按序号均分
                            # 圆」出发却零穿线（0 处 / 26.2px）。所以两个起点都
                            # 试，按「穿线少 → 净空大」取优；第一个就干净的直接
                            # 停（18 人只有一次调用，和以前一样快）。
                            _n = max(1, len(pos))
                            _R = max(hw * 2.0, _n * (2.0 * hw + 30.0)
                                     / (2.0 * math.pi))
                            _flat = {}
                            for _k, _p in enumerate(sorted(pos)):
                                _a = (-math.pi / 2 + 2.0 * math.pi * _k / _n)
                                _flat[_p] = (cx + _R * math.cos(_a),
                                             cy + _R * math.sin(_a))
                            # v0.42：人多时只试均分圆这一个起点。实测 30 人 / 37
                            # 条边两个起点都失败，第二个（当前环序）白烧 5.3 秒
                            # ——第一个起点 0.1 秒就判「结构性放不下」，那是图
                            # 结构决定的，换起点也一样。而人多时能成的场景（27
                            # 条边）恰恰都是均分圆这个起点成的，所以先试它就够，
                            # 不必再补第二次。
                            _starts = ([(_flat, True)]
                                       if _n >= 24
                                       else [(pos, False), (_flat, True)])
                            _bid = None
                            _tQ = time.time()
                            for _st, _use_flat in _starts:
                                _cand = dict(_st)
                                _inf = netlayout.auto_layers(
                                    _cand, e2, cx, cy, hw, hh, gap=30.0,
                                    clear=self.CHANNEL,
                                    floor=self.CHANNEL - 4.0, obs=_obs)
                                if _inf is None:
                                    continue
                                _hs = netlayout.spoke_hits(
                                    _cand, e2, cx, cy, hh, 12.0)[0]
                                _gs = netlayout.min_gap(_cand, e2, list(_cand),
                                                        hw, hh)
                                _key = (-_hs, _gs)
                                if _bid is None or _key > _bid[0]:
                                    _bid = (_key, _cand, _inf)
                                if _hs == 0 and _gs >= 20.0:
                                    break
                            if __import__("os").environ.get("NL_DEBUG"):
                                print("[dbg]   auto_layers %d 起点 %.2fs"
                                      % (len(_starts), time.time() - _tQ),
                                      flush=True)
                            if _bid is not None:
                                info = _bid[2]
                                pos.clear()
                                pos.update(_bid[1])
                        if info is None:
                            # 有固定卡（或人数太少）：沿用 v0.35 的二维自由布局
                            #
                            # v0.38：clear 从 46 一档改成**阶梯放宽**。人多时
                            # 46px 是做不到的——26 人实测它最后交出来的净空是
                            # −41（线直接压进卡里，比穿辐射线难看得多）。这里
                            # 依次试 46→30→20→14，取第一个「没有任何线压进卡片」
                            # 的结果，宁可通道窄一点，也不让线穿进卡里。
                            _p0 = dict(pos)
                            # v0.42：回退起点先按「关系跨度」重排环序。原来的
                            # 环序是按分数分四环排的（ring_pos），跟谁认识谁没
                            # 关系——30 人 / 37 条边时跨度代价 282，几乎每条边
                            # 都是跨圈的，怎么避让都消不掉 38 处穿线。重排后
                            # 实测降到 58~65，同一条边只跨 1~2 格，从卡缝里就
                            # 能过去。重排只改顺序、不改人数，失败也无副作用。
                            try:
                                _ang0 = {p: math.atan2(_p0[p][1] - cy,
                                                       _p0[p][0] - cx)
                                         for p in _p0}
                                _ord0 = sorted(_p0, key=lambda p: _ang0[p])
                                _seq0 = netlayout.order_spans(_ord0, e2)
                                if _seq0 != _ord0:
                                    _nn = len(_seq0)
                                    _R2 = max(hw * 2.0, _nn * (2.0 * hw + 30.0)
                                              / (2.0 * math.pi))
                                    for _k, _p in enumerate(_seq0):
                                        if _p in pinned:
                                            continue
                                        _a = (-math.pi / 2
                                              + 2.0 * math.pi * _k / _nn)
                                        _p0[_p] = (cx + _R2 * math.cos(_a),
                                                   cy + _R2 * math.sin(_a))
                                    if __import__("os").environ.get("NL_DEBUG"):
                                        print("[dbg]   回退起点重排环序 %d → %d"
                                              % (netlayout.span_cost(_ord0, e2),
                                                 netlayout.span_cost(_seq0, e2)),
                                              flush=True)
                            except Exception:
                                pass
                            _best = None
                            # v0.39：人多时从高净空往下降要跑满四档——30 人
                            # 实测 8.9 秒，点开关系网络先卡十秒。而高净空在
                            # 人多时本来就做不到（卡片占满环，线只能从缝里
                            # 挤）。所以 >=24 人改成只跑中档两档 + 放低验收线
                            # （6px 已经肉眼可见不压卡），时间砍掉六成。
                            if len(_p0) >= 24:
                                # v0.42：30 人实测这一档一跑就是 8~10 秒。原因是
                                # 候选位置太密：每张卡要扫约 385 个 (r, θ) 组合，
                                # 每个组合对 37 条线算一次 seg_rect_gap，单次
                                # relax_free 就是百万级调用（cProfile 实测全流程
                                # 2027 万次）。20px 净空在 30 人 / 37 条边时本来
                                # 也做不到，细扫纯属浪费——改粗扫：步长放大一倍，
                                # 只跑一档 2 轮。
                                _ladder, _accept = (20.0,), 6.0
                                _sweeps, _sr, _sa, _da = 2, 44.0, 0.07, 0.45
                            else:
                                _ladder = (self.FREE_CLEAR, 30.0, 20.0, 14.0)
                                _accept = 10.0
                                _sweeps = self.FREE_SWEEPS
                                _sr, _sa, _da = (self.FREE_STEP_R,
                                                 self.FREE_STEP_A,
                                                 self.FREE_DA)
                            _tA = time.time()
                            for _cl in _ladder:
                                _q = dict(_p0)
                                _l, _m = netlayout.relax_free(
                                    _q, segs, cx, cy, hw, hh,
                                    clear=_cl, r_lo=self.FREE_R_LO,
                                    r_hi=self.FREE_R_HI, da_slots=_da,
                                    step_r=_sr, step_a=_sa,
                                    sweeps=_sweeps,
                                    r_min=self.ME_R + NODE_H / 2.0 + 34.0,
                                    obs=[],
                                    pinned=pinned)
                                _g = netlayout.min_gap(_q, e2, list(_q), hw, hh)
                                if _best is None or _g > _best[0]:
                                    _best = (_g, _q, _l, _m)
                                if _g >= _accept:
                                    break
                            _g, _q, left, mv = _best
                            pos.clear()
                            pos.update(_q)
                            # 收尾：二维搜索对「自己那条线扫到别人」是看不到的
                            # （pen 只算与这张卡有关的段），密图上会留下个别
                            # 12px 的擦边。这一段一维下降只做扫尾——它只沿
                            # 半径动，不会毁掉上面的角向自由度。
                            _tB = time.time()
                            netlayout.relax_clear(
                                pos, segs, cx, cy, hw, hh,
                                clear=self.CHANNEL, r_lo=0.90, r_hi=1.20,
                                # v0.42：人多时也别只扫 2 轮——30 人实测 2 轮
                                # 收完还剩 5 条线压进卡里（净空 −35.8px），加到
                                # 5 轮多花 0.4 秒，压卡能明显减轻。
                                sweeps=(5 if len(pos) >= 24 else 6),
                                pinned=pinned)
                            if __import__("os").environ.get("NL_DEBUG"):
                                print("[dbg]   relax_free %.2fs  relax_clear %.2fs"
                                      % (_tB - _tA, time.time() - _tB),
                                      flush=True)
                            self._net_avoid = (left, round(mv, 1))
                            if not pinned and len(pos) >= 20:
                                # v0.37：四道尝试都没建起来，说明这个人数
                                # + 关系密度在本卡片尺寸下放不下（半径要退到
                                # 下限以内）。别让用户以为是 bug。
                                _g2 = netlayout.min_gap(pos, e2, list(pos), hw, hh)
                                if _g2 < 10.0:
                                    self.status_var.set(
                                        "人数较多：关系线可能压到卡片，建议手动拖动调整")
                                else:
                                    self.status_var.set(
                                        "人数较多：长线仍会横穿辐射线，可手动拖动调整")
                        else:
                            self._net_avoid = (info["cross"], info["gap"])
                            self._net_inner = len(info["inner"])
                            # 这一次建起来了，就把上一次兼容布局留在状态栏的
                            # 「人数较多」提示清掉——否则画面明明干净，底下
                            # 还在喊「关系线可能压到卡片」（30 人实测净空
                            # 26.9px 却显示报警）。
                            if str(self.status_var.get()).startswith("人数较多："):
                                self.status_var.set("")
                    else:
                        base = dict(pos)
                        left, mv = netlayout.avoid(
                            pos, e2, cx, cy, NODE_W / 2.0, NODE_H / 2.0,
                            slot_da=getattr(self, "_slot_theta", 0.3),
                            base=base, pinned=pinned, me_r=self.ME_R,
                            clear=self.LINK_CLEAR, rounds=40)
                        self._net_avoid = (left, round(mv, 1))
                except Exception as _ex:
                    if __import__("os").environ.get("NL_DEBUG"):
                        import traceback
                        print("[dbg]   布局异常:", _ex, flush=True)
                        traceback.print_exc()
                    self._net_avoid = None
        # v0.42：最后一道底线。上面每一步避让都会把卡往别处推，推完可能又
        # 压上；人多（30 人 / 37 条边）时尤其明显，最终画出「卡片压成一坨」
        # 的图，用户点都点不中。这里再跑一遍纯几何分离，只保「任意两张卡
        # 不重叠」——宁可线乱，卡片必须点得中。
        if rebuilt and not freeze and len(pos) > 1:
            _hw, _hh = NODE_W / 2.0, NODE_H / 2.0
            for _ in range(6):
                if not netlayout.overlap_pairs(pos, _hw, _hh):
                    break
                netlayout.separate_cards(pos, cx, cy, _hw, _hh,
                                         gap=10.0, rounds=40, pinned=pinned)
        if rebuilt and not freeze and generations and self.layout_mode != "free":
            _edges = [(l["person_a"], l["person_b"]) for l in links]
            netlayout.auto_layers(pos, _edges, cx, cy, NODE_W / 2.0,
                                  NODE_H / 2.0, clear=12.0,
                                  generations=generations, pinned=pinned)
        if (rebuilt and not freeze and not pinned and len(pos) >= 24
                and not generations and self.layout_mode != "free"):
            # 仅重建时优化；缩放、拖动、刷新必须复用保存的位置。
            _edges = [(l["person_a"], l["person_b"]) for l in links]
            _polished = netlayout.polish_geometry(pos, _edges, cx, cy,
                                                  NODE_W / 2.0, NODE_H / 2.0)
            if __import__("os").environ.get("NL_DEBUG"):
                print("[dbg] polish moves", _polished, flush=True)
            _unique = sorted({tuple(sorted((a, b))) for a, b in _edges
                              if a in pos and b in pos and a != b})
            _gap = netlayout.min_gap(pos, _unique, list(pos),
                                     NODE_W / 2.0, NODE_H / 2.0)
            _overlap = netlayout.overlap_pairs(pos, NODE_W / 2.0,
                                               NODE_H / 2.0)
            self._net_avoid = (_overlap, round(_gap, 1))
            self.status_var.set(
                ("布局完成：卡片无重叠，关系线净空 %.1fpx；部分连线仍有交叉"
                 % _gap) if _overlap == 0 and _gap >= 0 else
                "布局完成：仍有局部干涉，可手动拖动调整")
        self.node_pos = pos
        self._web_center = (cx, cy)

        # 以全量实际边的同一份直线快照评估；隐藏后的图不反过来驱动激活。
        self._layout_links = links
        all_routes = self._route_links(all_view["display_links"], pos, cx, cy)
        self._circles = netlayout.adaptive_circles(
            circle_candidates, all_routes, llm.circle_internal_link)
        self._relationship_view = build_relationship_view(
            persons, links, self._circles,
            is_circle_internal=llm.circle_internal_link,
            show_all=self._show_all_links.get(),
            normalize_relation=db.normalize_link_relation)
        self._links_cache = self._relationship_view["display_links"]
        self._link_routes = self._route_links(self._links_cache, pos, cx, cy)
        self._me_routes = netlayout.me_connections(pos, cx, cy, NODE_W / 2, NODE_H / 2,
                                                   radius=self.ME_R)
        self._link_items = []
        self._link_imgs = []
        self._link_style = {}

        # ---------- 可滚动区域（世界系）与视口（先定坐标再画内容） ----------
        # v0.9.2：旧实现 scrollregion 固定画布 ±300，可把网络整体拖出视口。
        # v0.10：内容包围盒 = 所有卡片（布局系半幅 NODE_W/2）+「我」+ 24px
        # 视野边距，映射到世界系（(L−中心)×zoom+中心）；再并入当前视口矩形
        # （视口永远在 region 内 → 保留视口永不跳位/钳制），四边各外扩
        # 10% 视口（缩小看全貌时网络小于视口，也能拖动 reposition）。
        # Tk 自动钳制视口于 region 内 → 网络永远拖不出去；
        # 而布局本身按人数增长（need_r）→ zoom=1 内容即溢出视口 → 随时可拖
        vw, vh = W, H                      # 画布即视口
        hw, hh = NODE_W / 2, NODE_H / 2    # 布局系卡片半幅（与 zoom 无关）
        m = 24.0                           # 视野边距（贴边时留一线呼吸）
        pts = list(pos.values()) + [(cx, cy)]
        for route in self._me_routes:
            pts.extend(route["pts"])
        # v0.27：直线端点与标签也在包围盒内（端点就是卡边，标签在卡外侧）
        for rt in self._link_routes:
            pts.extend(rt["pts"])
            if rt["label_default"]:
                pts.append(rt["label_a"])
                pts.append(rt["label_b"])
        lbx0 = min(p[0] for p in pts) - hw - m
        lbx1 = max(p[0] for p in pts) + hw + m
        lby0 = min(p[1] for p in pts) - hh - m
        lby1 = max(p[1] for p in pts) + hh + m
        # 布局系 → 世界系（以画布中心为锚 × zoom）
        wbx0 = (lbx0 - cx) * z + cx
        wbx1 = (lbx1 - cx) * z + cx
        wby0 = (lby0 - cy) * z + cy
        wby1 = (lby1 - cy) * z + cy
        # 换 scrollregion 前先读旧视口左上角（世界系，视口保留用）
        old_vx = old_vy = 0.0
        if self._view_inited:
            try:
                osx0, osy0, osx1, osy1 = self._sr
                old_vx = c.xview()[0] * max(1.0, osx1 - osx0) + osx0
                old_vy = c.yview()[0] * max(1.0, osy1 - osy0) + osy0
            except Exception:
                pass
        # 内容包围盒（世界系，未并入视口）——用于决定四边距
        cont_w, cont_h = wbx1 - wbx0, wby1 - wby0
        # v0.29：世界至少 2.4 屏 × 2.4 屏（以内容中心为中心）——用户反馈
        # 「画布为什么有空气墙」，旧版只给「内容包围盒 + 10%~25% 视口」，
        # 网一大就只剩百来像素可拖，拖两步就撞墙。现在无论人多人少，画布
        # 都有将近 1.4 屏的空地可以随手摆卡；内容超出时世界跟着内容长。
        min_w, min_h = 2.4 * vw, 2.4 * vh
        if cont_w < min_w:
            mid = (wbx0 + wbx1) / 2
            wbx0, wbx1 = mid - min_w / 2, mid + min_w / 2
        if cont_h < min_h:
            mid = (wby0 + wby1) / 2
            wby0, wby1 = mid - min_h / 2, mid + min_h / 2
        wbx0 = min(wbx0, old_vx)
        wby0 = min(wby0, old_vy)
        wbx1 = max(wbx1, old_vx + vw)
        wby1 = max(wby1, old_vy + vh)
        # 四边再留 10% 视口：内容超出视口时也能滚到最外圈卡片外侧。
        # 旧实现「内容小于视口给 25%」的分支已被上面的 2.4 屏主场取代
        mx = max(24.0, 0.10 * vw)
        my = max(24.0, 0.10 * vh)
        wbx0 -= mx; wby0 -= my; wbx1 += mx; wby1 += my
        region = (wbx0, wby0, wbx1, wby1)
        c.configure(scrollregion=region)
        self._sr = region
        ntw = max(1.0, wbx1 - wbx0)
        nth = max(1.0, wby1 - wby0)
        if not self._view_inited:
            # 首画 / 窗口尺寸变化：视口对准**内容包围盒中心**。
            # v0.27 曾改成对齐「我」——但避让会把个别卡沿半径外推几十像素，
            # 包围盒不再对称，对准圆心会把外凸那侧的卡切在视口外（实测底部
            # 两张卡被裁）。改回包围盒中心：环近似对称，误差很小，而「一张
            # 卡都不切」是硬要求。
            ccx, ccy = self._l2s((lbx0 + lbx1) / 2, (lby0 + lby1) / 2)
            c.xview_moveto(max(0.0, min(1.0, (ccx - vw / 2 - wbx0) / ntw)))
            c.yview_moveto(max(0.0, min(1.0, (ccy - vh / 2 - wby0) / nth)))
            self._view_inited = True
        else:
            # 刷新/缩放保持视口：世界系原点不变 → 光标下的内容点不动，
            # 天然就是「以光标为锚缩放」
            c.xview_moveto(max(0.0, min(1.0, (old_vx - wbx0) / ntw)))
            c.yview_moveto(max(0.0, min(1.0, (old_vy - wby0) / nth)))

        # 背景纹理层（v0.10 默认关，⚙更多→🎨背景 可开）——先画，堆叠最底层
        if self._bg_on:
            self._draw_bg(W, H, cx, cy, region, Rmax)
        else:
            c.delete("bg")

        # v0.63：圈色块背景停绘——划分交给「场景卡」（用户 11-07 定案：
        # 点场景卡切换即可，不再用底色色块划分区域）。方法体保留备用。
        # self._draw_circle_regions()

        # 每个可见人物都有一条到我的常驻展示连接，不写入人物间事实关系。
        # v0.57：中心线统一压成浅色细线，避免 31 条线在视觉上盖过亲属关系。
        self._me_items = []
        self._me_line_by_pid = {}
        for route in self._me_routes:
            flat = [v for point in route["pts"] for v in self._l2s(*point)]
            line = c.create_line(*flat, fill=ME_LINE_BASE,
                                 width=max(1.0, 0.9 * z), capstyle="round",
                                 joinstyle="round", tags=("meline", f"me_{route['pid']}"))
            self._me_items.append((line, route["pid"]))
            self._me_line_by_pid[route["pid"]] = line
        self._draw_relationship_lines()

        self._draw_me_node(cx, cy)

        # 节点卡片（PIL 整卡合成：投影/圆角/药丸/头像/文字全部抗锯齿；
        # hover 与连线源高亮 = 整卡重渲染，不再靠 canvas 形状描边）
        self._node_colors = {}
        self._node_items = {}
        self._node_src = {}
        for it in items:
            pid = it["pid"]
            color = TIER_COLOR.get(it["tier"], MUTED)
            self._node_colors[pid] = color
            name = it["name"] if len(it["name"]) <= 8 else it["name"][:7] + "…"
            has_memo = bool((it["_p"].get("memo_md") or "").strip())
            self._node_src[pid] = (name, it["score"], it["tier"], color,
                                   it.get("avatar"), has_memo)
            state = "src" if self._linking_src == pid else "normal"
            img = self._render_node(pid, state)
            if img is None:
                continue
            self._card_imgs[pid] = img
            item = c.create_image(*self._l2s(*pos[pid]), image=img,
                                  tags=(f"card_{pid}", f"node_{pid}"))
            self._node_items[pid] = {"item": item, "state": state}

        # v0.63：圈编号徽标随色块一并停绘（场景卡替代划分）
        # self._draw_circle_badges()
        self._draw_relationship_labels()

        self._place_vignette()

        # v0.5：启动连线微动画（线的角度/长度持续轻微变化）
        self._start_anim()

    def _draw_relationship_lines(self):
        """默认只显亲属线；其他真实关系在悬停人物时展开，勾选后全显。

        v0.57：过去把非亲属线统一"淡化"但仍画出来，32 条再加上 31 条中心线，
        整幅图仍是一团线。现在默认不画非亲属线（数据与标签都在，只是不占画布），
        悬停任一相关人物或勾选"显示全部连线"即可还原。
        """
        c = self.web_canvas
        for rt in self._link_routes:
            lnk = rt["lnk"]
            family = any(r.get("relation") in llm.FAMILY_RELATIONS - {"家人"}
                         for r in lnk.get("records", [lnk]))
            col = "#AD7952" if family else LINK_STYLE.get(lnk.get("relation"), (CYAN, ()))[0]
            wdt = max(1.0, min(2.0, (1.7 if family else 1.2) * self._zoom))
            flat = [v for point in rt["pts"] for v in self._l2s(*point)]
            tag = f"link_{lnk['id']}"
            visible = family or self._show_all_links.get()
            state = "normal" if visible else "hidden"
            gid = c.create_line(*flat, fill=_mix(col, BG, 0.74), width=wdt + 2.6,
                                tags=("linkglow", tag), capstyle="round", state=state)
            lid = c.create_line(*flat, fill=col, width=wdt,
                                tags=("link", tag), capstyle="round", state=state)
            self.edges.setdefault(lnk["person_a"], []).append(lid)
            self.edges.setdefault(lnk["person_b"], []).append(lid)
            self._link_style[lnk["id"]] = {"col": col, "w": wdt, "tag": tag,
                                           "line": lid, "glow": gid, "route": rt,
                                           "default_visible": visible}

    def _draw_relationship_labels(self):
        """亲属胶囊常显，次要语义保留在对应人物对上按需展开。"""
        c = self.web_canvas
        for st in self._link_style.values():
            rt = st["route"]
            txt = rt["lnk"].get("label") or rt["lnk"].get("relation") or "关系"
            mx, my = self._l2s(*rt["label_a"])
            leader = [v for point in rt["label_leader"] for v in self._l2s(*point)]
            st["leader"] = c.create_line(*leader, fill=_mix(st["col"], BG, 0.45),
                                        width=1, dash=(2, 3),
                                        state="normal" if rt["label_visible"] and math.dist(*rt["label_leader"]) * self._zoom > 18 else "hidden",
                                        tags=("relationship_label_leader", st["tag"]))
            tags = ("relationship_label", f"label_{rt['lnk']['id']}", st["tag"])
            size = max(7, min(11, round(9 * self._zoom)))
            image = images_util.smooth_pill(txt, st["col"], size=size)
            if image is not None:
                self._pill_imgs.append(image)
                c.create_image(mx, my, image=image, tags=tags)
            else:
                text = c.create_text(mx, my, text=txt, fill=st["col"],
                                     font=(UI_FONT, size), tags=tags)
                x0, y0, x1, y1 = c.bbox(text)
                background = _rounded(c, x0 - 5, y0 - 3, x1 + 5, y1 + 3,
                                      r=7, fill=SURFACE, outline=BORDER, tags=tags)
                c.tag_raise(text)
            c.itemconfigure(f"label_{rt['lnk']['id']}",
                            state="normal" if rt["label_visible"] else "hidden")

    def _sync_me_connections(self):
        """按当前布局与缩放同步展示层中心直线。"""
        cx, cy = self._web_center
        self._me_routes = netlayout.me_connections(self.node_pos, cx, cy,
                                                   NODE_W / 2, NODE_H / 2,
                                                   radius=self.ME_R)
        lines = {pid: line for line, pid in self._me_items}
        for route in self._me_routes:
            line = lines.get(route["pid"])
            if line is not None:
                flat = [v for point in route["pts"] for v in self._l2s(*point)]
                self.web_canvas.coords(line, *flat)

    def _draw_me_node(self, cx, cy):
        """账号节点与人物自由分布并存，维护评分仍只在人物卡片表达。"""
        c, z, me = self.web_canvas, self._zoom, self.me
        name = (me.get("name") or "我").strip() or "我"
        image = images_util.render_me_node(me.get("avatar"), name,
                                           d=max(40, int(116 * z)))
        if image is not None:
            self._me_img = image
            c.create_image(*self._l2s(cx, cy - 6), image=image, tags=("menode",))
            return
        sx, sy = self._l2s(cx, cy)
        radius = max(18, 30 * z)
        c.create_oval(sx - radius, sy - radius, sx + radius, sy + radius,
                      fill=SURFACE2, outline=CYAN, width=1.5, tags=("menode",))
        c.create_text(sx, sy, text="我", fill=CYAN, font=FONT_SM, tags=("menode",))
        c.create_text(sx, sy + radius + 12, text=name, fill=MUTED,
                      font=FONT_XS, tags=("menode",))

    # ---------- 动态连线：环境微动画 ----------
    def _start_anim(self):
        """以当前节点位置为锚点，启动低频漂移动画（活网络）"""
        self._stop_anim()
        if not self._anim_on or not getattr(self, "node_pos", None):
            return
        self._anim_base = {pid: tuple(p) for pid, p in self.node_pos.items()}
        self._anim_t = 0.0
        self._anim_job = self.after(60, self._anim_step)

    def _stop_anim(self):
        if getattr(self, "_anim_job", None):
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

    def _anim_step(self):
        """每帧：节点绕锚点缓慢漂移 → 连线角度/长度连续变化 → 中线标签跟随
        v0.10：漂移在布局系（L），画布位移 = L 位移 × zoom"""
        if not self._anim_job or not self._anim_on:
            return
        c = self.web_canvas
        self._anim_t += 0.06
        t = self._anim_t
        z = getattr(self, "_zoom", 1.0)
        for pid, (bx, by) in self._anim_base.items():
            cur = self.node_pos.get(pid)
            if cur is None:
                continue
            ph = (pid * 1.7) % 6.283
            nx = bx + 5.0 * math.sin(t * 0.9 + ph)
            ny = by + 4.0 * math.cos(t * 0.7 + ph * 1.3)
            mdx, mdy = nx - cur[0], ny - cur[1]
            if abs(mdx) < 0.05 and abs(mdy) < 0.05:
                continue
            c.move(f"card_{pid}", mdx * z, mdy * z)
            c.move(f"node_{pid}", mdx * z, mdy * z)
            self.node_pos[pid] = (nx, ny)
        cx, cy = self._web_center
        routes = self._route_links(self._links_cache, self.node_pos, cx, cy,
                                   focus_pid=self._hover_pid)
        for route in routes:
            st = self._link_style.get(route["lnk"]["id"])
            if not st:
                continue
            flat = [v for point in route["pts"] for v in self._l2s(*point)]
            c.coords(st["line"], *flat)
            c.coords(st["glow"], *flat)
            old = self._l2s(*st["route"]["label_a"])
            new = self._l2s(*route["label_a"])
            c.move(f"label_{route['lnk']['id']}", new[0] - old[0], new[1] - old[1])
            if "leader" in st:
                c.coords(st["leader"], *[v for point in route["label_leader"] for v in self._l2s(*point)])
                c.itemconfigure(st["leader"], state="normal" if route["label_visible"] and math.dist(*route["label_leader"]) * self._zoom > 18 else "hidden")
            st["route"] = route
        self._link_routes = routes
        self._sync_me_connections()
        self._update_circle_regions()
        self._anim_job = self.after(60, self._anim_step)

    def toggle_anim(self):
        """⚙更多：开/关连线微动画"""
        self._anim_on = not self._anim_on
        if self._anim_on:
            self._start_anim()
            self.status_var.set("动态连线已开启（线的角度/长度会持续轻微变化）")
        else:
            self._stop_anim()
            self.status_var.set("动态连线已关闭")

    def _force_positions(self, items, links, pinned, ring_pos, W, H, cx, cy):
        """力导向布局（Fruchterman-Reingold）：按人物间连线自动生成网络

        - 有连线的节点互相吸引（关系越亲，弹簧静止长度越短）
        - 人物两两之间互相排斥（k²/d）
        - 中心「我」是容器：近处弱斥力（防止节点塌到圆心）+ 远处引力（d²/k），
          与相互斥力平衡后自然形成以「我」为圆心的扩散环——取代旧的
          「中心全额斥力 + 0.012 微弱向心力」（斥力恒大于向心力约 30 倍，
          节点会被炸到画布边缘钳位，智能布局退化成贴边一排且互相重叠）
        - pinned 中的节点（用户拖拽固定过）位置不变
        - v0.10.1：布局系与 zoom 完全无关（zoom 只在布局→世界映射时统一缩放）；
          k 随人数温和增长（need_r·1.25，卡宽×1.2 为底、画布短边×0.45 为顶）——
          人少紧凑靠拢、人多铺开不重叠；钳制区放宽到画布外 50% → 人多时布局
          自然溢出画布，zoom=1 就有可拖动内容（配合滚轮缩小 = 人多也方便观察）；
          一律从分环初位全温重排（布局复用由 _draw_web 签名机制承担，能走到
          这里必然是该重排；旧「低温续跑」会在尺寸变化时把节点冻结在旧中心
          相对位置上，挤成一团且偏离新中心）
        """
        ids = [it["pid"] for it in items]
        pos = {}
        # v0.10.1：一律从分环初位全温重排（pinned 尊重用户固定位置）。
        # 旧实现的「低温续跑」在画布尺寸变化时把节点冻结在旧中心的相对
        # 位置上——启动过程中尺寸多次变化 → 节点挤成一团且偏离新中心
        # （用户反馈散架图的根因之一）。布局复用已由 _draw_web 的签名
        # 机制承担（结构/尺寸没变直接沿用），能走到这里必然是该重排。
        for pid in ids:
            if pid in pinned:
                pos[pid] = [float(pinned[pid][0]), float(pinned[pid][1])]
            else:
                rx, ry = ring_pos.get(pid, (cx, cy))
                pos[pid] = [float(rx), float(ry)]

        n = len(ids) + 1                     # + 中心「我」
        # v0.10.1：k（理想间距）随人数温和增长——人少时紧凑靠拢，人多才铺开。
        # 旧公式 sqrt(W·H/n)·0.85 在 4 人时给出 ~400px 间距，小网络被直接
        # 炸满画布（卡片甩到边缘、me 线拉老长——用户反馈散架图的根因之一）。
        # 期望间距 ≈ 外环周长/人数（need_r 即「n 张卡不重叠所需半径」），
        # 下限卡宽×1.2（防重叠），上限画布短边×0.45（防无限铺开）
        need_r = len(ids) * (NODE_W + 24) / 6.283185307 * 1.05
        k = min(max(NODE_W * 1.2, need_r * 1.25), min(W, H) * 0.45)
        # 全温充分展开。初值 min(W,H)/20——分环初位本就贴近平衡点，过大的
        # 初始温度会让节点在冷却前冲过平衡点（4 人网络被甩到 500px 外），
        # 减半后展开充分且不过冲；线性冷却让引力/斥力全程持续精修
        t0 = min(W, H) / 20.0
        # v0.21：迭代数随人数自适应 + 收敛早停——斥力是 O(n²)，300 轮 ×
        # 200 人 = 600 万对距离计算（>3s，人多卡顿的元凶之一）。人少给足
        # 300 轮精修；人多减到 110-160 轮（大人数本就只能到粗平衡），
        # 再加每轮总位移 < 0.5px 早停（通常 100 轮内已收敛，实测省 60%）
        if len(ids) <= 24:
            iters = 300
        elif len(ids) <= 80:
            iters = 160
        else:
            iters = 110
        # 布局钳制区 = 画布外扩 50%（v0.10：允许布局溢出画布，溢出部分
        # 靠拖动/缩小查看；zoom 不参与——布局与 zoom 解耦，缩放时
        # 钳制边界不变，不会经斥力耦合撼动整网平衡）
        hw, hh = NODE_W / 2, NODE_H / 2
        cminx, cmaxx = -0.5 * W, 1.5 * W
        cminy, cmaxy = -0.5 * H, 1.5 * H
        for it_i in range(iters):
            # 线性冷却：旧实现 ×0.96 指数衰减约 50 轮就趋近 0，布局被速冻在
            # 「斥力把节点冲向边缘」的爆炸初期；线性冷却让引力/斥力全程持续精修
            temp = t0 * (1.0 - it_i / iters)
            disp = {pid: [0.0, 0.0] for pid in ids}
            # 斥力（人物两两之间）
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    u, v = ids[i], ids[j]
                    dx = pos[u][0] - pos[v][0]
                    dy = pos[u][1] - pos[v][1]
                    d2 = dx * dx + dy * dy
                    if d2 < 0.01:              # 完全重合时给微小确定性偏移
                        dx, dy = 0.1 + 0.01 * i, 0.1 + 0.01 * j
                        d2 = dx * dx + dy * dy
                    d = math.sqrt(d2)
                    f = k * k / d
                    ux, uy = dx / d * f, dy / d * f
                    disp[u][0] += ux; disp[u][1] += uy
                    disp[v][0] -= ux; disp[v][1] -= uy
            # 弹簧引力（沿连线）；v0.5：静止长度由关系分决定——分越高线越短
            for lnk in links:
                a, b = lnk["person_a"], lnk["person_b"]
                if a not in pos or b not in pos:
                    continue
                lscore = lnk.get("score")
                lscore = 50 if lscore is None else int(lscore)
                rest = k * (1.25 - lscore / 100.0)
                dx = pos[a][0] - pos[b][0]
                dy = pos[a][1] - pos[b][1]
                d = max(0.01, math.hypot(dx, dy))
                f = (d - rest) * d / k          # 弹簧力：d>rest 吸引，d<rest 排斥，平衡于 rest
                disp[a][0] -= dx / d * f; disp[a][1] -= dy / d * f
                disp[b][0] += dx / d * f; disp[b][1] += dy / d * f
            # 中心「我」容器力：近处弱斥力（不塌圆心）+ 远处引力（拉回画布中心）
            for pid in ids:
                dx = pos[pid][0] - cx
                dy = pos[pid][1] - cy
                d = max(0.01, math.hypot(dx, dy))
                f = 0.3 * k * k / d - d * d / k   # >0 向外 / <0 向内
                disp[pid][0] += dx / d * f
                disp[pid][1] += dy / d * f
            # 冷却应用
            moved = 0.0
            for pid in ids:
                if pid in pinned:
                    continue
                d = math.hypot(disp[pid][0], disp[pid][1])
                if d > 0:
                    lim = min(d, temp)
                    pos[pid][0] += disp[pid][0] / d * lim
                    pos[pid][1] += disp[pid][1] / d * lim
                    moved += lim
                pos[pid][0] = min(max(pos[pid][0], cminx), cmaxx)
                pos[pid][1] = min(max(pos[pid][1], cminy), cmaxy)
            if moved < 0.5:               # v0.21：布局收敛 → 早停（省 60% 迭代）
                break
        return {pid: (pos[pid][0], pos[pid][1]) for pid in ids}

    def _radial_positions(self, items, pinned, W, H, cx, cy, links=None):
        """v0.30 辐射布局：单环 + 均分卡位 + 弦长最优的环序（配合直线连线）

        用户诉求（2026-10-21 / 10-28 / 10-28）：
        「自动布局应该实现线和线没有交叉、线和人物卡片也没有干涉的状态」
        「线还是直线连接，是我在操作自动布局的时候找到不发生线和其他元素
        干涉的位置」
        「这个是自动布局后无干涉的效果吗？为什么比我手动布局的差那么多？」

        v0.27 的问题（用户一眼看出来的）：环序用「引荐树前序」硬拼——一个引
        荐人带 4 个孩子时，除了两侧的孩子，其余必然被甩到远端，弦只能横穿
        半个圆；再加上 netlayout.avoid 沿半径把挡路的卡往外推，圆环被推成
        ±15% 的高低不平。合起来就是「线在中间乱穿 + 卡片一圈高高低低」。

        v0.30 换成两件事：
        ① **环序做优化**（netlayout.order_cycle）：以前序为初值，局部搜索
           「交换两张卡 / 把一张卡搬到别处」，代价 = Σ 跨距² + 缝流量²。
           弦越短越好、也别全挤在同一段弧上，于是四五个孩子的星形被摊到
           引荐人两侧，同一簇人自动挨在一起。
        ② **卡位按「缝流量」分配宽度**（v0.32，netlayout.place_uneven）：
           半径统一（环仍是正圆），但角度不再均分——关系线多的那段缝更宽，
           线少的更窄。18 人时均分只剩 26px 缝、却有缝要挤 8 条线（≈需
           60px），卡宽周长都固定，只有按需求分配才挤得开。这是用户手摆
           时下意识在做的事（「哪里线多，哪里把卡松开一点」）。

        无交叉说明：
        - 辐射线（我↔人物）是半径射线，每张卡独占唯一角度 → 任意两条辐射
          线除圆心外不相交；
        - 关系弦的跨距被压到最小（多数为 1 个卡位，即相邻两卡），贴着环内
          侧走，不穿别的卡；跨得远的少数几条由切向微调让出净空；
        - 弦不切圆心：跨距 ≤ n/2 时弦到圆心的距离 ≥ R·cos(π/n) ≫ 我 的半径。

        静态：布局一次算好，无迭代无动画。pinned（用户拖拽固定过）的位置原
        样保留，不参与排队。
        """
        pos = dict(pinned)
        auto = [it for it in items if it["pid"] not in pinned]
        if not auto:
            return pos
        # ① 引荐树：父 = intro_by 指向的那个人（按名字匹配；不在图里的视为根）
        by_pid = {it["pid"]: it for it in auto}
        name2pid = {}
        for it in items:                      # 含已固定的卡：引荐指向它们时也要认
            nm = (it.get("name") or "").strip()
            if nm:
                name2pid.setdefault(nm, it["pid"])
        # ① 两条线都算「关系」：显式连线 + 引荐（intro_by 指向谁）
        edges, wts = [], {}
        for l in (links or []):
            edges.append((l["person_a"], l["person_b"]))
        kids, roots = {}, []
        for it in auto:
            nm = ((it.get("intro_by")
                   or (it.get("_p") or {}).get("intro_by") or "").strip())
            par = name2pid.get(nm) if nm else None
            if par is not None and par != it["pid"]:
                kids.setdefault(par, []).append(it)
                edges.append((par, it["pid"]))
                wts[(par, it["pid"])] = 0.8   # 引荐只当提示，权重低于显式关系
            else:
                roots.append(it)
        for g in kids.values():
            g.sort(key=lambda x: x["score"], reverse=True)
        roots.sort(key=lambda x: x["score"], reverse=True)
        # ② 初值 = 引荐树前序（保住「父在前、孩子在后」的大结构）
        pre, seen = [], set()

        def visit(it):
            if it["pid"] in seen:
                return
            seen.add(it["pid"])
            pre.append(it["pid"])
            for ch in kids.get(it["pid"], []):
                visit(ch)

        for r in roots:
            visit(r)
        for it in auto:                       # 兜底（循环引荐等异常数据）
            visit(it)
        # ③ 环序优化：局部搜索把每条弦的跨距压到最短（见 netlayout.order_cycle）。
        #    星形（一个引荐人带四五个孩子）会被摊到引荐人两侧，不再全堆一边
        #    让四五条线横穿半个圆。
        order = netlayout.order_cycle([it["pid"] for it in auto], edges,
                                      weights=wts, init=pre, load_w=0.3)
        n = len(order)
        theta = 2 * math.pi / n
        R0 = _ring_radius_chart(n, W, H)  # 与 fit_view 共用同一公式（供 stub 调用）
        # ④ v0.32 卡位不再严格均分：先按均分排一版，量出每条缝要被几条线穿过，
        #    再按「流量」重排。18 人时均分卡位只剩 26px 缝、却有缝要挤 4 条线
        #    （需 ~50px）——卡宽与周长都固定，只有把缝按需求分配才挤得开。
        tmp = {}
        ang = -math.pi / 2.0
        for pid in order:
            tmp[pid] = (cx + R0 * math.cos(ang), cy + R0 * math.sin(ang))
            ang += theta
        loads = netlayout.slot_loads(order, edges, tmp, cx, cy)
        ring, R, gaps = netlayout.place_uneven(order, loads, NODE_W / 2.0,
                                               cx, cy, min_r=R0)
        if not ring:                          # 极端兜底：退回均分
            ring, R, gaps = tmp, R0, [theta] * n
        pos.update(ring)
        self._slot_theta = theta
        self._ring_r = R
        self._slot_gaps = gaps
        self._slot_loads = loads
        return pos


    def _relationship_label_default(self, link):
        records = link.get("records") or [link]
        return self._show_all_links.get() or any(
            r.get("relation") in llm.FAMILY_RELATIONS - {"家人"} for r in records)

    def _route_links(self, links, pos, cx, cy, focus_pid=None):
        """把每条关系线路由成**一条直线段**：卡片边 → 卡片边

        用户诉求（2026-10-28）：「线还是直线连接，是我在操作自动布局的时候
        找到不发生线和其他元素干涉的位置」——线的形态回到直线，干涉由布局
        负责（netlayout.avoid 在卡位内微调卡片，见 _draw_web）。

        端头裁到卡片边框外 1.5px（线像从卡边长出来），于是：
        · 线不会钻到卡片下面（卡片圆角处不会露出线头）；
        · 标签锚点就落在卡边外侧，悬停时不会压住头像。
        返回 list[{"lnk","pts"(布局系两点),"label_a","label_b","reach"}]
        """
        if len(pos) < 2:
            return []
        hw, hh = NODE_W / 2.0, NODE_H / 2.0
        # 同对人物的多条语义由纯投影模块聚合，渲染不再按最高分删关系。
        out, occupied = [], []
        for lnk in sorted(links, key=lambda link: not self._relationship_label_default(link)):
            pa, pb = lnk["person_a"], lnk["person_b"]
            if pa not in pos or pb not in pos or pa == pb:
                continue
            A, B = pos[pa], pos[pb]
            p1 = netlayout.clip_to_rect(A, B, A, hw, hh, 1.5) or A
            p2 = netlayout.clip_to_rect(B, A, B, hw, hh, 1.5) or B
            # 两卡贴得太近（裁完反序）→ 退化为原始两点
            if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 4.0:
                p1, p2 = A, B
            label_default = self._relationship_label_default(lnk)
            label_visible = label_default or focus_pid in (pa, pb)
            label = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
            if label_visible:
                label, bounds = self._relationship_label_anchor(
                    lnk, p1, p2, pos, cx, cy, occupied)
                occupied.append(bounds)
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            length2 = dx * dx + dy * dy
            fraction = max(0.0, min(1.0, ((label[0] - p1[0]) * dx
                                         + (label[1] - p1[1]) * dy) / length2)) if length2 else 0.5
            foot = (p1[0] + fraction * dx, p1[1] + fraction * dy)
            out.append({"lnk": lnk, "pts": [p1, p2],
                        "label_default": label_default, "label_visible": label_visible,
                        "label_leader": [foot, label],
                        "label_a": label, "label_b": label,
                        "reach": max(math.hypot(p1[0] - cx, p1[1] - cy),
                                     math.hypot(p2[0] - cx, p2[1] - cy))})
        return out

    def _relationship_label_anchor(self, link, a, b, pos, cx, cy, occupied):
        """中点附近选择不遮卡的标签位置；只移动标签，不挪固定人物。"""
        z = max(0.08, self._zoom)
        size = max(7, min(11, round(9 * z)))
        text = link.get("label") or link.get("relation") or "关系"
        tw = sum(size if ord(ch) > 255 else size * 0.62 for ch in text) + 24
        hw, hh = tw / (2 * z), (size + 15) / (2 * z)
        # 人物图片额外含固定屏幕像素留白（宽24、高26），缩小时也需完整避让。
        card_hw = NODE_W / 2 + 16 / z
        card_hh = NODE_H / 2 + 17 / z
        boxes = [(x - card_hw, y - card_hh, x + card_hw, y + card_hh)
                 for x, y in pos.values()]
        boxes.append((cx - 66, cy - 66, cx + 66, cy + 66))
        boxes.extend(occupied)
        dx, dy = b[0] - a[0], b[1] - a[1]
        distance = math.hypot(dx, dy)
        nx, ny = (-dy / distance, dx / distance) if distance else (0.0, 1.0)
        candidates = []
        for fraction in (0.5, 0.38, 0.62, 0.25, 0.75):
            mx, my = a[0] + fraction * dx, a[1] + fraction * dy
            for offset in (0, 20, -20, 38, -38, 62, -62, 90, -90):
                candidates.append((mx + nx * offset / z, my + ny * offset / z))
        candidates.sort(key=lambda p: (p[0] - (a[0] + b[0]) / 2) ** 2
                        + (p[1] - (a[1] + b[1]) / 2) ** 2)
        chosen, best = candidates[0], float("inf")
        for x, y in candidates:
            overlap = sum(max(0, min(x + hw, r) - max(x - hw, l))
                          * max(0, min(y + hh, bottom) - max(y - hh, top))
                          for l, top, r, bottom in boxes)
            if overlap < best:
                chosen, best = (x, y), overlap
            if not overlap:
                break
        # 密集时保留最近的最小遮挡位置，避免远置标签反过来把整图缩小。
        x, y = chosen
        return chosen, (x - hw, y - hh, x + hw, y + hh)

    @staticmethod
    def _label_anchor(here, other, cx, cy):
        """关系标签锚点 = 线段中点，再朝圆心方向收 22px

        v0.27 把标签挂在「卡片正外侧」（沿线段外推 26px + 法向 17px）：环上
        相邻两卡只隔 20°，这个点正好落在**隔壁卡片**上（实测胶囊压住邻卡的
        左边缘）。改挂在中点偏环内一侧——环内是空的，胶囊两边都不压，读起来
        也像「标在这条关系上」。
        """
        mx = (here[0] + other[0]) / 2.0
        my = (here[1] + other[1]) / 2.0
        dx, dy = mx - cx, my - cy
        d = math.hypot(dx, dy) or 1.0
        return (mx - dx / d * 22.0, my - dy / d * 22.0)

    def _pil_text(self, c, x, y, text, size, color, bold=False,
                  anchor="center", tags=()):
        """优先用 PIL 抗锯齿文字图片；PIL 不可用时退 canvas text"""
        img = images_util.smooth_text(text, size, color, bold)
        if img is not None:
            if not hasattr(self, "_text_imgs"):
                self._text_imgs = []
            self._text_imgs.append(img)
            c.create_image(x, y, image=img, anchor=anchor, tags=tags)
            return
        c.create_text(x, y, text=text, fill=color, anchor=anchor, tags=tags,
                      font=(UI_FONT, size, "bold" if bold else ""))

    def _draw_legend(self, parent):
        """单行说明占用画布外页边，缩放和平移均不会遮挡人物。"""
        tk.Label(parent, text="卡片维护分：", bg=BG, fg=MUTED,
                 font=FONT_XS).pack(side="left")
        for name, color in (("核心", RED), ("重要", ORANGE),
                            ("普通", CYAN), ("浅层", MUTED)):
            tk.Label(parent, text=name, bg=BG, fg=color,
                     font=FONT_XS).pack(side="left", padx=(0, 10))
        tk.Label(parent, text="浅细线＝与我相连 · 暖色线＝亲属关系 · 悬停展开其他关系 · 浅色区域＝圈子（勾选可全显）",
                 bg=BG, fg=MUTED, font=FONT_XS).pack(side="left", padx=(6, 0))

    def _node_at(self, x, y):
        """精确命中：返回坐标下的节点 pid，否则 None
        （整卡图片含投影留白，卡片视觉中心比图片中心高 5px，命中按卡片算。
        v0.10：入参为画布系坐标（canvasx/canvasy 之后），内部转布局系
        与 node_pos（布局系）比较，命中框 = 布局系卡片半幅，与 zoom 无关）"""
        lx, ly = self._s2l(x, y)
        for pid, (nx, ny) in self.node_pos.items():
            if abs(lx - nx) <= NODE_W / 2 and abs(ly - (ny - 5)) <= NODE_H / 2:
                return pid
        return None

    def _on_motion(self, e):
        if self._drag:
            return
        sx, sy = self.web_canvas.canvasx(e.x), self.web_canvas.canvasy(e.y)
        pid = self._node_at(sx, sy)
        if pid != self._hover_pid:
            if self._hover_pid is not None:
                self._hover(self._hover_pid, False)
            self._hover_pid = pid
            if pid is not None:
                self._hover(pid, True)
        if self._linking:
            self.web_canvas.config(cursor="crosshair")
        elif pid is not None:
            self.web_canvas.config(cursor="hand2")
        else:
            self.web_canvas.config(cursor="")

    def _render_node(self, pid, state):
        """按 _node_src 缓存参数整卡渲染节点（normal / hover / src）"""
        src = self._node_src.get(pid)
        if not src:
            return None
        name, score, tier, color, avatar, memo = src
        try:
            return images_util.render_node_card(
                name, score, tier, color, avatar_path=avatar,
                has_memo=memo, state=state,
                w=int(NODE_W * self._zoom), h=int(NODE_H * self._zoom))
        except Exception:
            return None

    def _set_relationship_focus(self, pid=None):
        c = self.web_canvas
        cx, cy = self._web_center
        routes = self._route_links(self._links_cache, self.node_pos, cx, cy, focus_pid=pid)
        for route in routes:
            st = self._link_style.get(route["lnk"]["id"])
            if not st:
                continue
            tag = f"label_{route['lnk']['id']}"
            old, new = self._l2s(*st["route"]["label_a"]), self._l2s(*route["label_a"])
            c.move(tag, new[0] - old[0], new[1] - old[1])
            c.itemconfigure(tag, state="normal" if route["label_visible"] else "hidden")
            # 线本身也只在与当前关注人物相连时才出现，避免默认满屏连线。
            focus = pid is not None and pid in (route["lnk"]["person_a"],
                                                route["lnk"]["person_b"])
            line_state = "normal" if (st.get("default_visible") or focus) else "hidden"
            try:
                c.itemconfigure(st["line"], state=line_state)
                c.itemconfigure(st["glow"], state=line_state)
            except Exception:
                pass
            if "leader" in st:
                c.coords(st["leader"], *[v for p in route["label_leader"] for v in self._l2s(*p)])
                c.itemconfigure(st["leader"], state="normal" if route["label_visible"]
                                and math.dist(*route["label_leader"]) * self._zoom > 18 else "hidden")
            st["route"] = route
        self._link_routes = routes

    def _clear_links(self, focus_pid=None):
        """恢复线样式，并仅保留亲属或当前关注人物的标签。"""
        self._set_relationship_focus(focus_pid)
        c = self.web_canvas
        for st in getattr(self, "_link_style", {}).values():
            try:
                c.itemconfigure(st["line"], fill=st["col"], width=st["w"])
                c.itemconfigure(st["glow"], fill=_mix(st["col"], BG, 0.74),
                                width=st["w"] + 2.6)
            except Exception:
                pass
        for line in getattr(self, "_me_line_by_pid", {}).values():
            try:
                c.itemconfigure(line, fill=ME_LINE_BASE, width=max(1.0, 0.9 * self._zoom))
            except Exception:
                pass

    def toggle_layout(self, mode="free"):
        """显式选择布局，默认恢复自由二维，固定人物坐标始终保留。"""
        self.layout_mode = mode
        self.refresh_network()
        msgs = {"free": "自由布局（默认）：按实际关系排布，拖动可固定人物",
                "radial": "高级布局 · 环形：沿中心周围排布，固定位置保留",
                "smart": "高级布局 · 力导向：按已记录人物关系排布，固定位置保留",
                "ring": "高级布局 · 评分分层：按维护分分环，固定位置保留"}
        self.status_var.set(msgs[self.layout_mode])

    def _draw_links_for(self, pid):
        """悬停展开当前人物的具体语义，保留亲属结构与全部中心线。"""
        self._clear_links(focus_pid=pid)
        if not pid:
            return
        c = self.web_canvas
        # 只把这一根中心线提亮：视线立刻落到"他和我"上，其余线仍是背景。
        focus_line = getattr(self, "_me_line_by_pid", {}).get(pid)
        if focus_line is not None:
            c.itemconfigure(focus_line, fill=ME_LINE_FOCUS,
                            width=max(1.2, 1.8 * self._zoom))
        for st in getattr(self, "_link_style", {}).values():
            rt = st["route"]
            lnk = rt["lnk"]
            mine = pid in (lnk["person_a"], lnk["person_b"])
            if mine:
                bright = _mix(st["col"], "#FFFFFF", 0.28)
                c.itemconfigure(st["line"], fill=bright, width=st["w"] + 1.4)
                c.itemconfigure(st["glow"], fill=_mix(st["col"], BG, 0.52),
                                width=st["w"] + 3.6)
            else:
                c.itemconfigure(st["line"], fill=_mix(st["col"], BG, 0.55),
                                width=max(1.0, st["w"] - 0.4))
                c.itemconfigure(st["glow"], fill=_mix(st["col"], BG, 0.86),
                                width=st["w"] + 2.0)
                continue
        c.tag_raise("relationship_label")

    def _hover(self, pid, on):
        """悬停节点：整卡重渲染（提亮 + 青描边）+ 关系线按需显示（v0.25）"""
        if on:
            self._draw_links_for(pid)
        else:
            self._clear_links()
        info = getattr(self, "_node_items", {}).get(pid)
        if not info:
            return
        state = "src" if self._linking_src == pid else \
            ("hover" if on else "normal")
        if info.get("state") == state:
            return
        info["state"] = state
        img = self._render_node(pid, state)
        if img is None:
            return
        self._card_imgs[pid] = img
        try:
            self.web_canvas.itemconfig(info["item"], image=img)
        except Exception:
            pass

    # ---------- 窗口缩放（防抖重绘） ----------
    def _on_canvas_resize(self, e):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        # 拖拽时：只移动节点位置，不重画任何PIL（毫秒级响应）
        W = self.web_canvas.winfo_width()
        H = self.web_canvas.winfo_height()
        if self._web_size != (W, H):
            self._web_size = (W, H)
            # 画布尺寸变化 → 内容宽高变化，600ms 后的整幅重画需要重新居中
            self._view_inited = False
        if W > 100 and H > 100 and hasattr(self, "node_pos") and self.node_pos:
            # 按比例重算所有节点位置，直接move（不删不重画）
            c = self.web_canvas
            old_center = self._l2s(*self._web_center)
            self._web_center = (W / 2, H / 2)   # 先更新中心，_l2s 才正确
            new_center = self._l2s(*self._web_center)
            c.move("menode", new_center[0] - old_center[0], new_center[1] - old_center[1])
            items = self._web_items()
            for it in items:
                px, py = it.get("pos_x"), it.get("pos_y")
                if px is None or py is None:
                    continue
                if px > 1.5: px, py = px / W, py / H
                pid = it["pid"]
                if pid in self.node_pos:
                    ox, oy = self.node_pos[pid]
                    # 与 _draw_web 一致：比例坐标映射布局系（不随 zoom）
                    nx, ny = px * W, py * H
                    if abs(nx - ox) > 0.5 or abs(ny - oy) > 0.5:
                        # v0.10：布局系 → 画布系换算后直接定位
                        c.coords(f"card_{pid}", *self._l2s(nx, ny))
                        c.coords(f"node_{pid}", *self._l2s(nx, ny))
                    self.node_pos[pid] = (nx, ny)
            self._sync_me_connections()
        # 松手后600ms才完整重画连线
        self._resize_job = self.after(600, self.refresh_network)

    # ---------- 节点拖拽 & 点击 ----------
    def on_press(self, e):
        if self._linking:
            self._linking_click(e)
            return
        c = self.web_canvas
        sx, sy = c.canvasx(e.x), c.canvasy(e.y)  # 视口滚动后坐标转换
        self._drag = None
        pid = self._node_at(sx, sy)
        if pid is None:
            # 空白处左键拖动 = 平移画布（与中键一致，1:1 跟随光标）
            self._panning = {"x": e.x, "y": e.y}
            c.config(cursor="fleur")
            return
        ox, oy = self.node_pos[pid]
        self._drag = {"pid": pid, "press_x": sx, "press_y": sy,
                      "node_x0": ox, "node_y0": oy,
                      "moved": False, "temp_lines": []}
        self._stop_anim()
        c.config(cursor="fleur")
        c.tag_raise(f"card_{pid}")
        c.tag_raise(f"node_{pid}")

    def on_drag(self, e):
        if self._panning is not None and not self._drag:
            # 空白处拖动：平移视口（1:1）
            self._pan_by(e.x - self._panning["x"], e.y - self._panning["y"])
            self._panning["x"] = e.x
            self._panning["y"] = e.y
            return
        if not self._drag:
            return
        if self._drag["moved"]:
            # v0.29：贴近视口边缘 → 画布自动平移（先平移再读坐标，卡片的
            # 世界位置会连带这段位移一起算进去，屏幕上始终贴住光标）
            self._edge_pan(e.x, e.y)
        pid = self._drag["pid"]
        c = self.web_canvas
        sx, sy = c.canvasx(e.x), c.canvasy(e.y)
        z = max(1e-6, getattr(self, "_zoom", 1.0))
        # v0.10：屏幕位移 ÷ zoom = 布局系位移（1:1 跟手，缩放后仍跟手）
        nx = self._drag["node_x0"] + (sx - self._drag["press_x"]) / z
        ny = self._drag["node_y0"] + (sy - self._drag["press_y"]) / z
        # v0.29：取消「空气墙」——旧版把卡片钳在画布 ±50% 的硬边界上，
        # 拖到那儿就停住（用户：「应该整个的范围都是可以移动的位置才行」）。
        # 现在位置不设限，世界随卡片扩张（_grow_world_to）

        if not self._drag["moved"]:
            if abs(sx - self._drag["press_x"]) + abs(sy - self._drag["press_y"]) < 6:
                return  # 死区
            self._begin_drag_lines(pid)
        self._drag["moved"] = True

        # 移动卡片到新位置（绝对定位，不是增量 move）
        old_x, old_y = self.node_pos[pid]
        if abs(nx - old_x) > 0.01 or abs(ny - old_y) > 0.01:
            c.coords(f"card_{pid}", *self._l2s(nx, ny))   # image item 用 coords 直接定位
            self.node_pos[pid] = (nx, ny)
            self._grow_world_to(*self._l2s(nx, ny))
            self._update_temp_lines(pid, nx, ny)
            self._sync_me_connections()
            self._update_circle_regions(pid)

    def _edge_pan(self, ex, ey, margin=44.0, step=24.0):
        """拖卡时鼠标贴近视口边缘 → 画布自动平移，卡片可以一路拖到远处。

        v0.29：以前只能把卡拖到当前视口内，想挪到屏幕外得先松手、再平移
        视口、再重新拖一次。现在按住不放拖到边上画布就跟着走（越靠边越快），
        配合 _pan_by 的世界自动扩张，等于无限画板。"""
        c = self.web_canvas
        vw, vh = c.winfo_width(), c.winfo_height()
        if vw < 40 or vh < 40:
            return
        dx = dy = 0.0
        if ex < margin:
            dx = min(step, (margin - ex) * 0.45)
        elif ex > vw - margin:
            dx = -min(step, (ex - (vw - margin)) * 0.45)
        if ey < margin:
            dy = min(step, (margin - ey) * 0.45)
        elif ey > vh - margin:
            dy = -min(step, (ey - (vh - margin)) * 0.45)
        if dx or dy:
            self._pan_by(dx, dy)

    def _begin_drag_lines(self, pid):
        """拖拽时以真实关系临时线实时跟随，线位于所有人物卡片下面。"""
        c = self.web_canvas
        temps = []
        lw = max(2, 4 * getattr(self, "_zoom", 1.0))
        # 人物间关系临时线（拖拽期间隐藏该人的线，改用从对方卡片到拖拽点
        #    的虚线实时反馈，松手整图重绘重算直线端点）
        pairs = []
        for lid, st in list(getattr(self, "_link_style", {}).items()):
            lnk = st["route"]["lnk"]
            if pid not in (lnk["person_a"], lnk["person_b"]):
                continue
            other = (lnk["person_b"] if pid == lnk["person_a"]
                     else lnk["person_a"])
            op = self.node_pos.get(other)
            if not op:
                continue
            try:
                c.itemconfigure(st["line"], state="hidden")
                c.itemconfigure(st["glow"], state="hidden")
                c.itemconfigure(f"label_{lnk['id']}", state="hidden")
                c.itemconfigure(st["leader"], state="hidden")
            except Exception:
                pass
            l = c.create_line(*self._l2s(*op),
                              *self._l2s(*self.node_pos[pid]),
                              fill=st["col"], width=lw,
                              dash=(6, 3), tags=("dragtmp",),
                              capstyle="round")
            temps.append(l)
            pairs.append(other)
        # 把临时线放在 bg 背景之上、所有卡片之下
        # （bg 现在是上百个 item，逐个 raise 确保压过全部背景层）
        c.tag_lower("dragtmp")
        for bitem in tuple(c.find_withtag("bg")) + tuple(c.find_withtag("circle_region")):
            c.tag_raise("dragtmp", bitem)
        self._drag["temp_lines"] = temps
        self._drag["link_pairs"] = pairs

    def _update_temp_lines(self, pid, nx, ny):
        """拖拽中将每条真实关系线的被拖端更新到布局坐标。"""
        c = self.web_canvas
        snx, sny = self._l2s(nx, ny)
        i = 0
        # 临时线只包含真实关系，顺序与 _begin_drag_lines 的 pairs 一致。
        for other in self._drag.get("link_pairs", []):
            if i >= len(self._drag["temp_lines"]):
                break
            op = self.node_pos.get(other)
            if not op:
                continue
            c.coords(self._drag["temp_lines"][i], *self._l2s(*op), snx, sny)
            i += 1

    def _end_drag_lines(self):
        """拖拽结束：删临时线，恢复该人外环走线（随后整图重绘重算路径）"""
        c = self.web_canvas
        c.delete("dragtmp")
        for st in getattr(self, "_link_style", {}).values():
            try:
                c.itemconfigure(st["line"], state="normal")
                c.itemconfigure(st["glow"], state="normal")
            except Exception:
                pass
        self._set_relationship_focus()

    def on_release(self, e):
        c = self.web_canvas
        c.config(cursor="")
        if self._panning is not None:
            self._panning = None
        if not self._drag:
            return
        pid = self._drag["pid"]
        moved = self._drag["moved"]
        self._drag = None
        if moved:
            self._end_drag_lines()
            # 按比例保存（布局系坐标 ÷ 画布尺寸）。v0.10：布局可超画布，
            # 比例允许 <0 或 >1；读取时 >3 才判定为旧版像素坐标
            W = c.winfo_width() or 1
            H = c.winfo_height() or 1
            nx, ny = self.node_pos[pid]
            db.set_position(pid, nx / W, ny / H)
            name = (db.get_person(pid) or {}).get("name", "")
            self.status_var.set(f"已固定「{name}」的位置")
            # 重绘整个画布，让 PIL 连线按新节点位置重新生成。
            # v0.28：这一次刷新「冻结布局」——卡片位置就是用户刚放下的位置，
            # 不许任何自动调整（去重叠 / 直线避让 / 炸开自愈）再动它一下
            self._freeze_layout = True
            try:
                self.refresh_network()
            finally:
                self._freeze_layout = False
            return
        else:
            # 单击 = 打开资料卡
            self.open_profile(pid)
        self._start_anim()              # 松手后从新位置续跑动画

    def on_right_click(self, e):
        sx, sy = self.web_canvas.canvasx(e.x), self.web_canvas.canvasy(e.y)
        pid = self._node_at(sx, sy)
        if pid is None:
            lid = self._link_at(sx, sy)     # 点在连线上 → 连接菜单
            if lid is not None:
                self._link_menu(e, lid)
            else:
                self.cancel_linking()         # 点空白 = 退出连线模式
            return
        m = tk.Menu(self, tearoff=0, bg=SURFACE2, fg=TEXT,
                    activebackground=CYAN, activeforeground="#06222B",
                    relief="flat", font=FONT_SM)
        # 换形象、记忆、滑杆和重置位置收进资料卡。
        m.add_command(label="打开资料卡", command=lambda: self.open_profile(pid))
        m.add_command(label="记录联系", command=lambda: self.record_contact(pid))
        m.add_command(label="连接到其他人物…", command=lambda: self.start_linking_from(pid))
        m.add_command(label="去送礼", command=lambda: self.go_gift(pid))
        m.add_command(label="生成祝福", command=lambda: self.go_greet(pid))
        m.add_separator()
        m.add_command(label="删除该人物", command=lambda: self.confirm_delete(pid))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    # ---------- 节点快捷操作 ----------
    def change_avatar(self, pid):
        p = db.get_person(pid)
        if not p:
            return
        path = filedialog.askopenfilename(
            title=f"选择「{p['name']}」的形象图片",
            filetypes=[("图片文件", "*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp")])
        if not path:
            return
        try:
            saved = images_util.save_avatar(path, pid)
        except Exception as ex:
            self._error("更换形象失败", f"图片处理出错：{ex}")
            return
        if saved:
            db.set_avatar(pid, saved)
            self.status_var.set(f"已更新「{p['name']}」的形象")
            self.refresh_network()
            self.refresh_cards()

    def reset_node_pos(self, pid):
        db.clear_position(pid)
        # v0.28：清库后必须让布局签名失效——否则「复用现有布局」会拿
        # node_pos 里的旧坐标直接放回去，看起来像重置没生效
        self._layout_sig = None
        self.refresh_network()
        self.status_var.set("该节点已回到自动布局")

    # ---------- 连线模式 ----------
