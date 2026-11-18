"""形象图片(avatar)工具

- save_avatar: 把用户选择的图片处理后存到 data/avatars/avatar_<pid>.png，返回路径
- load_photo: 读取并缩略成 PhotoImage，供 tkinter 控件/画布使用
优先用 Pillow(PIL) 以支持 jpg/png/gif 与缩略图；若环境无 PIL 则退化为
tkinter 原生 PhotoImage（仅 png/gif），保证程序不因缺依赖而崩。
"""

import os
import sys
import shutil
import weakref
from functools import lru_cache

import db

try:
    from PIL import Image, ImageTk, ImageDraw, ImageFont, ImageChops, ImageFilter
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False
    Image = None
    ImageTk = None
    ImageDraw = None
    ImageFont = None
    ImageChops = None
    ImageFilter = None

try:
    _RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    _RESAMPLE = Image.LANCZOS

import tkinter as tk  # 退化路径用

AVATAR_DIR = os.path.join(db.DATA_DIR, "avatars")

# ---------------- 超采样抗锯齿渲染 ----------------
# tkinter canvas 的形状（圆角/胶囊/描边）是硬边锯齿；用 4 倍画布绘制后
# LANCZOS 缩回目标尺寸，得到真正平滑的边缘。文字也走 PIL 渲染，
# 比 Tk 原生 canvas 文字更厚实清晰。

_SS = 4  # 超采样倍率

