# -*- coding: utf-8 -*-
"""把系统桌面通道截取的原始帧合成比赛字幕帧。"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_tmp" / "v069" / "competition_demo"
RAW = OUT / "raw_frames"
META = OUT / "raw_meta"
FRAMES = OUT / "frames"
FRAMES.mkdir(exist_ok=True)
W, H = 1920, 1080
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
BOLD_PATH = r"C:\Windows\Fonts\msyhbd.ttc"
font_title = ImageFont.truetype(BOLD_PATH, 36)
font_sub = ImageFont.truetype(FONT_PATH, 25)
font_small = ImageFont.truetype(FONT_PATH, 20)


def compose(raw, title, subtitle, dim=False):
    base = Image.new("RGB", (W, H), "#07111f")
    draw = ImageDraw.Draw(base)
    draw.rectangle((0, 0, W, 84), fill="#0b1a2b")
    draw.text((48, 18), title, font=font_title, fill="#eaf7ff")
    view = raw.convert("RGB").resize((1664, 957), Image.Resampling.LANCZOS)
    if dim:
        view = ImageEnhance.Brightness(view).enhance(0.32)
    base.paste(view, (128, 123))
    draw.rectangle((0, 84, W, 122), fill="#0b1a2b")
    draw.rectangle((0, 84, 12, H), fill="#32d5d2")
    draw.text((50, 88), subtitle, font=font_sub, fill="#a9c3d8")
    draw.text((50, 1042), "v0.69 · 离线验收演示｜模拟模型 + 模拟购买，不发生真实扣款，不作为真实交易凭证",
              font=font_small, fill="#89aac3")
    return base


meta_files = sorted(META.glob("*.png.json"))
expected = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))["frames"]
if len(meta_files) != expected:
    raise RuntimeError("外部录屏帧数不是 %d：%d" % (expected, len(meta_files)))
for index, meta_path in enumerate(meta_files, 1):
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    raw_path = RAW / meta_path.name[:-5]
    with Image.open(raw_path) as raw:
        window = raw.crop(tuple(meta["bbox"]))
        frame = compose(window, meta["title"], meta["subtitle"], meta.get("dim", False))
        frame.save(FRAMES / ("%04d.png" % index), compress_level=2)
print(json.dumps({"frames": len(meta_files), "out": str(FRAMES)}, ensure_ascii=False))
