# -*- coding: utf-8 -*-
"""进步壹号 Agent —— ComfyUI UI 资产后处理（v0.11 / v0.12）

把 images/ui_raw/ 的 ComfyUI 原图处理成 ui.py 可直接使用的资产，
落 images/ui/：

  bg_stars_tile.png  四边无缝化（wrap crossfade）→ toggle_bg 平铺纹理
  banner_*.png       1024x512 → 460x116，左边缘 40px 渐隐入 #0B0F17
  empty_net.png      等比缩到 520 宽，四边渐隐入 #0B0F17
  app_icon.png       缩 256x256 + 圆角（22%）透明角 → 窗口图标
  title_icon.png     从 app_icon 原图中心裁 256 → 26x26 标题小图标
  login_hero.png     等比缩 480x500，四边渐隐入 #0B0F17

v0.12 透明/蒙版手法：
  glow_nodes / ai_avatar / icon_* / deco_corner / deco_sparkle /
  divider_gem / hero_particles
                    「纯黑底 + 发光主体」原图 → 亮度软提取 alpha → 真透明
                    RGBA PNG（黑底全透明、辉光半透亮），直接叠加在任意
                    底色上（Label bg=容器色 / Canvas create_image）
  glass_tile.png     星图纹理 alpha 40% 预合成 SURFACE 底色 → 不透明
                    128x128 平铺小图（rounded_card 卡片玻璃底/右面板）
  glass_tint.png     同上但输出 4x4 平均色（供 Text/Entry 等只吃纯色的
                    控件取「玻璃混合色」，视觉与玻璃底一致）
  top_glow_bar.png   程序化 RGBA 光带：青→紫→透明 水平渐变（顶栏底部）

用法：python tools/prep_ui_assets.py
"""

import os
import math
from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "images", "ui_raw")
OUT = os.path.join(ROOT, "images", "ui")
BG_RGB = (11, 15, 23)          # #0B0F17 与 ui.py BG 一致
SURFACE_RGB = (19, 26, 38)     # #131A26 与 ui.py SURFACE 一致
CYAN = (34, 211, 238)          # #22D3EE
PURPLE = (163, 113, 247)       # #A371F7

RESAMPLE = Image.LANCZOS


def fade_to_bg(img, px):
    """四边 px 像素渐隐到 BG 色（alpha 渐隐 + 叠 BG 色，输出 RGB）"""
    img = img.convert("RGB")
    w, h = img.size
    mask = Image.new("L", (w, h), 255)
    # 左右
    for x in range(px):
        a = int(255 * (x / max(1, px - 1)))
        for y in range(h):
            mask.putpixel((x, y), a)
            mask.putpixel((w - 1 - x, y), a)
    # 上下
    for y in range(px):
        a = int(255 * (y / max(1, px - 1)))
        for x in range(w):
            if mask.getpixel((x, y)) > a:
                mask.putpixel((x, y), a)
            if mask.getpixel((x, h - 1 - y)) > a:
                mask.putpixel((x, h - 1 - y), a)
    bg = Image.new("RGB", (w, h), BG_RGB)
    return Image.composite(img, bg, mask)


def seamless_tile(img):
    """四边无缝平铺：左 1/4 与右 1/4 交叉融合，上下同理"""
    img = img.convert("RGB")
    w, h = img.size
    # 左右 wrap：把右半翻到左边交叉淡化
    half = w // 2
    left = img.crop((0, 0, half, h))
    right = img.crop((w - half, 0, w, h))
    blend = Image.blend(right, left, 0.5)
    out = Image.new("RGB", (w, h))
    out.paste(blend, (0, 0))
    out.paste(blend, (w - half, 0))
    # 上下同理
    half_h = h // 2
    top = out.crop((0, 0, w, half_h))
    bottom = out.crop((0, h - half_h, w, h))
    blend2 = Image.blend(bottom, top, 0.5)
    final = Image.new("RGB", (w, h))
    final.paste(blend2, (0, 0))
    final.paste(blend2, (0, h - half_h))
    return final


