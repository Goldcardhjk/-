# -*- coding: utf-8 -*-
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
import cloud_client

class App(SidebarMixin, NetPageMixin, CardsPageMixin, GiftPageMixin, GreetPageMixin, ConnPageMixin, DialogsMixin, tk.Tk):
    # ---------- v0.27：关系线＝直线（布局负责避让，线不绕行） ----------
    LINK_CLEAR = 12.0                # 直线到卡片的最小净空（力导向/分层布局）
    CHANNEL = 26.0                   # 辐射布局：线到卡片的通道宽度（v0.33）
                                     # 12px 在 0.5 倍缩放下只有 6 屏幕像素，
                                     # 看上去就是「线压着卡边」——用户 2026-10-28
                                     # 的截图里那些擦边线正是 12px 净空。26px
                                     # 缩放后约 13 屏幕像素，肉眼是清晰的空隙。
    ME_R = 58.0                      # 圆心「我」障碍半径
    CH_R_LO = 0.94                   # 辐射布局：卡片最多内收 6%（v0.34，兼容保留）
    CH_R_HI = 1.14                   # 辐射布局：卡片最多外凸 14%
    # ── v0.35 二维自由布局（relax_free）─────────────────────────────────
    # 用户 2026-10-28：「不用让所有人物卡都在一个圆上，都在一个圆上就没法
    # 解决线的干涉问题」。v0.34 只让卡沿自己的射线滑（±14%），那还是同一个
    # 圆，只是半径毛糙——一张卡缩到 0.86R，它的辐射线仍占据那个角度直到
    # 0.86R，外圈照样插不进长弦。现在径向给到 −54%/+58%、切向给到 ±0.6 个
    # 卡位：卡可以整张缩进里层，把外圈那一环整个让给关系线穿过去。
    FREE_CLEAR = 46.0                # 线到卡片的净空（26px 在 0.5 倍缩放下
                                     # 只有 13 屏幕像素，用户仍觉得「贴着」）
    FREE_R_LO = 0.46                 # 最多缩到内层（相对本环半径）
    FREE_R_HI = 1.58                 # 最多推到外层
    FREE_DA = 0.60                   # 切向最多挪 0.6 个卡位（约 12°）
    FREE_STEP_R = 22.0               # 粗扫径向步长
    FREE_STEP_A = 0.036              # 粗扫切向步长（rad）
    FREE_SWEEPS = 8                  # 收敛轮数（实测 18 人 8 轮即稳定）

    # 旧脚本/测试直接访问这些控件时，等价于先打开对应页签，避免懒加载
    # 改变既有自动化契约；正常用户则统一走 _on_tab_changed。
    _LAZY_ATTR_PAGES = {
        "cards_frame": "tab_cards", "cv": "tab_cards",
        "g_person": "tab_gift", "gift_hint": "tab_gift", "order_hint": "tab_gift",
        "gr_person": "tab_greet", "gr_text": "tab_greet",
        "conn_canvas": "tab_conn", "conn_grid": "tab_conn",
    }

    def __init__(self, bootstrap_accounts=False):
        super().__init__()
        self.title("进步壹号 Agent v0.69 · 关系网络 · 云端积分 · MCP 智能体")
        self.geometry("1600x920")
        self.minsize(1200, 760)
        self.configure(bg=BG)
        # 纯 Tk 加载页不生成 PIL 资产；先上屏，再做数据库和首页构建。
        self._build_startup_screen()
        self._style()
        self._startup_progress("正在准备本地数据…", 18)
        db.init_db()
        # 只有 main.py 的真实入口执行账号登记；自动化常直接 App()，
        # 其隔离库/账号目录是运行时替换的，不能在这里反向登记账号。
        if bootstrap_accounts:
            db.bootstrap_accounts()
        self._startup_progress("正在读取账号与场景…", 38)
        self._init_state()
        # v0.66：账号选择做进主窗——不再弹独立登录窗，选完也不跳转新窗口。
        # 未登录/本机多账号时先铺一层账号入口覆盖层，选完原地建主界面。
        if self._need_account_gate():
            self._startup_progress("正在打开账号入口…", 82)
            self.build_account_gate()
            self._close_startup_screen()
        else:
            self._startup_progress("正在构建关系首页…", 68)
            self._enter_main()

    # ---------- 启动加载页 / 首页优先 ----------
    def _build_startup_screen(self):
        """轻量启动反馈：只使用 Tk 原生控件，构造期间也能更新真实阶段。"""
        self._startup = tk.Frame(self, bg=BG)
        self._startup.place(x=0, y=0, relwidth=1, relheight=1)
        box = tk.Frame(self._startup, bg=BG)
        box.place(relx=0.5, rely=0.46, anchor="center")
        self._startup_canvas = tk.Canvas(box, width=76, height=76, bg=BG,
                                         highlightthickness=0, bd=0)
        self._startup_canvas.pack(pady=(0, 16))
        self._startup_canvas.create_oval(12, 12, 64, 64, outline=BORDER, width=4)
        self._startup_arc = self._startup_canvas.create_arc(
            12, 12, 64, 64, start=90, extent=72, style="arc",
            outline=CYAN, width=4)
        tk.Label(box, text="进步壹号 Agent", bg=BG, fg=TEXT,
                 font=(UI_FONT, 20, "bold")).pack()
        tk.Label(box, text="关系经营工作台 · v0.69", bg=BG, fg=MUTED,
                 font=FONT_SM).pack(pady=(5, 22))
        self._startup_text = tk.StringVar(value="正在启动…")
        tk.Label(box, textvariable=self._startup_text, bg=BG, fg=CYAN,
                 font=FONT).pack()
        track = tk.Frame(box, bg=SURFACE2, width=280, height=4)
        track.pack(pady=(16, 0))
        track.pack_propagate(False)
        self._startup_bar = tk.Frame(track, bg=CYAN, width=1, height=4)
        self._startup_bar.place(x=0, y=0, height=4)
        self._startup_angle = 90
        self._startup_job = None
        self._startup_track = track
        self._animate_startup()
        # 让加载页在 App 构造函数返回前就已映射，而不是等全部页面建完。
        self.update()

    def _animate_startup(self):
        startup = getattr(self, "_startup", None)
        if startup is None:
            return
        try:
            self._startup_angle = (self._startup_angle - 18) % 360
            self._startup_canvas.itemconfigure(self._startup_arc,
                                                start=self._startup_angle)
            self._startup_job = self.after(80, self._animate_startup)
        except Exception:
            self._startup_job = None

    def _startup_progress(self, text, percent):
        """更新真实初始化阶段；每次让出事件循环，加载页不会只是启动后贴图。"""
        startup = getattr(self, "_startup", None)
        if startup is None:
            return
        try:
            self._startup_text.set(text)
            width = max(1, round(280 * max(0, min(100, percent)) / 100))
            self._startup_bar.configure(width=width)
            self.update()
        except Exception:
            pass

    def _close_startup_screen(self):
        job = getattr(self, "_startup_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._startup_job = None
        startup = getattr(self, "_startup", None)
        if startup is not None:
            try:
                startup.destroy()
            except Exception:
                pass
        self._startup = None

    def __getattr__(self, name):
        """兼容旧代码直接访问延迟页控件；仅对明确登记的控件触发构建。"""
        tab_attr = self._LAZY_ATTR_PAGES.get(name)
        tab = self.__dict__.get(tab_attr) if tab_attr else None
        pending = self.__dict__.get("_lazy_pages") or {}
        if tab is not None and str(tab) in pending:
            self._ensure_page(tab)
            if name in self.__dict__:
                return self.__dict__[name]
        raise AttributeError("%s has no attribute %r" % (type(self).__name__, name))

    # ---------- 状态（换账号时可重跑） ----------
    def _init_state(self):
        self.person_map = {}
        self.gift_reco = []
        self.sandbox = []
        self.selected_pid = None
        self.node_imgs = {}
        self._card_imgs = []
        self.rank_ids = []
        self.node_pos = {}       # pid -> (x, y) 画布坐标
        self.edges = {}          # pid -> 关联的引荐弦 item id 列表
        self._drag = None        # 拖拽状态
        self._freeze_layout = False  # v0.28：True 时本次刷新不动任何卡片位置
        self._hover_pid = None   # 当前悬停节点
        self._node_colors = {}   # pid -> 层级色（hover 恢复用）
        self.layout_mode = "free"  # 默认自由二维布局；其余布局供手动切换
        self._linking = False        # 是否处于连线模式
        self._linking_src = None     # 连线模式起点 pid
        # v0.63：使用场景卡。「全部」看整张网；切到具体场景只显示该场景的人，
        # 与「账号」（谁在用）是两层——场景卡做在账号内部，切换不退出登录。
        # 场景选择按账号持久化：上次看到哪个场景，这次打开直接回到那里。
        self.me = db.get_user() or {"name": "我", "avatar": None}
        self._scene = (self.me.get("scene") or "全部")
        if self._scene not in ([db.BUILTIN_SCENE] + db.list_scenes()):
            self._scene = "全部"
        self._scene_btns = {}        # 场景卡按钮引用，用于高亮当前场景
        self.status_var = tk.StringVar(value="v0.69 · 本地/云端双模式 · 服务端模型计费 · MCP 可被智能体调用")
        # v0.5：本地账号（"我"）+ 动态连线动画状态
        self.logged_out = False
        self._anim_on = False         # 连线微动画开关（默认关：v0.22 用户明确要求
        # 「没有操作的时候保持静态，不要随机乱动」——节点不再正弦漂移；
        # 想开动态走 ⚙更多→动效：开（toggle_anim 开启时才启动动画））
        self._anim_job = None
        self._anim_t = 0.0
        self._anim_base = {}         # pid -> 动画锚点（拖拽后更新）
        self._link_items = []        # (item, pill, pid_a, pid_b, img?, img?) 动画跟随用
        self._me_items = []          # (line_id, pid) 「我」→人物 扩散线
        self._web_center = (0, 0)

    # ---------- v0.66：主窗内账号入口 ----------
    def _need_account_gate(self):
        """启动先亮账号入口层。

        v0.66 用户定案：账号选择要在主窗里，但「每次启动先选账号」这件事
        保留——所以只要登记过账号就先亮入口层，不再因为它只有一个账号而跳过。
        一个账号都没有时（还没登记账号体系、或工具链的隔离测试库）不拦，
        退回单档模式直接进主界面——几十个 App() 都靠这条。
        """
        try:
            accounts = db.list_accounts()
        except Exception:
            return False
        return bool(accounts)

    @staticmethod
    def _account_meta(slug):
        """只读探一下该账号有多少人/场景（不切库，避免副作用）。"""
        meta = {"persons": 0, "scenes": 0}
        try:
            import sqlite3
            path = db.account_db_path(slug)
            if not os.path.exists(path):
                return meta
            conn = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                                   uri=True)
            try:
                meta["persons"] = conn.execute(
                    "SELECT COUNT(*) FROM persons").fetchone()[0]
                meta["scenes"] = conn.execute(
                    "SELECT COUNT(*) FROM scenes").fetchone()[0]
            finally:
                conn.close()
        except Exception:
            pass
        return meta

    def build_account_gate(self):
        """主窗内的账号入口：铺满整窗，选完原地进主界面（不换窗、不跳转）。"""
        gate = tk.Frame(self, bg=BG)
        gate.place(x=0, y=0, relwidth=1, relheight=1)
        self._gate = gate

        head = tk.Frame(gate, bg=BG)
        head.pack(fill="x", padx=30, pady=(26, 0))
        tk.Label(head, text="◈ 进步壹号 Agent", bg=BG, fg=CYAN,
                 font=(UI_FONT, 20, "bold")).pack(side="left")
        tk.Label(head, text="v0.69 · 人际关系网络 & 场景卡", bg=BG, fg=MUTED,
                 font=FONT_SM).pack(side="left", padx=(12, 0), pady=(7, 0))
        tk.Label(head, text="● 本地账号 · 云端服务可选", bg=BG, fg=GREEN,
                 font=FONT_SM).pack(side="right", pady=(7, 0))

        body = tk.Frame(gate, bg=BG)
        body.pack(expand=True)
        card_cv, card = rounded_card(body, pad=26, radius=16, parent_bg=BG)
        card_cv.pack()
        card_cv.configure(width=1010)

        tk.Label(card, text="选择账号进入", bg=SURFACE, fg=TEXT,
                 font=(UI_FONT, 18, "bold")).pack(anchor="w")
        tk.Label(card,
                 text="每个人物、每一段记忆，都跟着账号走——工作号归工作号，生活号归生活号。",
                 bg=SURFACE, fg=MUTED, font=FONT_SM).pack(anchor="w", pady=(4, 0))

        row = tk.Frame(card, bg=SURFACE)
        row.pack(fill="x", pady=(18, 0))
        for acc in db.list_accounts()[:4]:
            self._gate_tile(row, acc)
        self._gate_new_tile(row)
        newrow = tk.Frame(card, bg=SURFACE)
        tk.Label(newrow, text="新账号昵称：", bg=SURFACE, fg=TEXT,
                 font=FONT).pack(side="left")
        ent = tk.Entry(newrow, bg=SURFACE2, fg=TEXT, insertbackground=CYAN,
                       relief="flat", highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=CYAN,
                       font=FONT, width=18)
        ent.pack(side="left", padx=(4, 8), ipady=4)
        ent.insert(0, "新账号")

        def _create():
            self.enter_account(None, ent.get().strip() or "新账号")

        self._btn(newrow, "创建并进入", _create, "primary").pack(side="left")
        ent.bind("<Return>", lambda e: _create())
        self._gate_newrow = newrow

        foot = tk.Frame(card, bg=SURFACE)
        foot.pack(fill="x", pady=(18, 0))
        tk.Label(foot,
                 text="账号之间互不可见：一个账号一个 SQLite 库，切换不丢数据。",
                 bg=SURFACE, fg=MUTED, font=FONT_SM).pack(side="left")

        tk.Label(gate, text="账号选择就在主窗里完成，选完直接进入，不再单开窗口。",
                 bg=BG, fg=MUTED, font=FONT_XS).pack(side="bottom", pady=(0, 18))

    def _gate_tile(self, parent, acc):
        """账号卡片：头像 + 昵称 + 人物数，点一下进入。"""
        meta = self._account_meta(acc["slug"])
        tile = tk.Frame(parent, bg=SURFACE2, highlightthickness=1,
                        highlightbackground=BORDER, cursor="hand2")
        tile._acc = acc          # 供自动化/测试定位（入口层卡片不是 Button）
        tile.pack(side="left", expand=True, fill="x", padx=(0, 12))
        inner = tk.Frame(tile, bg=SURFACE2)
        inner.pack(padx=20, pady=18)
        top = tk.Frame(inner, bg=SURFACE2)
        top.pack(anchor="w")

        av = images_util.load_circle_photo(acc.get("avatar"), (52, 52))
        if av:
            lb = tk.Label(top, image=av, bg=SURFACE2)
            lb._av = av                       # 保持引用，防 GC
            lb.pack(side="left")
        else:
            canvas = tk.Canvas(top, width=52, height=52, bg=SURFACE2,
                               highlightthickness=0, bd=0)
            canvas.create_oval(2, 2, 50, 50, fill=SURFACE3, outline=BORDER)
            canvas.create_text(26, 26, text="我", fill=CYAN, font=FONT_MID)
            canvas.pack(side="left")
        nm = tk.Frame(top, bg=SURFACE2)
        nm.pack(side="left", padx=(12, 0))
        tk.Label(nm, text=acc["name"], bg=SURFACE2, fg=TEXT,
                 font=FONT_MID).pack(anchor="w")
        tk.Label(nm, text="%d 位人物 · %d 个场景"
                 % (meta["persons"], meta["scenes"]),
                 bg=SURFACE2, fg=MUTED, font=FONT_XS).pack(anchor="w")

        def _enter(_e=None):
            self.enter_account(acc["slug"], acc["name"], acc.get("avatar"))

        def _paint(bg):
            def walk(w):
                try:
                    w.configure(bg=bg)
                except Exception:
                    pass
                for c in w.winfo_children():
                    if c.winfo_class() != "Canvas":
                        walk(c)
            walk(tile)

        for w in self._walk_widgets(tile):
            w.bind("<Button-1>", _enter)
        tile.bind("<Enter>", lambda e: _paint(SURFACE3))
        tile.bind("<Leave>", lambda e: _paint(SURFACE2))
        return tile

    @staticmethod
    def _walk_widgets(w, out=None):
        out = [] if out is None else out
        out.append(w)
        for c in w.winfo_children():
            App._walk_widgets(c, out)
        return out

    def _gate_new_tile(self, parent):
        tile = tk.Frame(parent, bg=SURFACE, highlightthickness=1,
                        highlightbackground=BORDER, cursor="hand2")
        tile.pack(side="left", expand=True, fill="x")
        inner = tk.Frame(tile, bg=SURFACE)
        inner.pack(padx=18, pady=14)
        tk.Label(inner, text="＋", bg=SURFACE, fg=CYAN,
                 font=(UI_FONT, 16, "bold")).pack(anchor="w")
        tk.Label(inner, text="新建账号", bg=SURFACE, fg=TEXT,
                 font=FONT_MID).pack(anchor="w")
        tk.Label(inner, text="另开一份独立的人物与记忆", bg=SURFACE, fg=MUTED,
                 font=FONT_XS).pack(anchor="w")

        def _click(_e=None):
            self._gate_newrow.pack(fill="x", pady=(14, 0))
            for ch in self._gate_newrow.winfo_children():
                if ch.winfo_class() == "Entry":
                    ch.focus_set()
                    ch.select_range(0, tk.END)

        for w in self._walk_widgets(tile):
            w.bind("<Button-1>", _click)
        return tile

    def enter_account(self, slug=None, name=None, avatar=None):
        """主窗内切账号：切库 → 拆入口层 → 原地重建主界面。"""
        if slug:
            db.switch_account(slug)
        else:
            db.switch_account(db.register_account(name or "我", avatar)["slug"])
        if not db.get_user():
            db.save_user(name or "我", avatar)
        self._teardown_gate()
        self._init_state()
        self._enter_main()

    def _teardown_gate(self):
        gate = getattr(self, "_gate", None)
        if gate is not None:
            try:
                gate.destroy()
            except Exception:
                pass
            self._gate = None

    def _enter_main(self):
        self._build_ui()
        if STARTUP_POPUP:
            self.after(900, self.popup_due_reminders)

    def _teardown_ui(self):
        """清空主界面（保留窗口本身），供退出登录后原地回到账号入口。"""
        self._stop_anim()
        for w in list(self.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        # 把这一轮界面新建的属性一并丢掉：否则再进账号时 hasattr 会命中
        # 已销毁的控件（refresh_combos 读旧下拉就报 invalid command name）。
        base = getattr(self, "_ui_attr0", None)
        if base is not None:
            for k in list(vars(self).keys()):
                if k not in base:
                    try:
                        delattr(self, k)
                    except Exception:
                        pass

    def _build_ui(self):
        # 记住建界面前的属性集合，退登时按差集清理（见 _teardown_ui）。
        self._ui_attr0 = set(vars(self).keys())
        # ---------- 顶栏 ----------
        hdr = tk.Frame(self, bg=BG); hdr.pack(fill="x")
        ticon = None  # 浅色工作台使用简洁文字品牌，避免旧霓虹资产混入
        if ticon is not None:
            self._title_icon = ticon        # 保持引用，防 GC
            tk.Label(hdr, image=ticon, bg=BG).pack(side="left", padx=(16, 4),
                                                   pady=10)
        title_img = images_util.smooth_grad_text("进步壹号", 20,
                                                 TEXT, TEXT, True)
        if title_img is not None:
            self._title_img = title_img      # 保持引用，防 GC
            tk.Label(hdr, image=title_img, bg=BG).pack(side="left",
                                                       padx=(0 if ticon else 16, 16),
                                                       pady=10)
        else:
            tk.Label(hdr, text="◈ 进步壹号 Agent", bg=BG, fg=CYAN,
                     font=(UI_FONT, 17, "bold")).pack(side="left",
                                                                padx=16, pady=10)
        tk.Label(hdr, text="v0.69 · 关系经营 · 云端积分 · Vibe Pay · MCP", bg=BG, fg=MUTED,
                 font=FONT_SM).pack(side="left", pady=10)
        # 账号区：头像 + 昵称 + 退出登录
        acc = tk.Frame(hdr, bg=SURFACE, highlightthickness=1,
                       highlightbackground=BORDER, bd=0)
        acc.pack(side="right", padx=(8, 0), pady=8)
        me_av = images_util.load_circle_photo(self.me.get("avatar"), (24, 24))
        if me_av:
            self._hdr_img = me_av
            tk.Label(acc, image=me_av, bg=SURFACE).pack(side="left", padx=(8, 4), pady=4)
        acc_ic = images_util.ui_asset_scaled("icon_avatar", 18, 18)
        if acc_ic is not None:
            self._acc_ic = acc_ic          # 保持引用，防 GC
            tk.Label(acc, image=acc_ic, bg=SURFACE, bd=0).pack(side="left",
                                                               padx=(8, 3), pady=4)
        tk.Label(acc, text=self.me.get("name", "我"), bg=SURFACE, fg=TEXT,
                 font=FONT_SM).pack(side="left", pady=4)
        self._btn(acc, "退出登录", self.logout, "ghost").pack(side="left", padx=(4, 8), pady=3)
        chip = tk.Label(hdr, text="● 本地模拟运行", bg=SURFACE, fg=GREEN,
                        font=FONT_SM, padx=10, pady=3, highlightthickness=1,
                        highlightbackground=BORDER)
        chip.pack(side="right", padx=16)
        # v0.69：积分入口常驻页头——余额一眼可见，点开即充值/查单/支付设置
        self._points_btn = self._btn(hdr, "积分 %d" % db.points_balance(),
                                     self.open_points_dialog, "ghost",
                                     icon="icon_star")
        self._points_btn.pack(side="right", padx=(8, 0), pady=8)
        self.refresh_points_chip()
        # 顶栏极光带（ComfyUI/PIL 透明渐变，v0.12；缺失回退 2px 青线）
        glow_bar = None
        if glow_bar is not None:
            self._glow_bar = glow_bar        # 保持引用，防 GC
            tk.Label(self, image=glow_bar, bg=BG, bd=0).pack(fill="x")
        else:
            tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=20, pady=(2, 10))

        # ---------- 标签页 ----------
        # v0.16：PageStack 替身 Notebook——原生页签条在 clam 下压不掉
        # （style layout 覆盖无效 + 保底 29px，见 PageStack 类注释）
        self.nb = PageStack(self)
        self.tab_net = ttk.Frame(self.nb.host)
        self.tab_cards = ttk.Frame(self.nb.host)
        self.tab_gift = ttk.Frame(self.nb.host)
        self.tab_greet = ttk.Frame(self.nb.host)
        self.tab_conn = ttk.Frame(self.nb.host)
        self.nb.add(self.tab_net, text="关系网络")
        self.nb.add(self.tab_cards, text="人脉卡片")
        self.nb.add(self.tab_gift, text="智能送礼")
        self.nb.add(self.tab_greet, text="祝福语")
        self.nb.add(self.tab_conn, text="绿泡泡连接器")

        # v0.16：自绘圆角胶囊页签栏——ttk.Notebook 页签是直角且不支持
        # per-tab 图标（v0.13 遗留问题），原生页签条已由 PageStack 替掉，
        # 立体胶囊按钮接管切换
        tcv, tabbar = rounded_card(self, pad=6, radius=12, parent_bg=BG)
        tcv.pack(fill="x", padx=16, pady=(0, 12))
        self._tab_btns = []
        for name, tab, ic in (("关系网络", self.tab_net, "icon_net"),
                              ("人脉卡片", self.tab_cards, "icon_cards"),
                              ("智能送礼", self.tab_gift, "icon_gift"),
                              ("祝福语", self.tab_greet, "icon_star"),
                              ("绿泡泡连接器", self.tab_conn, "icon_conn")):
            b = self._btn(tabbar, name,
                          lambda t=tab: self._select_tab(t), "tab",
                          icon=ic, radius=15)
            b._tabname = name               # 页签名（_sync_tabs 重烘用）
            b.pack(side="left", padx=4)
            self._tab_btns.append((b, tab))
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._sync_tabs()
        self.nb.host.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.nb.host.update_idletasks()      # host 映射后栈序才最终生效
        self.tab_net.tkraise()

        tk.Label(self, textvariable=self.status_var, bg=SURFACE, fg=GREEN,
                 anchor="w", font=FONT_SM, padx=10).pack(side="bottom", fill="x")
        # 全局快捷键：ESC 退出连线模式
        self.bind("<Escape>", lambda e: self.cancel_linking())

        # ---------- 首页优先：其余页首次选择时再构建 ----------
        self.build_network()
        self._lazy_pages = {
            str(self.tab_cards): ("人脉卡片", self.build_cards),
            str(self.tab_gift): ("智能送礼", self.build_gifting),
            str(self.tab_greet): ("祝福语", self.build_greetings),
            str(self.tab_conn): ("绿泡泡连接器", self.build_connector),
        }
        self._lazy_building = None
        for tab, (name, _builder) in ((self.tab_cards, self._lazy_pages[str(self.tab_cards)]),
                                      (self.tab_gift, self._lazy_pages[str(self.tab_gift)]),
                                      (self.tab_greet, self._lazy_pages[str(self.tab_greet)]),
                                      (self.tab_conn, self._lazy_pages[str(self.tab_conn)])):
            self._make_lazy_placeholder(tab, name)
        self.refresh_combos()

        # 默认打开关系网络首页
        self.nb.select(self.tab_net)
        self._close_startup_screen()

        # 窗口/任务栏图标（ComfyUI 生成的 App 图标，v0.11）
        app_icon = images_util.ui_asset("app_icon")
        if app_icon is not None:
            self._app_icon = app_icon        # 保持引用，防 GC
            try:
                self.iconphoto(True, app_icon)
            except Exception:
                pass

    # ---------- 延迟页构建 ----------
    def _make_lazy_placeholder(self, tab, name):
        holder = tk.Frame(tab, bg=BG)
        holder.place(relx=0.5, rely=0.45, anchor="center")
        tk.Label(holder, text=name, bg=BG, fg=TEXT,
                 font=(UI_FONT, 16, "bold")).pack()
        label = tk.Label(holder, text="首次打开时加载，首页无需等待",
                         bg=BG, fg=MUTED, font=FONT_SM)
        label.pack(pady=(8, 0))
        holder._loading_label = label
        tab._lazy_placeholder = holder

    def _ensure_page(self, tab):
        """构建尚未初始化的页签；重复选择、跨页跳转均只构建一次。"""
        key = str(tab)
        pending = getattr(self, "_lazy_pages", None) or {}
        entry = pending.get(key)
        if entry is None or getattr(self, "_lazy_building", None) == key:
            return
        name, builder = entry
        self._lazy_building = key
        old_status = self.status_var.get()
        placeholder = getattr(tab, "_lazy_placeholder", None)
        try:
            self.status_var.set("正在加载「%s」…" % name)
            if placeholder is not None:
                placeholder._loading_label.configure(text="正在加载，请稍候…")
            # 当前页先显示占位，再进入重资产构建；隐藏页的兼容访问不抢焦点。
            if self.nb.select() is tab:
                self.update_idletasks()
            builder()
            # 先标记完成再刷新组合框，避免刷新链路反向触发同一页。
            self._lazy_pages.pop(key, None)
            if placeholder is not None:
                try:
                    placeholder.destroy()
                except Exception:
                    pass
                tab._lazy_placeholder = None
            self.refresh_combos()
            self.status_var.set(old_status)
        except Exception:
            self.status_var.set("「%s」加载失败，请切换页签后重试" % name)
            raise
        finally:
            self._lazy_building = None

    def _on_tab_changed(self, _event=None):
        self._sync_tabs()
        current = self.nb.select()
        if current is not None:
            self._ensure_page(current)

    # ---------- 样式 ----------
    def _select_tab(self, tab):
        """胶囊页签点击：切换页面栈 + 同步高亮态"""
        self.nb.select(tab)
        self._sync_tabs()

    def _sync_tabs(self):
        """同步胶囊页签高亮：选中页 tab_on（青描边青字），其余 tab"""
        try:
            cur = str(self.nb.select())
        except Exception:
            return
        for b, tab in getattr(self, "_tab_btns", []):
            on = (str(tab) == cur)
            b._kind = "tab_on" if on else "tab"
            self._btn_face(b, getattr(b, "_tabname", ""))

    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=BG, foreground=TEXT)
        st.configure("TFrame", background=BG)
        st.configure("Treeview", background=SURFACE, foreground=TEXT, fieldbackground=SURFACE,
                     borderwidth=0, rowheight=26, font=FONT)
        st.configure("Treeview.Heading", background=SURFACE2, foreground=CYAN,
                     font=FONT_SM, relief="flat")
        st.map("Treeview", background=[("selected", "#E5EEFF")], foreground=[("selected", "#1D4ED8")])
        st.configure("Vertical.TScrollbar", background=SURFACE2, troughcolor=BG,
                     bordercolor=BG, arrowcolor=CYAN)
        st.configure("Dark.TCombobox", fieldbackground=SURFACE2, background=SURFACE2,
                     foreground=TEXT, bordercolor=BORDER, arrowcolor=CYAN,
                     insertcolor=TEXT, font=FONT_SM, padding=4)
        st.map("Dark.TCombobox", fieldbackground=[("readonly", SURFACE2)],
               foreground=[("readonly", TEXT)],
               bordercolor=[("focus", CYAN)],
               # v0.16：不配 background map 时 clam 会把下拉箭头按钮画成
               # 浅色「白方框」（用户截图反馈根因）；arrowcolor 同步压暗
               background=[("!disabled", SURFACE2), ("active", SURFACE3),
                           ("pressed", SURFACE3)],
               arrowcolor=[("!disabled", CYAN), ("active", "#1D4ED8")])
        st.configure("Dark.TSpinbox", fieldbackground=SURFACE2, background=SURFACE2,
                     foreground=TEXT, bordercolor=BORDER, arrowcolor=CYAN,
                     insertcolor=TEXT, font=FONT_SM, arrowsize=13)
        st.map("Dark.TSpinbox", bordercolor=[("focus", CYAN)],
               background=[("!disabled", SURFACE2), ("active", SURFACE3),
                           ("pressed", SURFACE3)],
               arrowcolor=[("!disabled", CYAN), ("active", "#1D4ED8")])
        st.configure("Horizontal.TScale", background=SURFACE, troughcolor=SURFACE2,
                     borderwidth=0)
        st.map("Horizontal.TScale", background=[("active", CYAN)])

    # ---------- 通用控件 ----------
    def _entry(self, parent, width=20, font=None):
        return tk.Entry(parent, width=width, bg=SURFACE2, fg=TEXT, insertbackground=CYAN,
                        relief="flat", highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=CYAN, font=font or FONT)

    def _btn(self, parent, text, cmd, kind="normal", icon=None,
             radius=None):
        """立体三态按钮（v0.16）

        tk.Button 只支持一张图 → 面图（images_util.btn_face）把
        「竖向渐变 + 顶部高光 + 底部暗边 + 圆角 + 图标 + 文字」全部烘入；
        hover 整体提亮、press 渐变反转（内凹），换图即三态。
        radius 传 h//2 得全圆胶囊（页签用）；文字动态变化用 _btn_text。
        """
        ic = images_util.ui_asset_scaled(icon, 18, 18) if icon else None
        b = tk.Button(parent, command=cmd, bg=SURFACE2,
                      activebackground=SURFACE2, activeforeground=TEXT,
                      relief="flat", bd=0, highlightthickness=1,
                      highlightbackground=SURFACE2, highlightcolor=CYAN,
                      font=FONT_SM, cursor="hand2", padx=0, pady=0,
                      takefocus=1)
        b._kind = kind
        b._ic = ic                       # 保持引用，防 GC
        b._radius = radius
        b._faces = None
        b.bind("<Enter>",
               lambda e: b._faces and b.configure(image=b._faces[1]))
        b.bind("<Leave>",
               lambda e: b._faces and b.configure(image=b._faces[0]))
        b.bind("<ButtonPress-1>",
               lambda e: b._faces and b.configure(image=b._faces[2]))
        b.bind("<ButtonRelease-1>",
               lambda e: b._faces and b.configure(image=b._faces[1]))
        self._btn_face(b, text or "")
        return b

    def _btn_face(self, b, text):
        """按文字重算三态面图（kind/图标/文字一起烘入）"""
        kind = getattr(b, "_kind", "normal")
        ic = getattr(b, "_ic", None)
        radius = getattr(b, "_radius", None)
        tw = images_util.text_width(text, 9)
        iw = (18 + 6) if ic is not None else 0
        w = int(tw + iw + (26 if text else 16))
        h = 36 if kind in ("tab", "tab_on") else 32
        faces = (images_util.btn_face(kind, "normal", w, h, text, ic,
                                      radius=radius),
                 images_util.btn_face(kind, "hover", w, h, text, ic,
                                      radius=radius),
                 images_util.btn_face(kind, "press", w, h, text, ic,
                                      radius=radius))
        if faces[0] is None:             # PIL 不可用 → 退化平色按钮
            b.configure(text=text, image="", bg=SURFACE2, fg=TEXT,
                        font=FONT_SM, padx=12, pady=5)
            b._faces = None
            return
        b.configure(image=faces[0], text="")
        b._faces = faces                 # 持引用防 GC

    def _btn_text(self, b, text):
        """动态换按钮文字：重烘三态面图（替代 config(text=)）"""
        self._btn_face(b, text or "")

    def refresh_points_chip(self):
        """刷新页头「积分 N」按钮（付款到账、领体验分、换账号后调用）。"""
        btn = getattr(self, "_points_btn", None)
        if btn is None:
            return
        try:
            if not btn.winfo_exists():
                return
            text = ("云端积分 %s" % cloud_client.cached_balance() if cloud_client.is_active()
                    else "积分 %d" % db.points_balance())
            self._btn_text(btn, text)
        except Exception:
            pass                              # 退登/销毁过程中控件可能已失效

    # ---------- 深色模态对话框（v0.28：无边框自绘，去掉系统白标题栏） ----------
    def _label(self, parent, text, fg=TEXT, font=None, bg=BG, icon=None, **kw):
        lb = tk.Label(parent, text=text, bg=bg, fg=fg, font=font or FONT, **kw)
        if icon:                         # v0.13：透明图标替代 emoji
            ic = images_util.ui_asset_scaled(icon, 20, 20)
            if ic is not None:
                lb._ic = ic              # 保持引用，防 GC
                lb.configure(image=ic, compound="left", padx=4)
        return lb

    def logout(self):
        """退出登录：原地回到账号入口（同一窗口，不关窗也不跳转）。"""
        if not self._ask("退出登录",
                         "将退出当前账号并回到账号入口，本机数据保留。继续？"):
            return
        db.clear_user()
        self._teardown_ui()
        self._init_state()
        self.build_account_gate()

    def destroy(self):
        self._stop_anim()
        try:
            images_util._gc_cache(force=True)   # v0.21：root 销毁前清死条目
        except Exception:
            pass
        super().destroy()

    # ---------- 绘制关系网络 ----------
    def _build_more_menu(self):
        """构建「更多」下拉菜单（不弹出；供 open_more_menu 与冒烟测试复用）"""
        m = tk.Menu(self, tearoff=0, bg=SURFACE2, fg=TEXT,
                    activebackground=CYAN, activeforeground="#06222B",
                    relief="flat", font=FONT_SM)
        m.add_command(label="自由布局（默认）" + (" · 当前" if self.layout_mode == "free" else ""),
                      command=lambda: self.toggle_layout("free"))
        advanced = tk.Menu(m, tearoff=0, bg=SURFACE2, fg=TEXT,
                           activebackground=CYAN, font=FONT_SM)
        for mode, label in (("smart", "力导向布局"), ("radial", "环形布局（旧模式）"),
                            ("ring", "按维护分分层（旧模式）")):
            advanced.add_command(
                label=label + (" · 当前" if self.layout_mode == mode else ""),
                command=lambda selected=mode: self.toggle_layout(selected))
        m.add_cascade(label="高级布局", menu=advanced)
        m.add_command(label="重新自动布局（清除固定位置）", command=self.reset_layout)
        m.add_command(label="视图回到中心", command=self.recenter_view)
        m.add_separator()
        m.add_command(label="刷新网络", command=self.refresh_network)
        m.add_command(label="云端账号与 AI…", command=self.open_cloud_account_dialog)
        m.add_command(label="Vibe Pay 支付配置…", command=self.open_pay_config)
        m.add_command(label="积分充值…", command=self.open_points_dialog)
        m.add_command(label="备份当前账号数据…", command=self.backup_data)
        m.add_command(label="从备份恢复当前账号…", command=self.restore_data)
        m.add_command(label="去绿泡泡拉取好友", command=lambda: self.nb.select(self.tab_conn))
        m.add_command(label="人脉卡片管理", command=lambda: self.nb.select(self.tab_cards))
        m.add_separator()
        m.add_command(label=("动效：关" if self._anim_on else "动效：开"),
                      command=self.toggle_anim)
        m.add_command(label=("背景：关" if self._bg_on else "背景：开"),
                      command=self.toggle_bg)
        # 图标按索引贴（v0.62 校准新增项使所有索引右移）
        icons = {0: "icon_net", 3: "icon_refresh", 5: "icon_refresh",
                 6: "ai_avatar", 7: "icon_vibepay", 8: "icon_star",
                 9: "icon_save", 10: "icon_import", 11: "icon_conn",
                 12: "icon_cards", 14: "icon_star", 15: "icon_bg"}
        imgs = []
        for idx, name in icons.items():
            ic = images_util.ui_asset_scaled(name, 18, 18)
            if ic is not None:
                try:
                    m.entryconfigure(idx, image=ic, compound="left")
                    imgs.append(ic)
                except Exception:
                    pass
        self._menu_imgs = imgs            # 保持引用，防 GC
        return m

    def open_more_menu(self):
        """更多：高级操作下拉菜单（保持首页只有 3 个主按钮）"""
        m = self._build_more_menu()
        try:
            x = self.more_btn.winfo_rootx()
            y = self.more_btn.winfo_rooty() + self.more_btn.winfo_height()
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    # ---------- 数据备份 / 恢复（v0.61） ----------
    @staticmethod
    def _alive(w):
        """控件是否还活着。v0.66：退登后旧界面已销毁，属性还在但 Tcl 命令没了，
        直接 get() 会抛 invalid command name —— 重建界面时一律先探活。"""
        try:
            return w is not None and bool(w.winfo_exists())
        except Exception:
            return False

    def _fill_combo(self, attr, names):
        # vars() 只读已构建控件；不要用 getattr 触发兼容懒加载，否则首页
        # 刷新组合框时会把送礼/祝福语页又提前建出来。
        cb = vars(self).get(attr)
        if not self._alive(cb):
            return
        try:
            cur = cb.get()
        except tk.TclError:
            return
        cb["values"] = names
        if cur in self.person_map:
            cb.set(cur)

    def refresh_combos(self):
        self.person_map = {p["name"]: p["id"] for p in db.list_persons()}
        names = list(self.person_map.keys())
        self._fill_combo("g_person", names)
        self._fill_combo("gr_person", names)
        # v0.17：群发 chips 跟随人物增减（祝福语页未建则跳过）
        chips = getattr(self, "_gr_chip_host", None)
        if self._alive(chips):
            self._gr_rebuild_chips()
            # v0.18：分段祝福模型的成本提示跟随全局模型变化
            if hasattr(self, "_gr_tier_cbs"):
                for t in greetings.TIER_ORDER:
                    self._gr_update_tier_cost(t)
        # v0.5：快速补关系的双下拉
        if self._alive(getattr(self, "ql_a", None)):
            self.ql_a["values"] = names
        if self._alive(getattr(self, "ql_b", None)):
            self.ql_b["values"] = names

    def go_gift(self, pid):
        p = db.get_person(pid)
        self.nb.select(self.tab_gift)
        self.g_person.set(p["name"])
        self.on_gift_person()

    def go_greet(self, pid):
        p = db.get_person(pid)
        self.nb.select(self.tab_greet)
        self.gr_person.set(p["name"])
        if not self.gr_holiday.get():
            self.gr_holiday.set("生日")
        self.status_var.set(f"已切换到「{p['name']}」，点击生成专属祝福")
