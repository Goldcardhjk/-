# -*- coding: utf-8 -*-
"""进步壹号 Agent 发布封面合成（v0.24）

底图：ImageGen 深色科技风辐射星图（1024x1536）
合成：标题 / 副标题 / 卖点 chips / 底部标语 + 去右下角水印
字体：微软雅黑（系统字体，只读使用）
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "..", "generated-images",
                   "深色科技风软件发布主视觉图_深邃的藏蓝黑色渐变背景_中央是一_2026-10-26T08-05-47.png")
OUT = os.path.join(ROOT, "发布封面_进步壹号Agent.png")

FONT_BD = "C:/Windows/Fonts/msyhbd.ttc"   # 微软雅黑 Bold
FONT_RG = "C:/Windows/Fonts/msyh.ttc"     # 微软雅黑 Regular

CYAN = (34, 211, 238)        # #22D3EE 主强调色（同 App）
GREEN = (63, 185, 80)        # #3FB950 次强调色（同 App）
WHITE = (240, 246, 252)      # #F0F6FC
MUTED = (158, 176, 192)      # 弱化文字
DARK = (8, 14, 26)           # 深底


def font(path, size):
    return ImageFont.truetype(path, size)


def draw_glow_text(base, xy, text, fnt, fill, glow_color, glow_r=18, glow_a=110):
    """带外辉光的文字：模糊层 + 锐利层"""
    x, y = xy
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((x, y), text, font=fnt, fill=glow_color + (glow_a,))
    layer = layer.filter(ImageFilter.GaussianBlur(glow_r))
    base.alpha_composite(layer)
    d2 = ImageDraw.Draw(base)
    d2.text((x, y), text, font=fnt, fill=fill)
    return d2


def main():
    im = Image.open(SRC).convert("RGBA")
    W, H = im.size
    print("base:", W, "x", H)

    # ---------- ① 去右下角水印（取邻近底色覆盖） ----------
    bg_px = im.getpixel((W - 260, H - 8))
    d = ImageDraw.Draw(im)
    d.rectangle([W - 300, H - 70, W, H], fill=bg_px)

    # ---------- ② 底部压暗渐变带（保文字可读） ----------
    band = Image.new("RGBA", (W, 340), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    for i in range(340):
        a = int(200 * (i / 340) ** 1.4)
        bd.line([(0, i), (W, i)], fill=DARK + (a,))
    im.alpha_composite(band, (0, H - 340))

    # ---------- ③ 顶部小标签（字距拉开） ----------
    f_label = font(FONT_BD, 30)
    label = "JINBU YIHAO AGENT · 独立开发"
    lw = d.textlength(label, font=f_label)
    draw_glow_text(im, ((W - lw) / 2, 96), label, f_label, CYAN, CYAN, 12, 80)

    # ---------- ④ 主标题 ----------
    f_title = font(FONT_BD, 118)
    title = "进步壹号 Agent"
    tw = d.textlength(title, font=f_title)
    draw_glow_text(im, ((W - tw) / 2, 150), title, f_title, WHITE, CYAN, 26, 120)

    # ---------- ⑤ 分隔线 + 副标题 ----------
    ly = 330
    for x in range(int(W * 0.28), int(W * 0.72)):
        t = (x - W * 0.28) / (W * 0.44)
        a = int(255 * (1 - abs(t - 0.5) * 2) * 0.85)
        d.line([(x, ly), (x, ly + 2)], fill=CYAN + (a,))
    f_sub = font(FONT_RG, 44)
    sub = "替你记住人情往来的桌面助手"
    sw = d.textlength(sub, font=f_sub)
    d.text(((W - sw) / 2, 366), sub, font=f_sub, fill=(201, 209, 217))

    # ---------- ⑥ 卖点 chips（底部） ----------
    f_chip = font(FONT_BD, 30)
    chips = ["关系星图", "智能送礼", "AI 祝福语", "本地隐私"]
    cw = [d.textlength(t, font=f_chip) + 56 for t in chips]
    gap = 22
    total = sum(cw) + gap * (len(chips) - 1)
    cx = (W - total) / 2
    cy = H - 300
    ch = 62
    for t, w in zip(chips, cw):
        x0, y0 = cx, cy
        d.rounded_rectangle([x0, y0, x0 + w, y0 + ch], radius=ch / 2,
                            fill=(13, 24, 40, 215), outline=CYAN + (170,),
                            width=2)
        d.text((x0 + 28, y0 + 14), t, font=f_chip, fill=WHITE)
        cx += w + gap

    # ---------- ⑦ 底部标语 + 版本行 ----------
    f_tag = font(FONT_RG, 34)
    tag = "关系多的人，值得被温柔记住"
    tgw = d.textlength(tag, font=f_tag)
    draw_glow_text(im, ((W - tgw) / 2, H - 200), tag, f_tag,
                   (201, 209, 217), CYAN, 10, 60)
    f_ver = font(FONT_RG, 24)
    ver = "v0.24 · Windows 桌面版 · 数据保存在本地"
    vw = d.textlength(ver, font=f_ver)
    d.text(((W - vw) / 2, H - 140), ver, font=f_ver, fill=MUTED)

    im.convert("RGB").save(OUT, "PNG")
    print("saved:", OUT)


if __name__ == "__main__":
    main()