def rounded_icon(img, size=256, radius_ratio=0.22):
    """缩放到 size 并切圆角（透明角），输出 RGBA"""
    img = img.convert("RGB").resize((size, size), RESAMPLE)
    mask = Image.new("L", (size, size), 0)
    from PIL import ImageDraw
    d = ImageDraw.Draw(mask)
    r = int(size * radius_ratio)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def luma_to_alpha(img, gain=2.2, gamma=0.8, floor=24):
    """黑底发光图 → 真透明 RGBA。

    alpha = max(r,g,b) 软提取：纯黑底→全透明，辉光主体→半透亮，发光
    颜色原样保留（叠加到任意底色上自然融合）。gain 提升辉光强度、
    gamma 控制过渡软硬、floor 压掉黑底噪点。
    """
    img = img.convert("RGB")
    r, g, b = img.split()
    a = ImageChops.lighter(r, ImageChops.lighter(g, b))
    a = a.point(lambda v: min(255, int(255 * gain * (v / 255.0) ** gamma)))
    if floor:
        a = a.point(lambda v: 0 if v < floor else v)
    out = img.convert("RGBA")
    out.putalpha(a)
    return out


def transparent(name, size=None, crop=None, **kw):
    """黑底原图 → alpha 提取 → 可选裁剪 → 等比缩到 size → 存 OUT

    crop=(l,t,r,b) 在缩放前按原图比例裁剪（用于去掉 bluePencilXL 绘本
    模型顽固生成的「发光书本」主体，保留光弧/粒子部分）。
    """
    img = Image.open(os.path.join(RAW, "%s.png" % name))
    img = luma_to_alpha(img, **kw)
    if crop:
        img = img.crop(crop)
    if size:
        if isinstance(size, int):
            w, h = img.size
            size = (size, int(h * size / w)) if w >= h else (int(w * size / h), size)
        img = img.resize(size, RESAMPLE)
    img.save(os.path.join(OUT, "%s.png" % name))
    return img.size


def glass_tile(size=128, alpha=40):
    """星图纹理 → alpha 混合 SURFACE 底色 → 不透明平铺小图。

    预合成绕开 tkinter 半透明合成限制：任何控件都能拿它当背景，
    视觉上是「蒙了一层半透明星纹的玻璃面板」。
    同时输出 glass_overlay.png：纯星纹 RGBA（alpha≈26%），供
    smooth_card 以蒙版方式叠加到任意卡片底色上。
    """
    src = os.path.join(OUT, "bg_stars_tile.png")
    if not os.path.exists(src):
        src = os.path.join(RAW, "bg_stars_tile.png")
    tex = Image.open(src).convert("RGB").resize((size, size), RESAMPLE)
    base = Image.new("RGB", (size, size), SURFACE_RGB)
    img = Image.blend(base, tex, alpha / 255.0)
    img.save(os.path.join(OUT, "glass_tile.png"))
    # 4x4 平均色 → 纯色控件（Text/Entry）取玻璃混合色用
    tiny = img.resize((4, 4), RESAMPLE)
    px = list(tiny.getdata())
    n = len(px)
    avg = tuple(sum(c[i] for c in px) // n for i in range(3))
    Image.new("RGB", (4, 4), avg).save(os.path.join(OUT, "glass_tint.png"))
    # 星纹蒙版：RGBA，alpha 统一压到 26%（叠加时只透出细微星光）
    ov = tex.convert("RGBA")
    ov.putalpha(tex.convert("L").point(lambda v: int(66 * v / 255)))
    ov.save(os.path.join(OUT, "glass_overlay.png"))
    return "#%02X%02X%02X" % avg


def top_glow_bar(w=1600, h=56):
    """程序化 RGBA 顶栏光带：左青→中紫→右青，上下 alpha 渐隐。

    Label bg=BG 放置即「浮在顶栏上的一道极光」，纯 PIL 无需生成。
    """
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for x in range(w):
        t = x / max(1, w - 1)
        # 双色彩带：0=青 0.5=紫 1=青
        if t < 0.5:
            f = t / 0.5
            col = tuple(int(CYAN[i] + (PURPLE[i] - CYAN[i]) * f) for i in range(3))
        else:
            f = (t - 0.5) / 0.5
            col = tuple(int(PURPLE[i] + (CYAN[i] - PURPLE[i]) * f) for i in range(3))
        for y in range(h):
            # 垂直 alpha：中间最亮（峰在 35% 处），上下渐隐
            fy = 1.0 - abs(y - h * 0.35) / (h * 0.65)
            a = int(150 * max(0.0, fy) ** 1.6)
            px[x, y] = (col[0], col[1], col[2], a)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.save(os.path.join(OUT, "top_glow_bar.png"))
    return img.size


def proc_icon_copy(size=40):
    """程序化「复制」图标：两枚错位圆角矩形发光轮廓。

    bluePencilXL 对 copy/overlap 类描述有顽固「书本」偏见（两次重掷
    都是书），改用程序化绘制——青色前层 + 紫色后层 + 高斯柔光，
    风格与 ComfyUI 透明图标完全一致。
    """
    S = size * 8                       # 8x 超采样，缩回去抗锯齿
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(2, int(S * 0.055))
    r = S * 0.15
    # 后层（右下错位，紫）
    d.rounded_rectangle([S * 0.36, S * 0.32, S * 0.84, S * 0.80],
                        radius=r, outline=PURPLE + (255,), width=lw)
    # 前层（左上，青）
    d.rounded_rectangle([S * 0.16, S * 0.16, S * 0.64, S * 0.64],
                        radius=r, outline=CYAN + (255,), width=lw)
    # 柔光：模糊一层垫底再合成
    glow = img.filter(ImageFilter.GaussianBlur(S * 0.028))
    out = Image.alpha_composite(glow, img)
    out = out.resize((size, size), RESAMPLE)
    out.save(os.path.join(OUT, "icon_copy.png"))
    return out.size


# ---------------- v0.14：排名徽章（程序化） ----------------
_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
    ("C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simhei.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
]


def _badge_font(size, bold=True):
    from PIL import ImageFont
    for reg, bld in _FONT_CANDIDATES:
        path = bld if bold else reg
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, int(size))
        except Exception:
            continue
    return ImageFont.load_default()


