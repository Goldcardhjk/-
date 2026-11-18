"""进步壹号 Agent —— 本地登录页

进入 Agent 前先登录自己的账号（本地单人工具：设定"我是谁"）。
数据全部存本机 D 盘 SQLite，不做真实注册/密码体系。
"""

import tkinter as tk
from tkinter import filedialog, messagebox

import db
import images_util

# 与主界面一致的深色主题（此处独立定义，避免循环导入）
BG = "#0B0F17"
SURFACE = "#131A26"
SURFACE2 = "#1B2434"
SURFACE3 = "#243044"
BORDER = "#273349"
TEXT = "#E6EDF3"
MUTED = "#8A97A8"
CYAN = "#22D3EE"
GREEN = "#3FB950"

from ui_base import UI_FONT     # v0.70：跨平台字体（Win 雅黑 / mac 苹方）

FONT = (UI_FONT, 10)
FONT_SM = (UI_FONT, 9)
FONT_MID = (UI_FONT, 12, "bold")
FONT_LG = (UI_FONT, 15, "bold")

USER_AVATAR_PID = 0        # "我"的头像存为 avatar_0.png，与人物头像体系复用


def _round_pts(x1, y1, x2, y2, r):
    return [x1 + r, y1,  x2 - r, y1,  x2, y1,  x2, y1 + r,
            x2, y2 - r,  x2, y2,  x2 - r, y2,  x1 + r, y2,
            x1, y2,  x1, y2 - r,  x1, y1 + r,  x1, y1]


def _btn(parent, text, cmd, kind="normal", icon=None):
    pal = {"normal": (SURFACE2, TEXT, BORDER, "#31435E"),
           "primary": (CYAN, "#06222B", CYAN, "#8FF3FF"),
           "ghost": (SURFACE, MUTED, BORDER, SURFACE3)}
    bgc, fgc, bdc, act = pal.get(kind, pal["normal"])
    ic = None
    if icon:                          # v0.13：透明图标替代 emoji
        ic = images_util.ui_asset_scaled(icon, 18, 18)
    b = tk.Button(parent, text=text, command=cmd, bg=bgc, fg=fgc,
                  activebackground=act, activeforeground=fgc,
                  relief="flat", bd=0, highlightthickness=1,
                  highlightbackground=bdc, font=FONT_SM, cursor="hand2",
                  padx=14, pady=6,
                  image=ic, compound="left" if ic is not None else None)
    b._ic = ic                        # 保持引用，防 GC
    b.bind("<Enter>", lambda e: b.configure(bg=act))
    b.bind("<Leave>", lambda e: b.configure(bg=bgc))
    return b


