# -*- coding: utf-8 -*-
"""v0.20 定制标题图标与卡片角标（程序化生成 → images/ui/）

  icon_remind     发光铃铛+声波纹（青 #22D3EE）     40px  「近期提醒」标题图标
  icon_overview   发光节点星络+基座线（橙 #F0883E）  40px  「网络概览」标题图标
  card_deco_star  右上角星芒角标（青→紫渐变）        128px 人脉卡片卡面装饰

风格与既有 icon_* 家族一致：8x 超采样 + 高斯柔光垫底（prep_ui_assets._glow_icon）。
用法：.venv/Scripts/python.exe tools/prep_v020.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prep_ui_assets import _glow_icon, OUT  # noqa: E402

CYAN = (34, 211, 238)      # 近期提醒（层级青）
ORANGE = (240, 136, 62)    # 网络概览（层级橙）


def proc_icon_remind(size=40):
    """「近期提醒」铃铛图标：钟体弧线 + 顶部提手 + 两侧声波纹 + 下方铃锤"""
    def _draw(d, S, col):
        c = S / 2
        lw = max(3, int(S * 0.050))
        # 钟体：上宽下窄的弧线碗（用三段弧近似）
        r = S * 0.24
        cy = S * 0.52
        d.arc([c - r, cy - r * 0.9, c + r, cy + r * 1.1],
              start=180, end=360, fill=col + (255,), width=lw)
        # 钟体两侧竖线收口到铃锤
        d.line([(c - r, cy), (c - r * 0.72, cy + S * 0.10)],
               fill=col + (255,), width=lw)
        d.line([(c + r, cy), (c + r * 0.72, cy + S * 0.10)],
               fill=col + (255,), width=lw)
        # 底部横沿 + 铃锤
        d.line([(c - r * 0.82, cy + S * 0.10), (c + r * 0.82, cy + S * 0.10)],
               fill=col + (255,), width=lw)
        d.ellipse([c - S * 0.045, cy + S * 0.10, c + S * 0.045,
                   cy + S * 0.10 + S * 0.09], fill=col + (255,))
        # 顶部提手小圆
        d.ellipse([c - S * 0.05, S * 0.13, c + S * 0.05, S * 0.23],
                  outline=col + (255,), width=max(2, int(S * 0.036)))
        # 两侧声波纹（三道渐短弧）
        for i, rr in enumerate((0.36, 0.44)):
            a0, a1 = (-52, -18) if True else (0, 0)
            d.arc([c - S * rr, cy - S * rr, c + S * rr, cy + S * rr],
                  start=200 + i * 12, end=250 + i * 12,
                  fill=col + (255,), width=max(2, int(S * 0.030)))
    out = _glow_icon(_draw, size, color=CYAN)
    out.save(os.path.join(OUT, "icon_remind.png"))
    return out.size


def proc_icon_overview(size=40):
    """「网络概览」星络图标：中心大节点 + 三卫星节点 + 连线 + 基座线"""
    def _draw(d, S, col):
        lw = max(3, int(S * 0.048))
        c = S * 0.46
        cy = S * 0.44
        # 卫星节点（左上/右上/正下）
        sats = ((S * 0.20, S * 0.24), (S * 0.76, S * 0.30),
                (S * 0.30, S * 0.72))
        for (sx, sy) in sats:
            d.line([(c, cy), (sx, sy)], fill=col + (160,),
                   width=max(2, int(S * 0.030)))
        # 中心节点（大圆 + 内点）
        d.ellipse([c - S * 0.13, cy - S * 0.13, c + S * 0.13,
                   cy + S * 0.13], outline=col + (255,), width=lw)
        d.ellipse([c - S * 0.045, cy - S * 0.045, c + S * 0.045,
                   cy + S * 0.045], fill=col + (255,))
        # 卫星小圆点
        for (sx, sy) in sats:
            d.ellipse([sx - S * 0.05, sy - S * 0.05, sx + S * 0.05,
                       sy + S * 0.05], fill=col + (255,))
        # 基座横线（两段，示意「全览」）
        d.line([(S * 0.22, S * 0.85), (S * 0.44, S * 0.85)],
               fill=col + (255,), width=max(2, int(S * 0.034)))
        d.line([(S * 0.56, S * 0.85), (S * 0.78, S * 0.85)],
               fill=col + (255,), width=max(2, int(S * 0.034)))
    out = _glow_icon(_draw, size, color=ORANGE)
    out.save(os.path.join(OUT, "icon_overview.png"))
    return out.size


def proc_card_deco_star(size=128):
    """卡片右上角星芒角标：四角主星芒 + 中心亮核 + 柔光（青→紫）"""
    from PIL import Image, ImageDraw, ImageFilter
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = S / 2
    # 渐变采样：青(34,211,238) → 紫(167,139,250)
    c1, c2 = (34, 211, 238), (167, 139, 250)

    def grad(t):
        return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

    # 主星芒：四条长芒（水平/垂直）+ 四条短斜芒，宽度渐细
    for ang in range(0, 360, 45):
        a = math.radians(ang)
        long_ = ang % 90 == 0
        L = S * (0.40 if long_ else 0.22)
        W = S * (0.030 if long_ else 0.018)
        # 用多边形画带锥度的芒（粗根细尖）
        px, py = math.cos(a), math.sin(a)
        nx, ny = -py, px
        tip = (c + px * L, c + py * L)
        base = (c - px * S * 0.04, c - py * S * 0.04)
        w1 = W / 2
        pts = [(base[0] + nx * w1, base[1] + ny * w1),
               (tip[0], tip[1]),
               (base[0] - nx * w1, base[1] - ny * w1)]
        d.polygon(pts, fill=grad(abs(px)) + (235,))
    # 中心亮核（径向两圈）
    for rr, alpha in ((0.075, 255), (0.13, 90)):
        d.ellipse([c - S * rr, c - S * rr, c + S * rr, c + S * rr],
                  fill=(235, 250, 255, alpha))
    # 整体柔光
    soft = img.filter(ImageFilter.GaussianBlur(S * 0.016))
    out = Image.alpha_composite(soft, img).resize((size, size),
                                                  Image.LANCZOS)
    out.save(os.path.join(OUT, "card_deco_star.png"))
    return out.size


def main():
    print("[gen] icon_remind   ->", proc_icon_remind())
    print("[gen] icon_overview ->", proc_icon_overview())
    print("[gen] card_deco_star ->", proc_card_deco_star())
    print("\nv0.20 程序化定制资产全部生成完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