# ---------------- v0.16：提醒/图表/记录图标（程序化） ----------------
def _glow_icon(draw_fn, size=40, color=CYAN, glow=0.030, blur=0.026):
    """程序化发光图标基座：draw_fn(d, S) 在 8x 画布上作画（粗轮廓风格），
    外层高斯柔光垫底，风格与 ComfyUI 透明 icon_* 家族一致（v0.16）。"""
    S = size * 8
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    draw_fn(d, S, color)
    soft = img.filter(ImageFilter.GaussianBlur(S * glow))
    out = Image.alpha_composite(soft, img)
    out = out.resize((size, size), RESAMPLE)
    return out


def proc_icon_clock(size=40):
    """「近期提醒」时钟图标：粗轮廓圆 + 12/3/6/9 刻度 + 时分两针"""
    def _draw(d, S, col):
        c = S / 2
        r = S * 0.30
        lw = max(3, int(S * 0.052))
        d.ellipse([c - r, c - r, c + r, c + r], outline=col + (255,),
                  width=lw)
        # 刻度（四正向长刻度）
        for ang in (0, 90, 180, 270):
            a = math.radians(ang)
            x0 = c + (r - S * 0.075) * math.cos(a)
            y0 = c + (r - S * 0.075) * math.sin(a)
            x1 = c + (r - S * 0.018) * math.cos(a)
            y1 = c + (r - S * 0.018) * math.sin(a)
            d.line([(x0, y0), (x1, y1)], fill=col + (255,),
                   width=max(2, int(S * 0.038)))
        # 时针（指向 9）+ 分针（指向 12）
        d.line([(c, c), (c - r * 0.52, c)], fill=col + (255,),
               width=max(2, int(S * 0.046)))
        d.line([(c, c), (c, c - r * 0.68)], fill=col + (255,),
               width=max(2, int(S * 0.040)))
        d.ellipse([c - S * 0.035, c - S * 0.035, c + S * 0.035,
                   c + S * 0.035], fill=col + (255,))
    out = _glow_icon(_draw, size)
    out.save(os.path.join(OUT, "icon_clock.png"))
    return out.size


def proc_icon_chart(size=40):
    """「社交价值拆解」上升图表图标：三根渐高柱 + 趋势线"""
    def _draw(d, S, col):
        lw = max(3, int(S * 0.052))
        base = S * 0.74
        for i, (x, h) in enumerate(((0.24, 0.22), (0.44, 0.38),
                                    (0.64, 0.54))):
            x0 = S * x
            d.rounded_rectangle([x0, base - S * h, x0 + S * 0.13, base],
                                radius=S * 0.03, outline=col + (255,),
                                width=lw)
        # 趋势线（从左下柱顶到右上柱顶）
        d.line([(S * 0.24, base - S * 0.22), (S * 0.44, base - S * 0.38),
                (S * 0.70, base - S * 0.60)], fill=col + (255,),
               width=max(2, int(S * 0.040)), joint="curve")
        # 顶端箭头点
        d.ellipse([S * 0.70 - S * 0.045, base - S * 0.60 - S * 0.045,
                   S * 0.70 + S * 0.045, base - S * 0.60 + S * 0.045],
                  fill=col + (255,))
    out = _glow_icon(_draw, size)
    out.save(os.path.join(OUT, "icon_chart.png"))
    return out.size