def run_login(accounts=None):
    """弹出登录窗，返回账号 dict(name/avatar/account) 或 None（关闭=退出）"""
    accounts = list(accounts or [])
    picked = {"slug": accounts[0]["slug"] if accounts else None}
    root = tk.Tk()
    root.title("进步壹号 Agent · 登录")
    root.configure(bg=BG)
    root.resizable(False, False)
    W, H = 480, 500
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{W}x{H}+{max(0, (sw - W) // 2)}+{max(0, (sh - H) // 2)}")

    result = {}
    avatar_path = [None]

    # 登录页 hero 背景（ComfyUI 生成的夜空关系网络，v0.11；缺失则纯色）
    hero = images_util.ui_asset("login_hero")
    if hero is not None:
        bg_lbl = tk.Label(root, image=hero, bg=BG, bd=0)
        bg_lbl.place(x=0, y=0, width=W, height=H)
        # v0.12：粒子层叠加（同宽，只覆盖上部 250px）
        parts = images_util.ui_asset("hero_particles")
        if parts is not None:
            p_lbl = tk.Label(root, image=parts, bg=BG, bd=0)
            p_lbl._parts_img = parts       # 保持引用，防 GC
            p_lbl.place(x=0, y=0, width=W, height=250)
        # v0.59 修：原先用 place(relx=0.5, y=182) 把「知识宝石」吊坠浮在
        # 窗口正中，120×60 的辉光正好盖住副标题两行——文字被吃得看不清。
        # 登录页文字优先，装饰让位：不再叠加该浮层（素材仍留在 images/ui/）。
        # v0.12：两朵星尘粒子（左上 / 右上，错落有呼吸感）
        sp = images_util.ui_asset("deco_sparkle", sub=2)
        if sp is not None:
            for rx, ry in ((0.07, 0.14), (0.93, 0.26)):
                s_lbl = tk.Label(root, image=sp, bg=BG, bd=0)
                s_lbl._sp_img = sp         # 保持引用，防 GC
                s_lbl.place(relx=rx, rely=ry, anchor="center")
    app_icon = images_util.ui_asset("app_icon")
    if app_icon is not None:
        try:
            root.iconphoto(True, app_icon)
        except Exception:
            pass

    # 顶部品牌条：极光带（v0.12 透明渐变；缺失回退 3px 青线）
    glow_bar = images_util.ui_asset("top_glow_bar")
    if glow_bar is not None:
        gb_lbl = tk.Label(root, image=glow_bar, bg=BG, bd=0)
        gb_lbl._glow_img = glow_bar       # 保持引用，防 GC
        gb_lbl.pack(fill="x")
    else:
        tk.Frame(root, bg=CYAN, height=3).pack(fill="x")
    # 品牌标题（v0.12：标题前加 ComfyUI 小图标，与主窗顶栏一致）
    brand = tk.Frame(root, bg=BG)
    brand.pack(pady=(36, 4))
    ticon = images_util.ui_asset("title_icon")
    if ticon is not None:
        tl = tk.Label(brand, image=ticon, bg=BG, bd=0)
        tl._ticon = ticon                # 保持引用，防 GC
        tl.pack(side="left", padx=(0, 6))
    tk.Label(brand, text="◈ 进步壹号 Agent", bg=BG, fg=CYAN,
             font=(UI_FONT, 22, "bold")).pack(side="left")
    # v0.59：这两行原先用 MUTED 灰压在星空最亮处，本身就看不清；
    # 去掉浮层宝石后改近白青色，让副标题在背景上立起来。
    tk.Label(root, text="人际关系网络 · 社交价值 · 智能送礼", bg=BG, fg="#CFEFFA",
             font=FONT_SM).pack(pady=(6, 0))
    tk.Label(root, text="登录后开始管理你的关系网络", bg=BG, fg="#8FB9C8",
             font=FONT_SM).pack(pady=(3, 26))

    # 圆角卡片（Canvas 画底，内嵌内容）
    card_cv = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0, height=250)
    card_cv.pack(fill="x", padx=40)
    card = tk.Frame(card_cv, bg=SURFACE)
    wid = card_cv.create_window((1, 1), window=card, anchor="nw")

    def _sync(_e=None):
        w = card_cv.winfo_width()
        if w < 8:
            return
        h = card.winfo_reqheight() + 2
        card_cv.configure(height=h)
        card_cv.itemconfigure(wid, width=w - 2)
        card_cv.delete("cardbg")
        card_cv.create_polygon(_round_pts(1, 1, w - 2, h - 2, 14),
                               smooth=True, splinesteps=24, fill=SURFACE,
                               outline=BORDER, width=1, tags=("cardbg",))
        card_cv.tag_lower("cardbg")

    card.bind("<Configure>", _sync)
    card_cv.bind("<Configure>", _sync)

    # 头像（可选）
    av_box = tk.Frame(card, bg=SURFACE)
    av_box.pack(pady=(24, 6))
    av_cv = tk.Canvas(av_box, width=88, height=88, bg=SURFACE,
                      highlightthickness=0, bd=0)
    av_cv.pack()
    av_img = [None]

    def draw_avatar():
        av_cv.delete("all")
        # 头像微光环
        av_cv.create_oval(4, 4, 84, 84, outline="#17414E", width=1)
        av_cv.create_oval(8, 8, 80, 80, outline=CYAN, width=2)
        ph = images_util.load_circle_photo(avatar_path[0], (72, 72))
        if ph:
            av_img[0] = ph
            av_cv.create_image(44, 44, image=ph)
        else:
            av_cv.create_oval(12, 12, 76, 76, fill="#0E3A44", outline="")
            av_cv.create_text(44, 44, text="我", fill=CYAN, font=FONT_LG)

    draw_avatar()

    def pick_avatar():
        p = filedialog.askopenfilename(
            title="选择我的头像",
            filetypes=[("图片文件", "*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp")])
        if not p:
            return
        try:
            avatar_path[0] = images_util.save_avatar(p, USER_AVATAR_PID)
            draw_avatar()
        except Exception as ex:
            messagebox.showerror("头像处理失败", str(ex))

    _btn(av_box, "选择头像（可选）", pick_avatar, "ghost",
         icon="icon_avatar").pack(pady=(8, 0))

    # 昵称
    nf = tk.Frame(card, bg=SURFACE)
    nf.pack(fill="x", padx=30, pady=(18, 6))
    tk.Label(nf, text="我的昵称", bg=SURFACE, fg=MUTED, font=FONT_SM,
             anchor="w").pack(fill="x")
    ent = tk.Entry(nf, bg=SURFACE2, fg=TEXT, insertbackground=CYAN,
                   relief="flat", highlightthickness=1, highlightbackground=BORDER,
                   highlightcolor=CYAN, font=(UI_FONT, 12))
    ent.insert(0, "我")
    ent.pack(fill="x", pady=(4, 0), ipady=6)

    tk.Label(card, text="本地账号 · 数据仅存本机（D 盘 SQLite），不上传",
             bg=SURFACE, fg=MUTED, font=FONT_SM, anchor="w").pack(
        fill="x", padx=30, pady=(12, 22))

    # 已有账号：每人一份库，人物与任务记忆互不可见（发布方案要展示的隔离）。
    acc_row = tk.Frame(root, bg=BG)
    acc_row.pack(fill="x", padx=40, pady=(12, 0))
    if accounts:
        head = tk.Frame(acc_row, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="选择账号 · 各自的人物与记忆互不可见",
                 bg=BG, fg=MUTED, font=FONT_SM, anchor="w").pack(side="left")
        _btn(head, "新建账号", lambda: new_account(), "ghost").pack(side="right")
        chips = tk.Frame(acc_row, bg=BG)
        chips.pack(fill="x", pady=(6, 0))

        def render_chips():
            for w in chips.winfo_children():
                w.destroy()
            for acc in accounts[:6]:
                _btn(chips, acc["name"],
                     lambda slug=acc["slug"]: pick(slug),
                     "primary" if acc["slug"] == picked["slug"] else "ghost").pack(
                    side="left", padx=(0, 8))

        def pick(slug):
            picked["slug"] = slug
            acc = next(a for a in accounts if a["slug"] == slug)
            ent.delete(0, "end")
            ent.insert(0, acc["name"])
            render_chips()

        render_chips()

    def new_account():
        picked["slug"] = None
        ent.delete(0, "end")
        ent.insert(0, "")
        ent.focus_set()

    def on_enter():
        name = ent.get().strip() or "我"
        result["user"] = {"name": name, "avatar": avatar_path[0],
                          "account": picked["slug"]}
        root.destroy()

    bf = tk.Frame(root, bg=BG)
    bf.pack(pady=20)
    _btn(bf, "进入 Agent  →", on_enter, "primary").pack()
    ent.bind("<Return>", lambda e: on_enter())
    ent.focus_set()
    ent.select_range(0, tk.END)

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    # 有账号列表时内容更高：按实际需要撑开，避免账号行被窗口裁掉。
    root.update_idletasks()
    need_h = root.winfo_reqheight()
    if need_h > H:
        root.geometry(f"{W}x{need_h}+{max(0, (sw - W) // 2)}+{max(0, (sh - need_h) // 2)}")
    root.mainloop()
    return result.get("user")


if __name__ == "__main__":
    db.init_db()
    u = run_login()
    print("登录结果:", u)
