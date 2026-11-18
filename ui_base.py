"""进步壹号 Agent —— 桌面 GUI (tkinter 深色科技风)

首页：🌐 关系网络（节点可拖拽，左键单击开资料卡，右键出菜单；🔗 可在人物间连关系线）
布局：默认按实际人物关系自由二维排列；其他布局在「更多 → 高级布局」选择。
右侧任务栏：⏰近期提醒 + 📊网络概览 + 🏆维护优先级
其他页：🃏人脉卡片 / 🎁智能送礼 / ✨祝福语 / 🟢绿泡泡连接器
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import json
import math
import os
import random
import queue
import sys
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

# ---------- 主题 ----------
BG = "#F3F6FA"
SURFACE = "#FFFFFF"
SURFACE2 = "#F0F4FA"
SURFACE3 = "#E5EDF8"
BORDER = "#DCE4EF"
TEXT = "#18263C"
MUTED = "#63738A"
CYAN = "#2563EB"
GREEN = "#15805D"
RED = "#CE4258"
YELLOW = "#9A6B0C"
ORANGE = "#B96A18"
PURPLE = "#7755BC"

TIER_COLOR = {"核心": RED, "重要": ORANGE, "普通": CYAN, "浅层": MUTED}
RING_OF = {"核心": 0, "重要": 1, "普通": 2, "浅层": 3}
NODE_W, NODE_H = 154, 80          # 关系网络节点卡片尺寸（画布像素）

# v0.61：启动提醒弹窗开关。默认关——冒烟/验收直接实例化 App 时绝不弹窗；
# main.py 真实启动时置 True（录制脚本也可显式打开）。
STARTUP_POPUP = False

# 人物间连接关系：关系 -> (语义色, 原虚线样式)
# v0.27 起关系线一律是直线段，颜色/粗细统一由关系分 score 驱动（与图例一致）；
# 此表保留关系语义色，供标签/图例等场景备用，不再驱动描边样式。
LINK_STYLE = {
    "引荐": (PURPLE, (5, 3)),
    "家人": (RED, ()),
    "挚友": (ORANGE, ()),
    "合作伙伴": (CYAN, ()),
    "合作": (CYAN, ()),
    "同事": (GREEN, ()),
    "认识": (MUTED, (4, 3)),
}


class PageStack:
    """轻量页面栈（v0.16 替身 ttk.Notebook，仅保留 select/add/bind 接口）

    背景：ttk.Notebook 原生页签条在 clam 主题下压不掉——style layout
    覆盖本 Tk 版本无效（layout 查询恒为 client），文字/内距/边框全部
    清零后仍保底 ~29px，且页签是直角、不支持 per-tab 图标。页签切换
    已由自绘圆角胶囊栏（_btn kind=tab/tab_on）接管，这里只做页面
    堆叠：所有页 place 铺满 host，选中页 tkraise 置顶。
    """

    def __init__(self, master, bg=BG):
        self.host = tk.Frame(master, bg=bg)
        self._pages = []                       # [(page, text)] 建栈序
        self._cur = None
        self._cbs = []                         # [(seq, fn)] 事件订阅

    def add(self, page, text=""):
        self._pages.append((page, text))
        page.place(in_=self.host, x=0, y=0, relwidth=1.0, relheight=1.0)
        if self._cur is None:
            self._cur = page
            page.tkraise()                   # 首页置顶（防映射后末建页压顶）
        return page

    def select(self, page=None):
        if page is None:
            return self._cur
        for p, _ in self._pages:
            if str(p) == str(page):
                if p is not self._cur:
                    self._cur = p
                    p.tkraise()
                    self._fire()
                return
        # 传入未登记页：静默忽略

    def _fire(self):
        for seq, fn in self._cbs:
            if seq == "<<NotebookTabChanged>>":
                try:
                    fn(None)                   # 兼容事件签名 lambda e: ...
                except TypeError:
                    fn()

    def bind(self, seq, fn, add=None):
        self._cbs.append((seq, fn))


def _score_color(score):
    """关系分 → 连线颜色：0 灰 → 50 青 → 75 橙 → 100 红（平滑插值，随分数连续变化）"""
    stops = ((0, (138, 151, 168)), (50, (34, 211, 238)),
             (75, (240, 136, 62)), (100, (248, 81, 73)))
    try:
        s = float(score)
    except Exception:
        s = 50.0
    s = max(0.0, min(100.0, s))
    for i in range(len(stops) - 1):
        s0, c0 = stops[i]
        s1, c1 = stops[i + 1]
        if s <= s1:
            t = (s - s0) / (s1 - s0)
            return "#%02X%02X%02X" % tuple(
                round(c0[j] + (c1[j] - c0[j]) * t) for j in range(3))
    return "#F85149"


# v0.60：v0.55 起家人的身份/亲属/圈子信息以「｜身份：…｜亲属元数据 …｜圈子元数据:[…]」
# 的形式写在 notes 里，单独看数据没问题，但直接摊在卡片和资料卡上就是一堆内部标记。
# 展示层统一剥掉，只留人话；保存时若用户没改，仍写回原始字符串，元数据不丢。
NOTE_INTERNAL_MARKS = ("｜圈子元数据:", "｜亲属元数据", "｜身份：")


def clean_note(text):
    s = str(text or "")
    cut = len(s)
    for m in NOTE_INTERNAL_MARKS:
        i = s.find(m)
        if i != -1:
            cut = min(cut, i)
    return s[:cut].strip()


def _mix(c1, c2, t):
    """两 hex 色线性混合：t=0 → c1，t=1 → c2（chip 底色/悬停高亮预混算，
    绕开 canvas 不支持的 alpha 填充）"""
    a = c1.lstrip("#"); b = c2.lstrip("#")
    rgb = tuple(round(int(a[i:i + 2], 16) * (1 - t) + int(b[i:i + 2], 16) * t)
                for i in (0, 2, 4))
    return "#%02X%02X%02X" % rgb


# 中心线是「我→每个人」的展示层联系（不是事实关系）：31 人就是 31 条，
# 一旦画实就会盖过真正的亲属线。默认压到接近底色，只留方向感；
# 悬停某人时把那一根提亮，让「他和我」的关系读得清。v0.57
ME_LINE_BASE = _mix(CYAN, BG, 0.84)
ME_LINE_FOCUS = _mix(CYAN, BG, 0.32)


def _wrap_by_width(text, font, max_w):
    """按像素宽度折行（中文逐字断行），返回行列表

    弹窗要「一次算准尺寸、不靠二次 update_idletasks 试布局」，所以先把
    文字自己折好（Label 的 wraplength 只换行、量不出高度）。
    """
    out = []
    for para in str(text).split("\n"):
        cur = ""
        for ch in para:
            if cur and font.measure(cur + ch) > max_w:
                out.append(cur)
                cur = ch
            else:
                cur += ch
        out.append(cur)
    return out or [""]


# ---------- UI 字体：按平台解析一次 ----------
# v0.70：全工程原本硬编码 "Microsoft YaHei"，在 macOS 上这个名字不存在，
# Tk 会静默退到 Courier 之类的默认字体，整个界面字形全废。这里按平台挑
# 一个真实存在的中文 UI 字体，其余代码继续用 UI_FONT 常量即可。
#   Windows → 微软雅黑；macOS → 苹方（10.11+ 系统自带）；Linux → 文泉驿/Noto
def _resolve_ui_font():
    prefer = {
        "darwin": ["PingFang SC", "Heiti SC", "STHeiti", "Hiragino Sans GB",
                   "Arial Unicode MS", "Helvetica"],
        "win32": ["Microsoft YaHei", "SimHei", "SimSun"],
    }.get(sys.platform, ["Noto Sans CJK SC", "WenQuanYi Micro Hei",
                         "Source Han Sans SC", "DejaVu Sans"])
    try:
        from tkinter import font as _tkfont
        # families() 需要一个已存在的 Tk 根窗口；模块导入期通常还没有，
        # 失败就直接用候选表第一项（各平台系统自带，基本不会缺）。
        available = set(_tkfont.families())
        for name in prefer:
            if name in available:
                return name
    except Exception:
        pass
    return prefer[0]


UI_FONT = _resolve_ui_font()

FONT = (UI_FONT, 10)
FONT_SM = (UI_FONT, 9)
FONT_XS = (UI_FONT, 8)
FONT_MID = (UI_FONT, 12, "bold")
FONT_LG = (UI_FONT, 15, "bold")
FONT_BOLD = (UI_FONT, 10, "bold")
FONT_NUM = (UI_FONT, 16, "bold")


def _f(v, default=0.5):
    try:
        return float(v)
    except Exception:
        return default


def _split(s):
    return [x.strip() for x in s.replace("，", ",").replace("、", ",").split(",") if x.strip()]


def _parse_imp(s):
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part or "：" in part:
            part = part.replace("：", ":")
            lab, dt = part.split(":", 1)
            out.append({"label": lab.strip(), "date": dt.strip()})
        else:
            out.append({"label": part, "date": ""})
    return json.dumps(out, ensure_ascii=False)


def _rounded(c, x1, y1, x2, y2, r=10, **kw):
    """在 canvas 上画平滑圆角矩形，返回 item id"""
    pts = [
        x1 + r, y1,  x2 - r, y1,  x2, y1,  x2, y1 + r,
        x2, y2 - r,  x2, y2,  x2 - r, y2,  x1 + r, y2,
        x1, y2,  x1, y2 - r,  x1, y1 + r,  x1, y1,
    ]
    return c.create_polygon(pts, smooth=True, splinesteps=24, **kw)


def _round_pts(x1, y1, x2, y2, r):
    """圆角矩形顶点序列（供 create_polygon smooth 使用）"""
    return [
        x1 + r, y1,  x2 - r, y1,  x2, y1,  x2, y1 + r,
        x2, y2 - r,  x2, y2,  x2 - r, y2,  x1 + r, y2,
        x1, y2,  x1, y2 - r,  x1, y1 + r,  x1, y1,
    ]


def _pick_icon(*names):
    """按优先级返回第一个存在的 UI 资产名（v0.20 标题图标 fallback 链）

    ComfyUI 定制资产未生成/缺失时自动回落到程序化图标，全缺失返回 None
    （调用方 _label 的 icon=None 即不显示图标）。
    """
    for n in names:
        if n and images_util._ui_path(n + ".png"):
            return n
    return None


def rounded_card(parent, pad=10, bg=SURFACE, border=BORDER, radius=12,
                 parent_bg=BG, auto_height=True):
    """自适应高度的圆角卡片：外层 Canvas 画圆角底，内嵌内容 Frame。

    返回 (canvas, inner_frame)。canvas 用 pack(fill="x") 跟随父宽；
    高度随内容自动伸缩（auto_height=False 时跟随 pack 拉伸）。
    内容变化 / 父宽变化自动重绘。
    """
    cv = tk.Canvas(parent, bg=parent_bg, highlightthickness=0, bd=0, height=40)
    inner = tk.Frame(cv, bg=bg)
    wid = cv.create_window((pad, pad), window=inner, anchor="nw")

    def _sync(_e=None):
        w = cv.winfo_width() or cv.winfo_reqwidth()
        if w < 8:
            return
        cv.itemconfigure(wid, width=w - 2 * pad)
        h = (inner.winfo_reqheight() + 2 * pad) if auto_height else cv.winfo_height()
        h = max(36, h)
        if auto_height:
            cv.configure(height=h)
        cv.delete("cardbg")
        img = images_util.smooth_card(w - 2, h - 2, radius, bg, border,
                                      texture=images_util.glass_overlay_pil())
        if img is not None:
            cv._card_img = img          # 保持引用，防 PhotoImage 被 GC
            cv.create_image(w / 2, h / 2, image=img, tags=("cardbg",))
        else:                            # PIL 不可用时的退化路径
            cv.create_polygon(_round_pts(1, 1, w - 2, h - 2, radius),
                              smooth=True, splinesteps=24, fill=bg,
                              outline=border, width=1, tags=("cardbg",))
        cv.tag_lower("cardbg")

    inner.bind("<Configure>", _sync)
    cv.bind("<Configure>", _sync)
    return cv, inner


def row_card(parent, height=30, on_draw=None, bg=SURFACE2, border=BORDER,
             radius=9, cursor=None):
    """定高圆角行：跟随父宽自动重绘；on_draw(cv, w) 画内容（tag=rowcontent）。

    返回 canvas；可直接 bind 点击事件。"""
    cv = tk.Canvas(parent, height=height, bg=SURFACE, highlightthickness=0, bd=0,
                   cursor=cursor or "")
    _state = {"w": 0}

    def _draw(_e=None):
        w = cv.winfo_width()
        if w < 10 or w == _state["w"]:
            return
        _state["w"] = w
        cv.delete("rowbg", "rowcontent")
        img = images_util.smooth_card(w - 2, height - 2, radius, bg, border)
        if img is not None:
            cv._row_img = img
            cv.create_image(w / 2, height / 2, image=img, tags=("rowbg",))
        else:
            cv.create_polygon(_round_pts(1, 1, w - 2, height - 2, radius),
                              smooth=True, splinesteps=20, fill=bg,
                              outline=border, width=1, tags=("rowbg",))
        cv.tag_lower("rowbg")
        if on_draw:
            on_draw(cv, w)

    cv.bind("<Configure>", _draw)
    return cv


def stat_tile(parent, value, label, color, width=132, height=62, icon=None):
    """极简统计块（v0.28）：只有数字 + 名称，不再有图标 / 底纹 / 描边

    用户反馈（2026-10-28）：「这个也重新设计 不行就不要加那些灰色的条条，
    可以通过调整位置来实现功能的」。

    旧版每格都塞了图标蒙版 + 玻璃纹理 + 1px 描边，缩到 130px 宽以后这些东西
    糊成一条灰边（截图里四格右侧那条「凸出来的灰条」就是它）。现在装饰全去掉，
    语义只由数字颜色承担，层级靠排版表达：数字大（20px 粗体、语义色）在上，
    名称小（9px、灰）在下，四格共用同一套基线，靠留白自然分块。
    """
    cv = tk.Canvas(parent, width=width, height=height, bg=SURFACE,
                   highlightthickness=0, bd=0)
    cv.create_text(3, 4, text=str(value), fill=color, anchor="nw",
                   font=(UI_FONT, 20, "bold"))
    cv.create_text(4, height - 7, text=label, fill=MUTED, anchor="sw",
                   font=FONT_SM)
    return cv