def proc_icon_log(size=40):
    """「互动记录」列表图标：三条圆点头横线（记录/流水）"""
    def _draw(d, S, col):
        lw = max(3, int(S * 0.052))
        ys = (0.28, 0.50, 0.72)
        widths = (0.52, 0.44, 0.36)
        for y, w in zip(ys, widths):
            d.line([(S * 0.26, S * y), (S * (0.26 + w), S * y)],
                   fill=col + (255,), width=lw)
            d.ellipse([S * 0.14 - S * 0.045, S * y - S * 0.045,
                       S * 0.14 + S * 0.045, S * y + S * 0.045],
                      fill=col + (255,))
    out = _glow_icon(_draw, size)
    out.save(os.path.join(OUT, "icon_log.png"))
    return out.size


def gen_rank_badges(size=40, ranks=8):
    """程序化排名徽章 rank_1..rank_N.png（v0.14）

    替代原「🥇🥈🥉 emoji + 第四名起裸数字」的断层样式：
    前三名金/银/铜金属三段渐变，第四名起冷钢青蓝（与深色玻璃界面同族，
    外发光带青色调）；顶部椭圆高光 + 上下斜面描边 + 居中粗体数字。
    8x 超采样绘制后 LANCZOS 缩回，18px 显示依旧锐利。
    """
    S = size * 8
    palettes = {
        1: ((255, 229, 138), (245, 197, 66), (168, 123, 18), (90, 61, 0)),
        2: ((244, 247, 250), (201, 212, 222), (124, 136, 150), (58, 69, 82)),
        3: ((242, 195, 155), (201, 138, 84), (126, 78, 38), (74, 46, 18)),
    }
    steel = ((174, 191, 212), (107, 126, 150), (55, 69, 92), (240, 246, 252))
    out_sizes = []
    for rk in range(1, ranks + 1):
        top, mid, bot, num = palettes.get(rk, steel)
        # 外发光（金属徽章用本色辉光，钢徽章用青，与界面霓虹一致）
        glow_col = CYAN if rk > 3 else tuple(min(255, c + 24) for c in mid)
        glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse(
            [S * 0.05, S * 0.05, S * 0.95, S * 0.95], fill=glow_col + (78,))
        glow = glow.filter(ImageFilter.GaussianBlur(S * 0.045))
        img = Image.alpha_composite(Image.new("RGBA", (S, S), (0, 0, 0, 0)),
                                    glow)
        # 主体圆盘：垂直三段渐变（逐行画线，超采样下平滑）+ 圆形蒙版
        c, rad = S / 2, S * 0.40
        disc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dd = ImageDraw.Draw(disc)
        for i in range(S):
            t = i / max(1, S - 1)
            if t < 0.5:
                f = t / 0.5
                col = tuple(int(top[j] + (mid[j] - top[j]) * f)
                            for j in range(3))
            else:
                f = (t - 0.5) / 0.5
                col = tuple(int(mid[j] + (bot[j] - mid[j]) * f)
                            for j in range(3))
            dd.line([(0, i), (S, i)], fill=col + (255,))
        m = Image.new("L", (S, S), 0)
        ImageDraw.Draw(m).ellipse([c - rad, c - rad, c + rad, c + rad],
                                  fill=255)
        disc.putalpha(m)
        img = Image.alpha_composite(img, disc)
        # 顶部椭圆高光（高斯柔化）
        hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(hl).ellipse(
            [c - rad * 0.60, c - rad * 0.80, c + rad * 0.60, c + rad * 0.02],
            fill=(255, 255, 255, 82))
        hl = hl.filter(ImageFilter.GaussianBlur(S * 0.012))
        img = Image.alpha_composite(img, hl)
        # 斜面描边：上亮下暗（金属浮雕感）
        d = ImageDraw.Draw(img)
        rw = max(2, int(S * 0.016))
        d.arc([c - rad, c - rad, c + rad, c + rad], start=195, end=345,
              fill=tuple(min(255, x + 72) for x in top) + (235,), width=rw)
        d.arc([c - rad, c - rad, c + rad, c + rad], start=15, end=165,
              fill=tuple(int(x * 0.42) for x in bot) + (235,), width=rw)
        # 居中数字
        f = _badge_font(S * 0.38, True)
        d.text((c, c + S * 0.012), str(rk), font=f, fill=num + (255,),
               anchor="mm")
        out = img.resize((size, size), RESAMPLE)
        out.save(os.path.join(OUT, "rank_%d.png" % rk))
        out_sizes.append(out.size)
    return out_sizes


