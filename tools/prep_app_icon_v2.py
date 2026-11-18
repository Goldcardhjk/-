# -*- coding: utf-8 -*-
"""v0.64 主图标后处理：app_icon_v2 原图 → 全套应用图标。

产物（均落 images/ui/）：
  app_icon.png     256x256 圆角透明角（窗口 iconphoto，覆盖 v0.63 旧版）
  title_icon.png   26x26 标题栏小图标（取星环中心主体区）
  app_icon.ico     16/24/32/48/64/128/256 多尺寸内嵌（PyInstaller --icon 用）
  app_icon_512.png 512x512 圆角透明角（高清展示/发布页用）

原图不动（images/ui_raw/app_icon_v2.png）；旧版图先备份到 _tmp/ 再覆盖。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    from PIL import Image, ImageDraw
except ImportError:
    # 仅当当前解释器无 Pillow 时，才借用 _pylibs（注意 wheel 与解释器版本匹配）
    sys.path.insert(0, os.path.join(ROOT, "_pylibs"))
    from PIL import Image, ImageDraw

RAW = os.path.join(ROOT, "images", "ui_raw", "app_icon_v2.png")
OUT = os.path.join(ROOT, "images", "ui")
RESAMPLE = Image.LANCZOS


def rounded_icon(img, size, radius_ratio=0.22):
    """缩放并切圆角（透明角），输出 RGBA"""
    img = img.convert("RGB").resize((size, size), RESAMPLE)
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    r = int(size * radius_ratio)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def main():
    src = Image.open(RAW)
    os.makedirs(OUT, exist_ok=True)

    # ① 主图标 256（窗口 iconphoto）
    rounded_icon(src, 256).save(os.path.join(OUT, "app_icon.png"))

    # ② 高清 512（展示/发布页）
    rounded_icon(src, 512).save(os.path.join(OUT, "app_icon_512.png"))

    # ③ 标题小图标：中心星环主体（中心 46% 区域）→ 26x26
    w, h = src.size
    c = src.crop((int(w * 0.27), int(h * 0.27), int(w * 0.73), int(h * 0.73)))
    c = c.convert("RGB").resize((26, 26), RESAMPLE)
    c.save(os.path.join(OUT, "title_icon.png"))

    # ④ ICO：多尺寸内嵌（PIL 从高分辨率单帧自动重采样全部尺寸）
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    rounded_icon(src, 256).save(
        os.path.join(OUT, "app_icon.ico"),
        format="ICO",
        sizes=sizes,
    )

    for name in ("app_icon.png", "app_icon_512.png", "title_icon.png", "app_icon.ico"):
        p = os.path.join(OUT, name)
        print("  %s  %d bytes" % (name, os.path.getsize(p)))
    print("完成")


if __name__ == "__main__":
    main()
