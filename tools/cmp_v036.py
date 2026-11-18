# -*- coding: utf-8 -*-
"""生成 v0.35 -> v0.36 的上下对比图（只截关系网络画布区）。"""
import sys
from PIL import Image, ImageDraw, ImageFont

BOX = (21, 166, 1253, 889)      # 画布区
BG = (16, 18, 26)
FG = (232, 236, 245)
ACC = (0, 200, 255)
WARN = (255, 120, 90)

before_p, after_p, out_p = sys.argv[1], sys.argv[2], sys.argv[3]


def font(sz):
    for f in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(f, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def crop(p):
    return Image.open(p).convert("RGB").crop(BOX)


a, b = crop(before_p), crop(after_p)
cw, ch = a.size
PAD = 16
HEAD = 52
CANVAS_W = cw + PAD * 2
CANVAS_H = HEAD * 2 + ch * 2 + PAD * 3

cv = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
d = ImageDraw.Draw(cv)

f_title = font(24)
f_sub = font(17)

# 上：v0.35
y = PAD
d.text((PAD, y + 8), "v0.35  自动布局后", font=f_title, fill=FG)
d.text((PAD + 230, y + 14), "长线横穿辐射线 7 处  ·  线-卡最小净空 58px",
       font=f_sub, fill=WARN)
cv.paste(a, (PAD, y + HEAD))

# 下：v0.36
y2 = PAD + HEAD + ch + PAD
d.text((PAD, y2 + 8), "v0.36  两层构造", font=f_title, fill=ACC)
d.text((PAD + 230, y2 + 14), "长线横穿辐射线 0 处  ·  线-卡最小净空 34.8px  ·  线-线 0 交叉",
       font=f_sub, fill=(120, 230, 170))
cv.paste(b, (PAD, y2 + HEAD))

d.line([(PAD, y2 + HEAD - PAD // 2), (CANVAS_W - PAD, y2 + HEAD - PAD // 2)],
       fill=(52, 58, 74), width=1)

cv.save(out_p)
print("saved", out_p, cv.size)