def banner(name, w=460, h=116, fade=40, align="bottom"):
    """横幅：等比缩放后裁横条，左边缘渐隐入 BG。

    align="bottom" 取中下横条（礼物盒/卡册/握手小人等主体在中下部），
    align="center" 取中心横条（月牙+信封分布适中的情况）。
    """
    img = Image.open(os.path.join(RAW, "%s.png" % name)).convert("RGB")
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    img = img.resize((int(iw * scale), int(ih * scale)), RESAMPLE)
    iw, ih = img.size
    x0 = (iw - w) // 2
    if align == "bottom":
        y0 = ih - h
    else:
        y0 = (ih - h) // 2
    img = img.crop((x0, y0, x0 + w, y0 + h))
    # 左边缘渐隐（右对齐贴标题，左缘接文字）
    mask = Image.new("L", (w, h), 255)
    for x in range(fade):
        a = int(255 * (x / max(1, fade - 1)))
        for y in range(h):
            mask.putpixel((x, y), a)
    bg = Image.new("RGB", (w, h), BG_RGB)
    img = Image.composite(img, bg, mask)
    img.save(os.path.join(OUT, "%s.png" % name))
    return img.size


def main():
    os.makedirs(OUT, exist_ok=True)

    # ① 背景星图：四边无缝
    src = os.path.join(RAW, "bg_stars_tile.png")
    if os.path.exists(src):
        tile = seamless_tile(Image.open(src))
        tile = tile.resize((512, 512), RESAMPLE)   # 平铺尺寸减半，显存友好
        tile.save(os.path.join(OUT, "bg_stars_tile.png"))
        print("[ok] bg_stars_tile.png 512x512 seamless")

    # ② 四条功能页横幅（gift/cards/conn 主体在中下部 → 底部对齐；
    #    greet 月牙+信封分布适中 → 中心）
    for b, al in (("banner_gift", "bottom"), ("banner_greet", "center"),
                  ("banner_cards", "bottom"), ("banner_conn", "bottom")):
        if os.path.exists(os.path.join(RAW, "%s.png" % b)):
            print("[ok] %s.png %s" % (b, banner(b, align=al)))

    # ③ 空状态插画：520 宽，四边渐隐
    src = os.path.join(RAW, "empty_net.png")
    if os.path.exists(src):
        img = Image.open(src).convert("RGB")
        w, h = img.size
        nh = int(h * 520 / w)
        img = img.resize((520, nh), RESAMPLE)
        img = fade_to_bg(img, 14)
        img.save(os.path.join(OUT, "empty_net.png"))
        print("[ok] empty_net.png 520x%d" % nh)

    # ④ App 图标：256 圆角
    src = os.path.join(RAW, "app_icon.png")
    if os.path.exists(src):
        icon_src = Image.open(src).convert("RGB")
        icon = rounded_icon(icon_src, 256)
        icon.save(os.path.join(OUT, "app_icon.png"))
        # ⑤ 标题小图标：原图中心 30% 区域（节点箭头主体）→ 26x26
        w, h = icon_src.size
        c = icon_src.crop((int(w * 0.30), int(h * 0.30),
                           int(w * 0.70), int(h * 0.70)))
        c = c.resize((26, 26), RESAMPLE)
        c.save(os.path.join(OUT, "title_icon.png"))
        print("[ok] app_icon.png 256x256 + title_icon.png 26x26")

    # ⑥ 登录页背景：480x500，四边渐隐
    src = os.path.join(RAW, "login_hero.png")
    if os.path.exists(src):
        img = Image.open(src).convert("RGB")
        w, h = img.size
        nh = int(h * 480 / w)
        img = img.resize((480, nh), RESAMPLE)
        if nh >= 500:
            y0 = (nh - 500) // 2
            img = img.crop((0, y0, 480, y0 + 500))
        else:
            bg = Image.new("RGB", (480, 500), BG_RGB)
            bg.paste(img, (0, (500 - nh) // 2))
            img = bg
        img = fade_to_bg(img, 16)
        img.save(os.path.join(OUT, "login_hero.png"))
        print("[ok] login_hero.png 480x500")

    # ⑦ v0.12 透明/辉光资产：黑底 → 亮度提 alpha
    #    glow_nodes/deco_corner/hero_particles 的 bluePencilXL 原图底部
    #    有「发光书本」主体（绘本模型偏见），裁剪去掉只留光弧/粒子。
    if os.path.exists(os.path.join(RAW, "glow_nodes.png")):
        print("[ok] glow_nodes.png %s" % (transparent(
            "glow_nodes", 512, crop=(0, 0, 832, 545)),))
    if os.path.exists(os.path.join(RAW, "ai_avatar.png")):
        print("[ok] ai_avatar.png %s" % (transparent("ai_avatar", 72,
                                                     gain=2.4, floor=30),))
    for ic, sz in (("icon_gift", 22), ("icon_greet", 22),
                   ("icon_cards", 22), ("icon_conn", 22)):
        if os.path.exists(os.path.join(RAW, "%s.png" % ic)):
            print("[ok] %s.png %s" % (ic, transparent(ic, sz, gain=2.6,
                                                     floor=36)))
    if os.path.exists(os.path.join(RAW, "deco_corner.png")):
        print("[ok] deco_corner.png %s" % (transparent(
            "deco_corner", 240, crop=(0, 0, 512, 282), gain=1.8, floor=18),))
    if os.path.exists(os.path.join(RAW, "deco_sparkle.png")):
        print("[ok] deco_sparkle.png %s" % (transparent("deco_sparkle", 128,
                                                        gain=2.0, floor=20),))
    if os.path.exists(os.path.join(RAW, "divider_gem.png")):
        print("[ok] divider_gem.png %s" % (transparent("divider_gem", 120,
                                                       gain=2.5, floor=32),))
    if os.path.exists(os.path.join(RAW, "hero_particles.png")):
        print("[ok] hero_particles.png %s" % (transparent(
            "hero_particles", (480, 250), crop=(0, 0, 768, 400),
            gain=2.2, floor=20),))

    # ⑧ v0.13 表情替代图标：黑底 → 亮度提 alpha → 40px（2x 超采样存盘，
    #    ui 里按需 subsample；gain 高于装饰类，细线条小尺寸仍可见）
    for ic in ("icon_add", "icon_more", "icon_link", "icon_panel",
               "icon_net", "icon_refresh", "icon_bg", "icon_rank",
               "icon_import", "icon_save", "icon_edit", "icon_del",
               "icon_avatar", "icon_search", "icon_slider", "icon_star",
               # v0.62 支付域：Vibe Pay 徽标 / 扫码图标
               "icon_vibepay", "icon_qr",
               # v0.63 场景卡：办公/学校/家庭
               "icon_scene_work", "icon_scene_school", "icon_scene_home"):
        if os.path.exists(os.path.join(RAW, "%s.png" % ic)):
            print("[ok] %s.png %s" % (ic, transparent(ic, 40, gain=2.8,
                                                     floor=42),))
    # icon_copy：绘本书本偏见规避 → 程序化发光图标（覆盖生成产物）
    print("[ok] icon_copy.png %s (程序化)" % (proc_icon_copy(),))

    # ⑧b v0.14 排名徽章 rank_1..8：程序化金属/冷钢圆牌（金/银/铜 + 青钢），
    #     替代「🥇🥈🥉 + 裸数字」断层样式；维护优先级行 / 人脉卡片排名复用
    print("[ok] rank_1..8.png %s (程序化徽章)" % (gen_rank_badges(),))

    # ⑧c v0.16 表情替代图标：近期提醒(时钟)/社交价值(图表)/互动记录(列表)，
    #     粗轮廓 + 青色发光，风格与 ComfyUI icon_* 家族一致
    print("[ok] icon_clock.png %s (程序化)" % (proc_icon_clock(),))
    print("[ok] icon_chart.png %s (程序化)" % (proc_icon_chart(),))
    print("[ok] icon_log.png %s (程序化)" % (proc_icon_log(),))

    # ⑧d v0.62 支付域：支付成功插画（ComfyUI 生成）→ 420 圆角卡
    src = os.path.join(RAW, "pay_success.png")
    if os.path.exists(src):
        art = rounded_icon(Image.open(src).convert("RGB"), 420,
                           radius_ratio=0.10)
        art.save(os.path.join(OUT, "pay_success.png"))
        print("[ok] pay_success.png 420x420 rounded")

    # ⑨ v0.12 蒙版/预合成资产
    print("[ok] glass_tile.png 128x128 + glass_tint = %s"
          % glass_tile())
    print("[ok] top_glow_bar.png %s" % (top_glow_bar(),))

    print("\n全部处理完成 -> %s" % OUT)


if __name__ == "__main__":
    main()