# v0.70：PIL 侧字体也要跨平台。原本只有 C:/Windows/Fonts 三条，在 macOS 上
# 全部 miss 之后退到 ImageFont.load_default()——那是个位图字体，画不出中文，
# 人物卡/胶囊标签会变成一排方块。按平台给出真实存在的候选。
def _font_candidates():
    if sys.platform == "darwin":
        return [
            # 苹方：10.11+ 系统自带，.ttc 里含多字重，PIL 默认取第一个
            ("/System/Library/Fonts/PingFang.ttc",
             "/System/Library/Fonts/PingFang.ttc"),
            ("/System/Library/Fonts/STHeiti Light.ttc",
             "/System/Library/Fonts/STHeiti Medium.ttc"),
            ("/Library/Fonts/Arial Unicode.ttf",
             "/Library/Fonts/Arial Unicode.ttf"),
            ("/System/Library/Fonts/Supplemental/Arial.ttf",
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ]
    if sys.platform.startswith("linux"):
        return [
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
            ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
             "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ]
    return [
        ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
        ("C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simhei.ttf"),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ]


_FONT_CANDIDATES = _font_candidates()


@lru_cache(maxsize=64)
def _pil_font(size, bold=False):
    """按像素字号取 PIL 字体（只读系统字体文件，失败退默认字体）"""
    if not _HAS_PIL:
        return None
    for reg, bld in _FONT_CANDIDATES:
        path = bld if bold else reg
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _ss_canvas(w, h):
    """4 倍透明画布 + 画笔（内容坐标系仍按 1x 思考，乘 _SS）"""
    img = Image.new("RGBA", (max(4, int(w * _SS)), max(4, int(h * _SS))),
                    (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _ss_done_pil(img, w, h):
    """缩回目标尺寸（返回 PIL Image，供缓存管线使用）"""
    return img.resize((max(1, int(w)), max(1, int(h))), _RESAMPLE)


def _ss_done(img, w, h):
    """缩回目标尺寸并转 PhotoImage"""
    return ImageTk.PhotoImage(_ss_done_pil(img, w, h))


def _hex2rgb(s, default=(255, 255, 255)):
    s = (s or "").lstrip("#")
    if len(s) == 6:
        try:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            pass
    return default


def smooth_dot(color, d=9):
    """平滑小圆点（图例/状态点）→ PhotoImage"""
    if not _HAS_PIL:
        return None
    try:
        def _render():
            img, dr = _ss_canvas(d, d)
            dr.ellipse([0, 0, d * _SS - 1, d * _SS - 1], fill=color)
            return _ss_done_pil(img, d, d)
        return _cached_photo(("dot", color, d, _SS), _render)
    except Exception:
        return None


def smooth_text(text, size, color, bold=False):
    """抗锯齿文字（透明底）→ PhotoImage；PIL 不可用时返回 None
    size 是目标显示像素大小（自动乘超采样倍率）"""
    if not _HAS_PIL or not text:
        return None

    def _render():
        font = _pil_font(max(6, int(size * _SS)), bold)
        if font is None:
            return None
        img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        l, t, r, b = d.textbbox((0, 0), text, font=font)
        tw, th = r - l, b - t
        img = Image.new("RGBA", (max(4, tw + 8), max(4, th + 8)),
                        (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.text((4 - l, 4 - t), text, font=font, fill=color)
        return _ss_done_pil(img, (tw + 8) // _SS, (th + 8) // _SS)

    try:
        return _cached_photo(("txt", text, size, color, bold, _SS), _render)
    except Exception:
        return None


def smooth_card(w, h, r=12, fill="#1B2434",
                outline="#273349", ow=1, shadow=False, texture=None):
    """平滑圆角矩形底 → PhotoImage（可选右下柔和投影 / 蒙版纹理）

    texture：PIL RGBA 小图（如 glass_overlay 星纹蒙版）——在卡片底色上
    以圆角区域为蒙版平铺叠加（v0.12 玻璃拟态：卡片蒙一层半透明星纹）。
    """
    if not _HAS_PIL:
        return None
    try:
        def _render():
            pad_x, pad_y = (10, 12) if shadow else (2, 2)
            img, d = _ss_canvas(w + 2 * pad_x, h + 2 * pad_y)
            ox, oy = pad_x, pad_y
            if shadow:
                for off, alpha in ((5, 90), (10, 40)):
                    d.rounded_rectangle(
                        [(ox + off * 0.4) * _SS, (oy + off * 0.8) * _SS,
                         (ox + w + off * 0.4) * _SS, (oy + h + off * 0.8) * _SS],
                        radius=r * _SS, fill=(6, 10, 18, alpha))
            d.rounded_rectangle([ox * _SS, oy * _SS, (ox + w) * _SS, (oy + h) * _SS],
                                radius=r * _SS, fill=fill,
                                outline=outline, width=max(1, int(ow * _SS)))
            base = _ss_done_pil(img, w + 2 * pad_x, h + 2 * pad_y)
            if texture is not None:
                # 纹理平铺 + 圆角区域蒙版裁剪 → 只透出卡片内的星光
                W2, H2 = base.size
                tiled = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
                tw, th = texture.size
                for tx in range(0, W2, tw):
                    for ty in range(0, H2, th):
                        tiled.alpha_composite(texture, (tx, ty))
                m = Image.new("L", (W2, H2), 0)
                ImageDraw.Draw(m).rounded_rectangle(
                    [ox * _SS, oy * _SS, (ox + w) * _SS, (oy + h) * _SS],
                    radius=r * _SS, fill=255)
                tiled.putalpha(ImageChops.multiply(tiled.getchannel("A"), m))
                base.alpha_composite(tiled)
                # 纹理可能盖细边 → 补描一次圆角边
                ImageDraw.Draw(base).rounded_rectangle(
                    [ox * _SS, oy * _SS, (ox + w) * _SS, (oy + h) * _SS],
                    radius=r * _SS, outline=outline,
                    width=max(1, int(ow * _SS)))
            return base
        return _cached_photo(("cardbg", w, h, r, fill, outline, ow, shadow,
                              texture is not None, _SS), _render)
    except Exception:
        return None


def smooth_pill(text, fg, fill="#F0F4FA", outline=None, size=9):
    """平滑胶囊标签（圆角底 + 抗锯齿文字）→ PhotoImage"""
    if not _HAS_PIL or not text:
        return None

    def _render():
        # v0.18 修复：超采样画布上文字必须同倍放大（字号乘 _SS）。
        # 旧实现以 1x 字号画在 4x 画布上，缩回后文字只剩 1/4 大小，
        # 胶囊内文字「看不见」（用户截图：关系网连接标签无字）。
        font = _pil_font(max(6, int(size * _SS)), False)
        if font is None:
            return None
        img, d = _ss_canvas(4096 // _SS, int(size * 3))
        l, t, r, b = d.textbbox((0, 0), text, font=font)
        tw = max(1, int(round((r - l) / _SS)))   # 1x 文字宽（定药丸幅宽）
        pw, ph = tw + 18, size + 11
        img = Image.new("RGBA", ((pw + 2) * _SS, (ph + 2) * _SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([_SS, _SS, (pw + 1) * _SS, (ph + 1) * _SS],
                            radius=(ph / 2) * _SS, fill=fill,
                            outline=outline or fg, width=_SS)
        # 文字 4x 坐标：左内距 9px、垂直居中（坐标与字号同步 ×_SS）
        tx, ty = 10 * _SS - l, ((ph + 2) * _SS - (b - t)) / 2 - t
        d.text((tx, ty), text, font=font, fill=fg)
        return _ss_done_pil(img, pw + 2, ph + 2)

    try:
        return _cached_photo(("pill", text, fg, fill, outline, size, _SS),
                             _render)
    except Exception:
        return None


# ---------------- 成品图 PhotoImage 缓存（v0.10 性能） ----------------
# zoom 缩放/刷新时所有卡片/连线/「我」按同一组参数重画。PIL 渲染 + Tk
# PhotoImage 粘贴（大图一次十几 ms）是刷新耗时主体。按量化 key 缓存
# PhotoImage，重复刷新零渲染零粘贴。
# 安全：PhotoImage 绑定 Tk 解释器——缓存条目用 weakref 记录创建时的
# default root，root 已销毁（退出登录/重开窗口）时自动失效重渲。
_IMG_CACHE = {}                 # (root, key) -> (weakref(root), photo, px)
_IMG_CACHE_PX = 0               # 缓存总像素（控内存）
# v0.21：24M→96M（~384MB）。24M 时 100 人网络就顶格 LRU 抖动（无谓重渲
# 1.2s+）；48M 时 200 人仍超（卡片底图 55M + 网络图 25M）。96M 可完整
# 驻留 200 人级「卡片页 + 网络页」全部图片；超出部分仍按 LRU 优雅降级。
# _gc_cache 惰性清扫死 root 条目，防多窗口/重开场景堆积。
_IMG_CACHE_PX_MAX = 96_000_000


def _cur_root():
    return getattr(tk, "_default_root", None)


def _gc_cache(force=False):
    """清扫 _IMG_CACHE 中 root 已销毁的死条目（v0.21）。

    PhotoImage 绑定 Tk 解释器——root 销毁（退出登录/重开窗口/测试多
    App 实例）后条目失效，且 id(root) 可能被新 root 复用（新 root 拿到
    旧解释器的 PhotoImage）。旧实现只在「访问同 key」时清单条，死条目
    长期堆积还占 LRU 预算。这里惰性全扫：每 64 次缓存访问扫一次
    （1000 条扫描约 50μs，可忽略）。
    """
    global _IMG_CACHE_PX, _GC_TICK
    _GC_TICK += 1
    if not force and _GC_TICK % 64:
        return
    dead = [k for k, v in _IMG_CACHE.items() if v[0]() is None]
    for k in dead:
        try:
            _IMG_CACHE_PX -= _IMG_CACHE[k][2]
            del _IMG_CACHE[k]
        except KeyError:
            pass


_GC_TICK = 0


def _cached_photo(key, render):
    """render() -> PIL Image；返回缓存的 PhotoImage（同 root 重复调用零成本）"""
    global _IMG_CACHE_PX
    _gc_cache()
    root = _cur_root()
    if root is None:
        pil = render()
        if pil is None:
            return None
        try:
            return ImageTk.PhotoImage(pil)
        except Exception:
            return None
    ck = (id(root), key)
    ent = _IMG_CACHE.get(ck)
    if ent is not None:
        if ent[0]() is root:
            _IMG_CACHE.pop(ck)
            _IMG_CACHE[ck] = ent           # LRU：移到最新
            return ent[1]
        del _IMG_CACHE[ck]                 # root 已销毁：清理死条目
        _IMG_CACHE_PX -= ent[2]
    pil = render()
    if pil is None:
        return None
    try:
        photo = ImageTk.PhotoImage(pil)
    except Exception:
        return None
    px = max(1, pil.width * pil.height)
    _IMG_CACHE[ck] = (weakref.ref(root), photo, px)
    _IMG_CACHE_PX += px
    while _IMG_CACHE_PX > _IMG_CACHE_PX_MAX and len(_IMG_CACHE) > 1:
        _k0 = next(iter(_IMG_CACHE))       # dict.pop(key) 返回值而非键值对
        _ent = _IMG_CACHE.pop(_k0)
        _IMG_CACHE_PX -= _ent[2]
    return photo


def _fit_ss(cw, ch):
    """按局部系画布像素数选超采样倍率（短连线 4x 保圆头质量，长连线 2x
    控耗时）——v0.10：改为「先缩回 1x 再旋转」管线后，超采样画布只是
    细长的局部系（(L+2pad)×(W+2pad)），不再是旋转后的巨大方框。"""
    px = max(1, cw * ch)
    if px * 16 <= 200_000:
        return 4
    if px * 9 <= 500_000:
        return 3
    return 2


def smooth_link(color, length, width, angle=0.0, dash=None, glow=True,
                alpha_from=None, alpha_to=None):
    """抗锯齿柔光连线 → PhotoImage

    - 局部系（水平 0°）渲染体育场：圆头粗线 + 三层柔光（外晕/中晕/实心）
    - dash=(on, off)：虚线（圆头点段，保留关系类型特征）
    - alpha_from/alpha_to：沿线段水平渐变透明度（「我」扩散线中心侧淡出）
    放置时图片中心对准线段中点；渲染长度已由调用方预留端头余量。
    v0.10：长度随 zoom 缩放（地图式全缩）。渲染管线（性能关键）：
    ① 局部系 ss 倍超采样绘制（画布只有细长一条，很小）
    ② LANCZOS 缩回 1x——边缘至此已抗锯齿
    ③ 1x BICUBIC rotate-expand——旋转已光滑的内容不产生锯齿，
       且变换量从「ss 倍巨图」降到「1x 方框」，长连线快约 10 倍
    ④ 成品 PIL 图按量化 key 进 LRU 缓存，重复刷新只包 PhotoImage
    """
    if not _HAS_PIL:
        return None
    try:
        L = max(4.0, float(length))
        W = max(2.0, float(width))
        # 量化 key：zoom 微调/刷新时参数几乎不变 → 缓存命中（零渲染零粘贴）
        key = (color, int(L / 6) * 6, round(W * 2) / 2, round(angle, 1),
               dash, bool(glow), alpha_from, alpha_to)

        def _render():
            pad = W * 3.0 if glow else W * 1.5
            cw, ch = int(L + 2 * pad) + 2, int(W + 2 * pad) + 2
            rgb = _hex2rgb(color, (230, 237, 243))
            ss = _fit_ss(cw, ch)

            def _mask(w):
                """体育场 L mask（ss 倍局部系画布）：圆头粗线 / 圆头虚线"""
                m = Image.new("L", (cw * ss, ch * ss), 0)
                dm = ImageDraw.Draw(m)
                y = ch / 2
                r = max(1, int(round(w * ss / 2)))
                lw = max(1, int(round(w * ss)))

                def _cap(x):
                    dm.ellipse([x * ss - r, y * ss - r,
                                x * ss + r, y * ss + r], fill=255)

                if dash:
                    on, off = dash
                    xs = pad
                    while xs < pad + L:
                        xe = min(xs + on, pad + L)
                        dm.line([xs * ss, y * ss, xe * ss, y * ss],
                                fill=255, width=lw)
                        _cap(xs)
                        _cap(xe)
                        xs += on + off
                else:
                    dm.line([pad * ss, y * ss, (pad + L) * ss, y * ss],
                            fill=255, width=lw)
                    _cap(pad)
                    _cap(pad + L)
                return m

            # 水平 alpha 渐变 mask（1x 逐列构建再放大到 ss，与体育场 mask 相乘）
            grad_ss = None
            if alpha_from is not None:
                g1 = Image.new("L", (cw, ch), 0)
                gd = ImageDraw.Draw(g1)
                x0, x1 = pad, pad + L
                for x in range(cw):
                    t = (x - x0) / max(1e-6, x1 - x0)
                    t = 0.0 if t < 0 else 1.0 if t > 1 else t
                    gd.line([(x, 0), (x, ch)],
                            fill=max(0, min(255, int(round(
                                alpha_from + (alpha_to - alpha_from) * t)))))
                grad_ss = g1.resize((cw * ss, ch * ss), _RESAMPLE)

            img = Image.new("RGBA", (cw * ss, ch * ss), (0, 0, 0, 0))
            for w, scale in (((W * 2.6), 0.16), ((W * 1.5), 0.34), (W, 1.0)) \
                    if glow else ((W, 1.0),):
                m = _mask(w)
                if grad_ss is not None:
                    m = ImageChops.multiply(m, grad_ss)
                lay = Image.new("RGBA", m.size, rgb + (int(255 * scale),))
                out = Image.new("RGBA", m.size, (0, 0, 0, 0))
                out.paste(lay, (0, 0), m)
                img.alpha_composite(out)
            # ② 缩回 1x（边缘已抗锯齿）→ ③ 1x 旋转（BICUBIC 不添锯齿）
            out = img.resize((cw, ch), _RESAMPLE)
            if angle:
                out = out.rotate(-angle, expand=True,
                                 resample=getattr(Image, "BICUBIC", 3))
            return out

        return _cached_photo(key, _render)
    except Exception:
        return None


def smooth_grad_text(text, size, c1, c2, bold=True):
    """水平渐变抗锯齿文字（透明底）→ PhotoImage；失败返回 None"""
    if not _HAS_PIL or not text:
        return None
    # v0.18 修复：同 smooth_pill——超采样画布文字字号乘 _SS，
    # 否则标题文字缩回后只剩 1/4（顶栏标题「看不见」）
    font = _pil_font(max(6, int(size * _SS)), bold)
    if font is None:
        return None
    try:
        img, d = _ss_canvas(4096 // _SS, int(size * 2.4))
        l, t, r, b = d.textbbox((0, 0), text, font=font)
        tw, th = int(round((r - l) / _SS)), int(round((b - t) / _SS))
        W, H = max(4, tw + 4), max(4, th + 4)
        mask = Image.new("L", (W * _SS, H * _SS), 0)
        ImageDraw.Draw(mask).text((2 * _SS - l, 2 * _SS - t), text,
                                  font=font, fill=255)
        # 水平渐变（1x 逐列构建后放大）
        g1 = Image.new("RGBA", (W, H))
        gd = ImageDraw.Draw(g1)
        c1r, c2r = _hex2rgb(c1, (34, 211, 238)), _hex2rgb(c2, (163, 113, 247))
        for x in range(W):
            tt = x / max(1, W - 1)
            gd.line([(x, 0), (x, H)],
                    fill=tuple(round(c1r[j] + (c2r[j] - c1r[j]) * tt)
                               for j in range(3)) + (255,))
        g4 = g1.resize((W * _SS, H * _SS), _RESAMPLE)
        out = Image.new("RGBA", (W * _SS, H * _SS), (0, 0, 0, 0))
        out.paste(g4, (0, 0), mask)
        return ImageTk.PhotoImage(out.resize((W, H), _RESAMPLE))
    except Exception:
        return None


def render_node_card(name, score, tier, color, avatar_path=None,
                     has_memo=False, state="normal", w=154, h=80):
    """关系网络节点整卡合成（w/h可缩放，内部元素按比例）"""
    if not _HAS_PIL:
        return None
    try:
        s = w / 154.0  # 缩放比例
        # v0.10：zoom 全缩下卡片可能 380×200+，4x 超采样画布上千万像素、
        # 每张卡上百 ms。按卡片尺寸自适应降 ss（2x 边缘依旧平滑）+
        # 成品 PhotoImage 缓存（w/h 量化到 2px，刷新零渲染零粘贴）。
        key = ("card", name, score, tier, color, avatar_path,
               bool(has_memo), state, int(w / 2) * 2, int(h / 2) * 2)

        def _render():
            # 模块级 _SS 交换（tkinter 单线程，函数同步执行，安全）
            global _SS
            _ss_old = _SS
            _SS = _fit_ss(w + 24, h + 26)
            try:
                return _render_node_card_inner(name, score, tier, color,
                                               avatar_path, has_memo, state,
                                               w, h, s)
            finally:
                _SS = _ss_old

        return _cached_photo(key, _render)
    except Exception:
        return None


def _render_node_card_inner(name, score, tier, color, avatar_path, has_memo,
                            state, w, h, s):
    try:
        img, d = _ss_canvas(w + 24, h + 26)
        ox, oy = 12 * s, 8 * s
        # —— 柔和投影 ——
        for off, alpha in ((3*s, 18), (7*s, 7)):
            d.rounded_rectangle(
                [(ox + off * 0.5) * _SS, (oy + off) * _SS,
                 (ox + w + off * 0.5) * _SS, (oy + h + off) * _SS],
                radius=15*s * _SS, fill=(5, 9, 16, alpha))
        # —— 卡底 ——
        bgc = {"hover": "#EFF5FF", "src": "#ECF8F1"}.get(state, "#FFFFFF")
        oc = {"hover": "#2563EB", "src": "#15805D"}.get(state, "#CBD8E8")
        ow = 2 if state in ("hover", "src") else 1
        d.rounded_rectangle([ox * _SS, oy * _SS, (ox + w) * _SS, (oy + h) * _SS],
                            radius=14*s * _SS, fill=bgc, outline=oc,
                            width=ow * _SS)
        # v0.25：左侧层级渐变条已删除（用户：「卡片上加一个凸出来的方块
        # 像素条，这个很不好看」）——直条贴在圆角卡左缘，边缘方块状外凸；
        # 层级信息已由描边色 + 头像外圈分数弧完整表达，该条纯属冗余。
        # —— 头像 ——
        ax, ay, ar = ox + 33*s, oy + h / 2, 22*s
        ar = max(8, int(ar))
        av = None
        if avatar_path and os.path.exists(avatar_path):
            try:
                av = Image.open(avatar_path).convert("RGBA")
                av.thumbnail((ar * 2 * _SS, ar * 2 * _SS), _RESAMPLE)
                d0 = min(av.size)
                av = av.crop((0, 0, d0, d0)).resize(
                    (ar * 2 * _SS, ar * 2 * _SS), _RESAMPLE)
                m = Image.new("L", (ar * 2 * _SS, ar * 2 * _SS), 0)
                ImageDraw.Draw(m).ellipse(
                    (0, 0, ar * 2 * _SS - 1, ar * 2 * _SS - 1), fill=255)
                av.putalpha(m)
            except Exception:
                av = None
        if av is not None:
            img.alpha_composite(av, (int((ax - ar) * _SS), int((ay - ar) * _SS)))
            d.ellipse([(ax - ar) * _SS, (ay - ar) * _SS,
                       (ax + ar) * _SS, (ay + ar) * _SS],
                      outline=color, width=max(1, int(2*s)) * _SS)
        else:
            d.ellipse([(ax - ar + 1) * _SS, (ay - ar + 1) * _SS,
                       (ax + ar - 1) * _SS, (ay + ar - 1) * _SS], fill=color)
            f1 = _pil_font(max(8, int(16 * s)) * _SS, True)
            if f1:
                d.text(((ax) * _SS, (ay) * _SS), (name or "?")[:1],
                       font=f1, fill="#06222B", anchor="mm")
        # —— v0.14：头像外圈分数弧（满分 360°：暗轨 + 层级色已得分量）——
        try:
            arc_r = ar + 3.5 * s
            aw = max(1, int(2 * s)) * _SS
            arc_l = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ad = ImageDraw.Draw(arc_l)
            box = [(ax - arc_r) * _SS, (ay - arc_r) * _SS,
                   (ax + arc_r) * _SS, (ay + arc_r) * _SS]
            ad.arc(box, 0, 360, fill=(255, 255, 255, 34), width=aw)
            ext = max(2.0, 360.0 * min(100, max(0, float(score))) / 100.0)
            ad.arc(box, -90, -90 + ext, fill=rgb0 + (255,), width=aw)
            img.alpha_composite(arc_l)
        except Exception:
            pass
        # —— 姓名 + 分值与层级（字号随缩放）——
        name_sz = max(7, int(13 * s))
        sub_sz = max(6, int(10 * s))
        fn, fs = _pil_font(name_sz * _SS, True), _pil_font(sub_sz * _SS, False)
        tx = (ox + 62*s) * _SS
        max_chars = max(3, int(9 * s))
        if fn:
            d.text((tx, (oy + h / 2 - 13*s) * _SS), (name or "")[:max_chars],
                   font=fn, fill="#18263C")
        if fs:
            d.text((tx, (oy + h / 2 + 12*s) * _SS),
                   f"{score} 分 · {tier}", font=fs, fill=color)
        # —— 记忆笔记紫点 ——
        if has_memo:
            dsz = max(3, int(7*s))
            d.ellipse([(ox + w - 15*s) * _SS, (oy + 8*s) * _SS,
                       (ox + w - 15*s + dsz) * _SS, (oy + 8*s + dsz) * _SS],
                      fill="#A371F7")
        # 返回 PIL 图（由 render_node_card 的 _cached_photo 包 PhotoImage）
        return _ss_done_pil(img, w + 24, h + 26)
    except Exception:
        return None


def render_me_node(avatar_path=None, name="我", d=116):
    """中心「我」节点整图：多重光晕 + 圆形头像/首字 + 昵称胶囊（抗锯齿）

    v0.10：d 为整体直径，内部所有元素（光环/头像/字号/胶囊）按 s=d/116
    等比缩放——地图式 zoom 下「我」与节点卡片、连线一起缩放，视觉一致。
    大 d 时超采样倍率自适应下降 + 成品 PhotoImage 缓存（d 量化 4px）。
    """
    if not _HAS_PIL:
        return None
    key = ("me", avatar_path, name, int(d / 4) * 4)

    def _render():
        try:
            s = max(0.2, d / 116.0)            # 内容缩放比例（zoom 全缩）
            CW, CH = d + 160 * s, d + 130 * s  # 画布（含光晕/名字留白）
            ss = _fit_ss(CW, CH)
            img = Image.new("RGBA", (max(4, int(CW * ss)), max(4, int(CH * ss))),
                            (0, 0, 0, 0))
            dr = ImageDraw.Draw(img)
            cx, cy = CW / 2, CH / 2 - 6 * s
            # —— 光晕（从内到外逐层变淡，尺寸随 s）——
            for rr, col, wd in ((58, (222, 231, 247), 1), (46, (205, 221, 249), 1),
                                (36, "#2563EB", 2)):
                rr *= s
                dr.ellipse([(cx - rr) * ss, (cy - rr) * ss,
                            (cx + rr) * ss, (cy + rr) * ss],
                           outline=col, width=max(1, int(wd * s)) * ss)
            r = max(6, int(27 * s))
            av = None
            if avatar_path and os.path.exists(avatar_path):
                try:
                    av = Image.open(avatar_path).convert("RGBA")
                    av.thumbnail((r * 2 * ss, r * 2 * ss), _RESAMPLE)
                    d0 = min(av.size)
                    av = av.crop((0, 0, d0, d0)).resize(
                        (r * 2 * ss, r * 2 * ss), _RESAMPLE)
                    m = Image.new("L", (r * 2 * ss, r * 2 * ss), 0)
                    ImageDraw.Draw(m).ellipse(
                        (0, 0, r * 2 * ss - 1, r * 2 * ss - 1), fill=255)
                    av.putalpha(m)
                except Exception:
                    av = None
            if av is not None:
                img.alpha_composite(av, (int((cx - r) * ss), int((cy - r) * ss)))
            else:
                dr.ellipse([(cx - r) * ss, (cy - r) * ss,
                            (cx + r) * ss, (cy + r) * ss],
                           fill="#EAF1FF", outline="#2563EB",
                           width=max(1, int(2 * s)) * ss)
                f1 = _pil_font(max(8, int(34 * s)), True)
                if f1:
                    dr.text((cx * ss, cy * ss), "我", font=f1,
                            fill="#2563EB", anchor="mm")
            # —— 昵称胶囊（字号/偏移随 s）——
            nm = name if name == "我" else f"{name} · 我"
            f9 = _pil_font(max(6, int(18 * s)), False)
            if f9:
                l, t, rr2, b = dr.textbbox((0, 0), nm, font=f9)
                pw = (rr2 - l) + 26 * s
                ph = 27 * s
                cy2 = cy + 42 * s
                dr.rounded_rectangle(
                    [(cx - pw / 2) * ss, cy2 * ss,
                     (cx + pw / 2) * ss, (cy2 + ph) * ss],
                    radius=(ph / 2) * ss, fill="#FFFFFF",
                    outline="#DCE4EF", width=max(1, int(s)) * ss)
                dr.text((cx * ss, (cy2 + ph / 2) * ss), nm, font=f9,
                        fill="#2563EB", anchor="mm")
            return img.resize((max(1, int(CW)), max(1, int(CH))), _RESAMPLE)
        except Exception:
            return None

    try:
        return _cached_photo(key, _render)
    except Exception:
        return None


def smooth_bg(w, h, cx, cy, rmax, bg="#0B0F17", glow="#162133"):
    """画布背景：淡点阵网格 + 中心径向柔光 + 淡同心环 + 放射纹 + 四角轻压暗
    （2x 超采样平滑，点阵若隐若现于光晕之下，突出中心「我」）"""
    if not _HAS_PIL:
        return None
    try:
        S = 2
        W2, H2 = int(w) * S, int(h) * S
        base = Image.new("RGB", (W2, H2), bg)
        d = ImageDraw.Draw(base)
        # ① 淡点阵网格（间距 34px，半径 1.15px，极淡）
        step = 34 * S
        dot = max(1, int(1.15 * S))
        for gy in range(int(step / 2), H2, step):
            for gx in range(int(step / 2), W2, step):
                d.ellipse([gx - dot, gy - dot, gx + dot, gy + dot],
                          fill="#1B2740")
        # ② 中心径向柔光：96px 小图放大 + 径向 alpha mask 融入底色
        n = 96
        g = Image.new("RGB", (n, n))
        gp = g.load()
        c0, c1 = _hex2rgb(bg), _hex2rgb(glow)
        m = Image.new("L", (n, n))
        mp = m.load()
        for yy in range(n):
            for xx in range(n):
                t = min(1.0, ((xx - n / 2) ** 2 + (yy - n / 2) ** 2) ** 0.5
                        / (n / 2))
                gp[xx, yy] = tuple(round(c0[j] + (c1[j] - c0[j]) * (1 - t) ** 1.6)
                                   for j in range(3))
                mp[xx, yy] = int(255 * (1 - t) ** 1.6)
        R = int(rmax * 1.35) * S
        g = g.resize((max(4, R * 2), max(4, R * 2)), _RESAMPLE)
        m = m.resize((max(4, R * 2), max(4, R * 2)), _RESAMPLE)
        base.paste(g, (int(cx * S) - R, int(cy * S) - R), m)
        d = ImageDraw.Draw(base)
        # ③ 淡同心环 + 放射纹
        for ri in range(3):
            rr = rmax * (0.42 + 0.26 * ri) * S
            d.ellipse([(cx * S) - rr, (cy * S) - rr,
                       (cx * S) + rr, (cy * S) + rr],
                      outline="#1E2D4A", width=max(1, S // 2))
        for k in range(12):
            ang = 6.283185307 * k / 12
            d.line([cx * S, cy * S,
                    (cx + rmax * _cos(ang)) * S,
                    (cy + rmax * _sin(ang)) * S],
                   fill="#182742", width=1)
        # ④ 四角轻压暗（聚焦中心，小图放大即可，渐变平滑）
        v = Image.new("L", (n, n))
        vp = v.load()
        for yy in range(n):
            for xx in range(n):
                t = min(1.0, ((xx - n / 2) ** 2 + (yy - n / 2) ** 2) ** 0.5
                        / (n / 2))
                vp[xx, yy] = int(64 * t ** 2.2)
        v = v.resize((W2, H2), _RESAMPLE)
        base.paste(Image.new("RGB", (W2, H2), (4, 7, 12)), (0, 0), v)
        base = base.resize((int(w), int(h)), _RESAMPLE)
        return ImageTk.PhotoImage(base)
    except Exception:
        return None


def bg_dot_tile(step=34, n=6, dot_r=1.15, color="#1B2740"):
    """可平铺的淡点阵小图：n×n 个圆点、边长 step*n（4x 超采样，圆点平滑）。
    在画布上按 step 网格重复铺设即得完整点阵背景（item 少、平移零成本）。"""
    if not _HAS_PIL:
        return None
    try:
        S = 4
        size = step * n
        img = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        r = max(1.0, dot_r * S)
        for i in range(n):
            for j in range(n):
                x = (step / 2 + i * step) * S
                y = (step / 2 + j * step) * S
                d.ellipse([x - r, y - r, x + r, y + r], fill=color)
        img = img.resize((size, size), _RESAMPLE)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


def bg_corner_vig(size=240, max_alpha=64, color=(4, 7, 12)):
    """四角压暗小图：中心透明 → 边缘 max_alpha 的径向渐变（96px 小图放大，平滑）。
    使用时把图片中心对准视口四角，营造聚焦中心的纵深感。"""
    if not _HAS_PIL:
        return None
    try:
        n = 96
        m = Image.new("L", (n, n))
        mp = m.load()
        for yy in range(n):
            for xx in range(n):
                t = min(1.0, ((xx - n / 2) ** 2 + (yy - n / 2) ** 2) ** 0.5
                        / (n / 2))
                mp[xx, yy] = int(max_alpha * t ** 2.2)
        m = m.resize((size, size), _RESAMPLE)
        img = Image.new("RGBA", (size, size), color + (0,))
        img.putalpha(m)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


import math as _math


def _cos(a):
    return _math.cos(a)


def _sin(a):
    return _math.sin(a)


def ensure_dir():
    os.makedirs(AVATAR_DIR, exist_ok=True)


def save_avatar(src_path, pid):
    """保存形象图片，返回存储路径（统一存为 png）"""
    ensure_dir()
    dst = os.path.join(AVATAR_DIR, f"avatar_{pid}.png")
    if _HAS_PIL:
        im = Image.open(src_path).convert("RGBA")
        im.thumbnail((256, 256))
        im.save(dst, "PNG")
    else:
        ext = os.path.splitext(src_path)[1].lower()
        if ext not in (".png", ".gif"):
            raise RuntimeError("未安装 Pillow，仅支持 png/gif 图片")
        shutil.copyfile(src_path, dst)
    return dst


def load_photo(path, size=(64, 64)):
    """读取形象图为 PhotoImage；失败返回 None"""
    if not path or not os.path.exists(path):
        return None
    try:
        if _HAS_PIL:
            im = Image.open(path).convert("RGBA")
            im.thumbnail(size)
            return ImageTk.PhotoImage(im)
        else:
            ph = tk.PhotoImage(file=path)
            fx = max(1, ph.width() // size[0])
            fy = max(1, ph.height() // size[1])
            f = max(fx, fy)
            return ph.subsample(f, f) if f > 1 else ph
    except Exception:
        return None


def load_circle_photo(path, size=(38, 38)):
    """读取形象图并圆形裁切（四角透明），返回 PhotoImage；失败退回 load_photo"""
    if not path or not os.path.exists(path):
        return None
    if not _HAS_PIL:
        return load_photo(path, size)
    try:
        im = Image.open(path).convert("RGBA")
        im.thumbnail((size[0] * 2, size[1] * 2))
        w, h = im.size
        d = min(w, h)
        im = im.crop(((w - d) // 2, (h - d) // 2, (w + d) // 2, (h + d) // 2))
        im = im.resize(size, _RESAMPLE)
        # 圆形遮罩也走 4x 超采样，边缘平滑无阶梯
        m4 = Image.new("L", (size[0] * _SS, size[1] * _SS), 0)
        ImageDraw.Draw(m4).ellipse(
            (0, 0, size[0] * _SS - 1, size[1] * _SS - 1), fill=255)
        mask = m4.resize(size, _RESAMPLE)
        im.putalpha(mask)
        return ImageTk.PhotoImage(im)
    except Exception:
        return load_photo(path, size)


def letter_avatar(name, size=44, color="#22D3EE", glow=True):
    """姓氏色圆头像（无形象图时）：渐变圆盘 + 姓氏首字 + 层级色辉光描边

    风格与关系网节点卡一致（亮盘 + 深字 #06222B），外加细描边提升精致度；
    8x 超采样绘制后 LANCZOS 缩回，44px 显示依旧锐利。
    返回 PhotoImage；PIL 不可用时返回 None（调用方退回无头像）。
    v0.21：走 _cached_photo 缓存（key=姓/尺寸/颜色/辉光）——卡片增量刷新与
    网络节点重复请求同参数头像时零渲染成本（人多卡顿优化的一环）。
    v0.24：glow=False 供连接器卡片用——简洁风去掉外辉光圈，只留渐变盘。
    """
    if not _HAS_PIL:
        return None
    ch = (name or "?")[:1]
    return _cached_photo(("letterav", ch, int(size), color, bool(glow)),
                         lambda: _render_letter_avatar(ch, size, color, glow))


def _render_letter_avatar(ch, size, color, glow=True):
    """letter_avatar 的实际渲染（PIL Image；失败返回 None）"""
    try:
        S = size * _SS
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        c = S / 2
        rad = S * 0.46
        r, g, b = _hex2rgb(color)
        # 外辉光（本色柔光圈；v0.24 glow=False 时跳过——简洁风）
        if glow:
            glowimg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
            ImageDraw.Draw(glowimg).ellipse(
                [c - rad, c - rad, c + rad, c + rad], fill=(r, g, b, 90))
            glowimg = glowimg.filter(ImageFilter.GaussianBlur(S * 0.030))
            img = Image.alpha_composite(img, glowimg)
        # 渐变圆盘：顶部提亮 35% → 底部本色（垂直三段渐变逐行画线）
        top = tuple(min(255, int(v * 1.35)) for v in (r, g, b))
        disc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dd = ImageDraw.Draw(disc)
        for i in range(S):
            t = i / max(1, S - 1)
            col = tuple(int(top[j] + ((r, g, b)[j] - top[j]) * t)
                        for j in range(3))
            dd.line([(0, i), (S, i)], fill=col + (255,))
        m = Image.new("L", (S, S), 0)
        ImageDraw.Draw(m).ellipse([c - rad, c - rad, c + rad, c + rad],
                                  fill=255)
        disc.putalpha(m)
        img = Image.alpha_composite(img, disc)
        # 描边（本色）
        d = ImageDraw.Draw(img)
        d.ellipse([c - rad, c - rad, c + rad, c + rad],
                  outline=(r, g, b, 255), width=max(2, int(S * 0.022)))
        # 姓氏首字（深色，与节点卡一致）
        f = _pil_font(int(S * 0.40), True)
        d.text((c, c + S * 0.012), ch, font=f, fill="#06222B", anchor="mm")
        return img.resize((size, size), _RESAMPLE)
    except Exception:
        return None


# ---------------- ComfyUI UI 资产（v0.11） ----------------
# tools/gen_ui_assets.py 生成 + tools/prep_ui_assets.py 后处理的界面美化
# 资产（空状态插画/星图背景/页面横幅/App 图标/登录页背景）。
# 加载走 _cached_photo 同款 LRU；文件缺失/PIL 不可用时返回 None，
# 调用方一律有降级路径（emoji / 纯色 / 矢量点阵）。
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images", "ui")


def _ui_path(name):
    p = os.path.join(UI_DIR, name)
    return p if os.path.exists(p) else None


def ui_asset(name, sub=1):
    """加载 images/ui/<name>.png 为 PhotoImage（带缓存）。

    sub>1 时整数倍缩小（PhotoImage.subsample，省内存）；
    文件不存在返回 None（调用方降级）。
    """
    p = _ui_path("%s.png" % name)
    if p is None:
        return None

    def _render():
        try:
            return Image.open(p).convert("RGBA")
        except Exception:
            return None

    photo = _cached_photo(("ui", name, sub), _render)
    if photo is not None and sub > 1:
        try:
            photo = photo.subsample(sub, sub)
        except Exception:
            pass
    return photo


def bg_stars_tile_ui():
    """画布背景星图平铺小图（512x512，toggle_bg 开启时用）；缺失返回 None"""
    return ui_asset("bg_stars_tile")


# ---------------- v0.16：交互按钮三态立体面 ----------------
# tk.Button 只支持一张图 → 面图把「渐变 + 高光 + 暗边 + 圆角 + 图标 + 文字」
# 全部烘进去，hover/press 换图即三态立体效果。
_BTN_PAL = {
    "normal":  ("#FFFFFF", "#F8FAFD", "#DCE4EF"),
    "primary": ("#2563EB", "#245CE0", "#2563EB"),
    "green":   ("#15805D", "#137653", "#15805D"),
    "danger":  ("#FFF3F4", "#FFF3F4", "#F0CDD3"),
    "ghost":   ("#F0F4FA", "#F0F4FA", "#F0F4FA"),
    "tab":     ("#FFFFFF", "#FFFFFF", "#FFFFFF"),
    "tab_on":  ("#EAF1FF", "#EAF1FF", "#D8E5FF"),
}
_BTN_FG = {"normal": "#34445C", "primary": "#FFFFFF", "green": "#FFFFFF",
           "danger": "#B8324A", "ghost": "#63738A",
           "tab": "#63738A", "tab_on": "#245BD5"}
TEXT_HEX = "#18263C"          # 与 ui.py TEXT 一致


def _lighten_hex(hexc, f):
    rgb = _hex2rgb(hexc)
    return "#%02X%02X%02X" % tuple(
        min(255, int(c + (255 - c) * f)) for c in rgb)


def btn_face(kind="normal", state="normal", w=80, h=30, text="",
             icon=None, radius=None):
    """按钮/页签三态立体面（v0.16，PIL 3x 超采样，带 LRU 缓存）

    - 竖向渐变（上亮下暗）+ 顶部 1px 高光 + 底部 1px 暗边 → 立体感
    - hover：顶/底/描边整体提亮；press：渐变反转（内凹）
    - text/icon 直接烘入（tk.Button 单图限制）；icon 传 PIL Image
    - radius 默认 8；传 h/2 得全圆胶囊（页签用）
    """
    if not _HAS_PIL:
        return None
    w = max(8, int(w)); h = max(8, int(h))
    r = int(radius if radius is not None else min(8, h // 2))
    icon_key = None
    if icon is not None:
        try:
            icon_key = (icon.size, icon.mode, icon.tobytes()[:64])
        except Exception:
            icon_key = "ic"
    key = ("btn", kind, state, w, h, r, text, icon_key)

    def _render():
        try:
            SS = 3
            W, H = w * SS, h * SS
            top, bot, bd = _BTN_PAL.get(kind, _BTN_PAL["normal"])
            if state == "hover":
                top = _lighten_hex(top, 0.16)
                bot = _lighten_hex(bot, 0.16)
                bd = _lighten_hex(bd, 0.35)
            elif state == "press":
                top, bot = bot, top
                bd = "#%02X%02X%02X" % tuple(
                    int(c * 0.55) for c in _hex2rgb(bd))
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            # 竖向渐变圆角底
            t255, b255 = _hex2rgb(top), _hex2rgb(bot)
            grad = Image.new("RGBA", (1, H))
            for yy in range(H):
                tt = yy / max(1, H - 1)
                grad.putpixel((0, yy), tuple(
                    int(t255[j] + (b255[j] - t255[j]) * tt) for j in range(3))
                    + (255,))
            grad = grad.resize((W, H))
            mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, W - 1, H - 1], radius=r * SS, fill=255)
            img.paste(grad, (0, 0), mask)
            d = ImageDraw.Draw(img)
            # 描边
            d.rounded_rectangle([SS, SS, W - 1 - SS, H - 1 - SS],
                                radius=max(1, (r - 1) * SS),
                                outline=_hex2rgb(bd) + (255,), width=SS)
            # 顶部高光 + 底部暗边（立体关键）
            hl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            hd = ImageDraw.Draw(hl)
            hd.line([(r * SS * 0.7, SS * 1.5), (W - r * SS * 0.7, SS * 1.5)],
                    fill=(255, 255, 255, 46), width=SS)
            hd.line([(r * SS * 0.7, H - SS * 1.5), (W - r * SS * 0.7, H - SS * 1.5)],
                    fill=(30, 55, 100, 8), width=SS)
            hl.putalpha(Image.composite(hl.split()[3], Image.new("L", (W, H), 0),
                                        mask))
            img = Image.alpha_composite(img, hl)
            d = ImageDraw.Draw(img)
            # 图标（左）
            cx = w / 2
            fnt = _pil_font(9 * SS, False)
            tw = 0
            if fnt is not None and text:
                try:
                    tw = fnt.getlength(text) / SS
                except Exception:
                    tw = len(text) * 9
            iw = 0
            if icon is not None:
                iw = 18 + (6 if text else 0)
            content = tw + iw
            x = (w - content) / 2
            if icon is not None:
                try:
                    ic = icon.convert("RGBA").resize(
                        (18 * SS, 18 * SS), _RESAMPLE)
                    img.alpha_composite(ic, (int(x * SS),
                                             int((h - 18) / 2 * SS)))
                except Exception:
                    pass
                x += iw
            # 文字（垂直居中，微下移 0.5px 视觉对齐）
            if text and fnt is not None:
                d.text(((x + tw / 2) * SS, h / 2 * SS + SS * 0.5), text,
                       font=fnt, fill=_hex2rgb(_BTN_FG.get(kind, TEXT_HEX))
                       + (255,), anchor="mm")
            return img.resize((w, h), _RESAMPLE)
        except Exception:
            return None

    return _cached_photo(key, _render)


def text_width(text, size, bold=False):
    """PIL 字体精确测量文字像素宽（与 smooth_text 同字体，布局避让用）"""
    f = _pil_font(max(4, int(size)), bold)
    if f is None:
        return len(text) * size * 0.62
    try:
        return f.getlength(text or "")
    except Exception:
        return len(text) * size * 0.62


# ---------------- v0.12 透明/蒙版资产加载 ----------------
_UI_TEX = {}          # name -> PIL.Image（进程内缓存，只读一次）


def _ui_pil(name):
    """加载 images/ui/<name>.png 为 PIL RGBA（带缓存）；缺失返回 None"""
    if name in _UI_TEX:
        return _UI_TEX[name]
    p = _ui_path("%s.png" % name)
    img = None
    if p is not None:
        try:
            img = Image.open(p).convert("RGBA")
        except Exception:
            img = None
    _UI_TEX[name] = img
    return img


def glass_overlay_pil():
    """星纹蒙版（RGBA 128x128，alpha≈26%）→ 供 smooth_card 蒙版叠加；
    缺失返回 None（卡片退化为纯色底）"""
    return None  # 浅色卡片保持干净，不叠加旧星纹


def glass_tile_ui():
    """玻璃面板平铺小图（128x128 不透明，预合成 SURFACE 底）→ PhotoImage；
    缺失返回 None"""
    return ui_asset("glass_tile")


def glass_tint_ui():
    """玻璃混合纯色（'#RRGGBB'）：Text/Entry 等只吃纯色的控件用，
    与玻璃底纹视觉一致；缺失返回 None"""
    return "#F0F4FA"


def ui_asset_scaled(name, w, h):
    """加载 images/ui/<name>.png 并按像素尺寸缩放（PIL LANCZOS，带缓存）。

    透明辉光资产在画布上要随 zoom 缩放，PhotoImage 只支持整数倍
    subsample，任意尺寸走 PIL 重缩放 + LRU 缓存。缺失返回 None。
    """
    key = ("uiscaled", name, int(w), int(h))

    def _render():
        im = _ui_pil(name)
        if im is None:
            return None
        try:
            return im.resize((max(1, int(w)), max(1, int(h))), _RESAMPLE)
        except Exception:
            return None

    return _cached_photo(key, _render)
