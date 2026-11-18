# -*- coding: utf-8 -*-
"""进步壹号 Agent —— ComfyUI UI 资产生成工具（v0.11）

通过本地 ComfyUI API（默认 http://127.0.0.1:8188）用 bluePencilXL_v700
（SDXL 蓝铅插画风）+ sdxl.vae + storybook-kids-xl LoRA 生成界面美化资产，
产物落 <项目>/images/ui_raw/（未经处理的原图），再由 prep_ui_assets.py
做渐隐/裁切/量化后进 <项目>/images/ui/。

用法：
    python tools/gen_ui_assets.py                 # 生成全部
    python tools/gen_ui_assets.py --only app_icon,empty_net
    python tools/gen_ui_assets.py --wait 30       # 队列忙时最多等待 30 分钟

注意：ComfyUI 实例可能正在跑其他项目任务（显存 16GB 独占），脚本会等
队列清空后才提交，绝不打断已在跑的任务。
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import uuid

COMFY = "http://127.0.0.1:8188"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "images", "ui_raw")

CKPT = "bluePencilXL_v700.safetensors"
VAE = "sdxl.vae.safetensors"
LORA = "storybook-kids-xl.safetensors"
LORA_STRENGTH = 0.40

NEG = ("text, watermark, signature, logo, letters, words, captions, subtitles, "
       "user interface, blurry, low quality, worst quality, jpeg artifacts, "
       "photorealistic, 3d render, bright background, white background, "
       "daylight, extra limbs, deformed, mutated hands, duplicate")

BASE = ("deep navy night sky background, flat illustration, blue pencil "
        "children picture book style, minimal elegant composition, cyan and "
        "violet soft glow, dark muted palette, high quality, no text")

# name -> (width, height, seed, extra prompt)
ASSETS = {
    # 首页画布空状态插画（竖图）：夜空下的空软木板，几张空白人物卡垂着线
    "empty_net": (832, 1216, 1101,
        "a lonely empty corkboard floating in a deep navy night sky, several "
        "blank white profile cards pinned on it with thin hanging threads, "
        "faint constellation lines between empty spots, one warm small desk "
        "lamp glow from the side, tiny stars around, soft and quiet mood"),
    # 画布背景星图（可平铺）：深navy星空 + 细星座线 + 微弱点阵
    "bg_stars_tile": (1024, 1024, 1102,
        "seamless starry night sky texture, deep navy blue background, many "
        "tiny scattered soft stars, a few faint thin constellation lines, "
        "very subtle dot grid, calm and dark, even distribution across the "
        "whole frame, no focal point"),
    # 送礼页横幅（4:1 横图）：星空下的礼物盒 + 流星
    "banner_gift": (1024, 512, 1103,
        "wide banner scene, a small wrapped gift box with a ribbon glowing "
        "warmly, a shooting star streaking across the sky, tiny sparkles "
        "and small stars, empty space on the left for text"),
    # 祝福语页横幅：信封 + 火漆印 + 流星尾迹 + 月牙
    "banner_greet": (1024, 512, 1104,
        "wide banner scene, an envelope with a wax seal floating in the sky "
        "with thin light trails and a small crescent moon, soft sparkles, "
        "empty space on the left for text"),
    # 人脉卡片页横幅：一叠空白人物卡 + 星光
    "banner_cards": (1024, 512, 1105,
        "wide banner scene, a neat fanned stack of blank profile cards with "
        "soft glowing edges like a hand of cards, tiny stars around, empty "
        "space on the left for text"),
    # 连接器页横幅：两个发光节点 + 光丝握手 + 绿色信号波
    "banner_conn": (1024, 512, 1106,
        "wide banner scene, two glowing nodes facing each other connected by "
        "a luminous handshake of thin light threads, gentle green signal "
        "waves between them, tiny stars, empty space on the left for text"),
    # App 图标：深navy圆角方章 + 节点上升箭头
    "app_icon": (1024, 1024, 1107,
        "app icon design, a rounded square badge with deep navy background, "
        "a rising arrow made of three connected glowing nodes and thin "
        "lines, small stars around it, cyan to violet gradient glow, clean "
        "centered symmetric composition, simple shapes"),
    # 登录页 hero 背景（竖图）：夜空关系网络全景
    "login_hero": (768, 800, 1108,
        "a calm deep navy night sky with a small social network of glowing "
        "nodes and thin luminous lines spread across the upper half, soft "
        "horizon glow at the bottom, tiny stars, quiet and welcoming mood, "
        "lower half mostly empty dark sky"),

    # ================= v0.12 第二批：透明/辉光素材 =================
    # 本批统一「纯黑底 + 发光主体」生成，后处理用亮度提 alpha 得到真透明
    # PNG（黑底=全透明，辉光主体=半透亮），用于画布光晕/头像/图标/装饰。
    # 星云光晕团：垫在关系节点背后的柔光（大面积 bokeh）
    "glow_nodes": (832, 832, 1211,
        "abstract soft out of focus bokeh circles and gentle light haze, cyan "
        "and violet glowing orbs of pure light, no objects, no books, no "
        "pages, no paper, no silhouette, evenly spread soft haze, isolated on "
        "pure black background"),
    # AI 助手头像：迷你机器人星使（圆润机器球 + 光环 + 星星）
    "ai_avatar": (512, 512, 1212,
        "a cute small round robot head with a glowing halo ring above and a "
        "tiny antenna, rounded helmet shape, friendly glowing face screen, no "
        "human, no girl, no hair, no body, cyan and violet glow, isolated on "
        "pure black background, centered, front view"),
    # 功能图标（256）：礼物盒
    "icon_gift": (256, 256, 1203,
        "a small glowing gift box with ribbon icon, rounded simple shapes, "
        "cyan glow outline, isolated on pure black background, centered"),
    # 功能图标：火漆信封
    "icon_greet": (256, 256, 1204,
        "a glowing envelope with wax seal icon, rounded simple shapes, violet "
        "glow outline, isolated on pure black background, centered"),
    # 功能图标：扇形卡册
    "icon_cards": (256, 256, 1205,
        "a fanned stack of three glowing profile cards icon, rounded simple "
        "shapes, cyan glow outline, isolated on pure black background, centered"),
    # 功能图标：相连双节点
    "icon_conn": (256, 256, 1206,
        "two glowing dots connected by a light thread icon, rounded simple "
        "shapes, green and cyan glow, isolated on pure black background, "
        "centered"),
    # 角落星云装饰：单角放射柔光 + 星点（放置时四角镜像/旋转复用）
    "deco_corner": (512, 512, 1213,
        "abstract diagonal light rays and soft nebula glow radiating from the "
        "top left corner, tiny stars along the rays, no books, no pages, no "
        "paper, no objects, isolated on pure black background, only one "
        "corner has content, other areas empty"),
    # 星尘粒子群：大小不一的闪烁星点 + 微十字星芒
    "deco_sparkle": (512, 512, 1208,
        "a group of scattered tiny sparkling stars and four pointed star "
        "sparkles of different sizes, cyan and white glow, isolated on pure "
        "black background, airy distribution, no big shapes"),
    # 分隔线吊坠：中央菱形宝石 + 两侧渐隐光丝
    "divider_gem": (512, 256, 1209,
        "a small glowing rhombus gem pendant in the center with thin light "
        "threads fading to both sides, cyan to violet gradient, isolated on "
        "pure black background, horizontal symmetric composition"),
    # 登录页粒子层：漂浮光点 + 细小星座线（叠在 login_hero 之上）
    "hero_particles": (768, 800, 1214,
        "abstract floating glowing light particles and a few faint thin "
        "constellation lines in the upper area, tiny sparkles, no books, no "
        "pages, no paper, no objects, isolated on pure black background, "
        "lower area mostly empty"),
    # ====== v0.13 表情符号替代图标（透明小图标，20px 显示） ======
    # 统一：单枚居中、青紫渐变发光、粗轮廓、无文字、黑底提 alpha
    "icon_add": (512, 512, 1301,
        "a single glowing plus sign inside a soft rounded circle badge, "
        "cyan to violet gradient glow, thick clean outline, minimal icon, "
        "no text, isolated on pure black background, centered"),
    "icon_more": (512, 512, 1302,
        "a single glowing gear cog icon, cyan to violet gradient glow, "
        "thick clean outline, minimal flat icon, no text, isolated on pure "
        "black background, centered"),
    "icon_link": (512, 512, 1303,
        "two interlocking glowing circle rings like a chain link, cyan to "
        "violet gradient glow, thick clean outline, minimal icon, no text, "
        "isolated on pure black background, centered"),
    "icon_panel": (512, 512, 1304,
        "a glowing side panel layout icon, a tall rounded rectangle beside a "
        "wide rectangle, cyan to violet gradient glow, thick clean outline, "
        "minimal icon, no text, isolated on pure black background, centered"),
    "icon_net": (512, 512, 1305,
        "a glowing network icon, three small circles connected by thin lines "
        "forming a triangle, cyan to violet gradient glow, thick clean "
        "outline, minimal icon, no text, isolated on pure black background, "
        "centered"),
    "icon_refresh": (512, 512, 1306,
        "a glowing circular refresh arrow icon, one arrow curving in a loop, "
        "cyan to violet gradient glow, thick clean outline, minimal icon, "
        "no text, isolated on pure black background, centered"),
    "icon_bg": (512, 512, 1307,
        "a glowing artist paint palette icon with a few dots, cyan to violet "
        "gradient glow, thick clean outline, minimal icon, no text, isolated "
        "on pure black background, centered"),
    "icon_rank": (512, 512, 1308,
        "a glowing winner podium icon, three small steps with a star above, "
        "cyan to violet gradient glow, thick clean outline, minimal icon, "
        "no text, isolated on pure black background, centered"),
    "icon_import": (512, 512, 1309,
        "a glowing download tray icon, an arrow pointing down into a tray, "
        "cyan to violet gradient glow, thick clean outline, minimal icon, "
        "no text, isolated on pure black background, centered"),
    "icon_save": (512, 512, 1310,
        "a glowing floppy disk save icon, rounded square with a small shutter "
        "slot, cyan to violet gradient glow, thick clean outline, minimal "
        "icon, no text, isolated on pure black background, centered"),
    "icon_edit": (512, 512, 1311,
        "a glowing pencil icon, diagonal pencil with a sharp tip, cyan to "
        "violet gradient glow, thick clean outline, minimal icon, no text, "
        "isolated on pure black background, centered"),
    "icon_del": (512, 512, 1312,
        "a glowing trash bin icon, simple bin with a lid, cyan to violet "
        "gradient glow, thick clean outline, minimal icon, no text, isolated "
        "on pure black background, centered"),
    "icon_avatar": (512, 512, 1313,
        "a glowing person avatar icon, head and shoulders silhouette in a "
        "circle, cyan to violet gradient glow, thick clean outline, minimal "
        "icon, no text, isolated on pure black background, centered"),
    "icon_search": (512, 512, 1314,
        "a glowing magnifying glass icon, circle lens with a short handle, "
        "cyan to violet gradient glow, thick clean outline, minimal icon, "
        "no text, isolated on pure black background, centered"),
    "icon_copy": (512, 512, 1325,
        "two identical rounded square outlines overlapping diagonally like "
        "duplicate stacked layers, front square shifted down right, cyan to "
        "violet gradient glow, thick clean outline, minimal icon, no books, "
        "no pages, no paper, no text, isolated on pure black background, "
        "centered"),
    "icon_slider": (512, 512, 1316,
        "a glowing slider controls icon, a horizontal line with two small "
        "round knobs, cyan to violet gradient glow, thick clean outline, "
        "minimal icon, no text, isolated on pure black background, centered"),
    "icon_star": (512, 512, 1317,
        "a glowing four point sparkle star icon, cyan to violet gradient "
        "glow, thick clean outline, minimal icon, no text, isolated on pure "
        "black background, centered"),

    # ================= v0.62 支付域 =================
    # Vibe Pay 通道徽标：盾牌+粗勾（支付宝蓝辉光家族）
    "icon_vibepay": (512, 512, 1511,
        "a glowing rounded shield badge containing a bold checkmark, soft "
        "alipay blue glow with a few cyan sparkles, thick clean outline, "
        "minimal flat icon, no text, no letters, isolated on pure black "
        "background, centered"),
    # 扫码图标：发光圆角方块 + 抽象点阵 + 四角亮框
    "icon_qr": (512, 512, 1512,
        "a glowing rounded square tile filled with an abstract grid of small "
        "rounded dots like a qr code pattern, four bright corner brackets "
        "around it, cyan to blue glow, thick clean outline, minimal flat "
        "icon, no text, no letters, isolated on pure black background, "
        "centered"),
    # 支付成功插画：夜空下发光礼物盒 + 大对勾 + 星光（用于支付成功状态）
    "pay_success": (1024, 1024, 1513,
        "a warmly glowing gift box on a small pedestal with a big luminous "
        "checkmark badge floating above it, gentle tiny golden and cyan "
        "sparkles like soft fireworks, deep navy night sky background, "
        "calm celebratory mood, centered composition"),

    # ================= v0.63 使用场景卡 =================
    "icon_scene_work": (512, 512, 1521,
        "a glowing briefcase with a small tie icon, rounded simple shapes, "
        "cyan to violet gradient glow, thick clean outline, minimal flat "
        "icon, no text, no letters, isolated on pure black background, "
        "centered"),
    "icon_scene_school": (512, 512, 1522,
        "a glowing graduation cap above an open book icon, rounded simple "
        "shapes, cyan to violet gradient glow, thick clean outline, minimal "
        "flat icon, no text, no letters, isolated on pure black background, "
        "centered"),
    "icon_scene_home": (512, 512, 1523,
        "a glowing little house with a warm heart inside it, rounded simple "
        "shapes, cyan to violet gradient glow, thick clean outline, minimal "
        "flat icon, no text, no letters, isolated on pure black background, "
        "centered"),

    # ================= v0.64 主图标升级 =================
    # 精致版 App 图标：圆角徽章内「一人居中 + 众人环绕」的关系星环。
    # 契合产品核心：人物关系可视化。带天空底（非透明素材），
    # 由 prep 的 rounded_icon 直接裁圆角出 256。
    "app_icon_v2": (1024, 1024, 1531,
        "app icon design, a rounded square badge filling the whole frame, "
        "deep navy night sky background inside the badge, one bright warm "
        "star larger than the others at the very center, six smaller glowing "
        "orbs of light evenly orbiting around it in a perfect circle, each "
        "orb connected to the center star by a thin luminous thread, the "
        "threads forming a delicate mandala of connection, cyan to violet "
        "gradient glow, tiny scattered stars inside the badge, gold warm "
        "accent on the center star, blue pencil children picture book style, "
        "flat illustration, clean centered symmetric composition, simple "
        "shapes, elegant, high quality, no text, no letters, no watermark, "
        "badge edges touching frame edges"),
}


def _post(path, payload):
    req = urllib.request.Request(
        COMFY + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path):
    with urllib.request.urlopen(COMFY + path, timeout=30) as r:
        return r.read()


def build_workflow(name, w, h, seed):
    extra = ASSETS[name][3]
    if "pure black background" in extra:
        # 透明类素材：纯黑底 + 发光主体（后处理提 alpha），不加天空背景描述
        pos = ("flat illustration, blue pencil children picture book style, "
               "cyan and violet soft glow, high quality, " + extra)
    else:
        pos = BASE + ", " + extra
    neg = NEG
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": CKPT}},
        "2": {"class_type": "VAELoader",
              "inputs": {"vae_name": VAE}},
        "3": {"class_type": "LoraLoader",
              "inputs": {"model": ["1", 0], "clip": ["1", 1],
                         "lora_name": LORA,
                         "strength_model": LORA_STRENGTH,
                         "strength_clip": LORA_STRENGTH}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": pos, "clip": ["3", 1]}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"text": neg, "clip": ["3", 1]}},
        "6": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "7": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": 30, "cfg": 6.5,
                         "sampler_name": "dpmpp_2m", "scheduler": "karras",
                         "denoise": 1.0, "model": ["3", 0],
                         "positive": ["4", 0], "negative": ["5", 0],
                         "latent_image": ["6", 0]}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["2", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0],
                         "filename_prefix": "jinbu_ui/%s" % name}},
    }


def wait_queue_free(max_wait_min):
    """等 ComfyUI 队列清空（不打断别人正在跑的任务）。返回剩余秒数。"""
    t0 = time.time()
    while True:
        try:
            q = json.loads(_get("/queue"))
        except Exception as ex:
            print("  [warn] 队列查询失败：%s" % ex)
            return max_wait_min * 60
        n_run = len(q.get("queue_running", []))
        n_pend = len(q.get("queue_pending", []))
        if n_run == 0 and n_pend == 0:
            return max(0, max_wait_min * 60 - (time.time() - t0))
        waited = (time.time() - t0) / 60.0
        if waited >= max_wait_min:
            raise RuntimeError("等待 %.0f 分钟后队列仍忙（running=%d, pending=%d）"
                               % (max_wait_min, n_run, n_pend))
        print("  [wait] ComfyUI 队列忙（running=%d, pending=%d），已等 %.1f 分钟…"
              % (n_run, n_pend, waited))
        time.sleep(30)


def generate(name, poll_timeout=900):
    w, h, seed, _ = ASSETS[name]
    wf = build_workflow(name, w, h, seed)
    pid = _post("/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())})["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < poll_timeout:
        try:
            hist = json.loads(_get("/history/%s" % pid))
        except Exception:
            hist = {}
        if pid in hist and hist[pid].get("outputs"):
            outs = hist[pid]["outputs"]
            for node_out in outs.values():
                for img in (node_out.get("images") or []):
                    if img.get("type") == "output":
                        return img["filename"], img.get("subfolder", "")
        time.sleep(3)
    raise RuntimeError("%s 生成超时（%ds）" % (name, poll_timeout))


def download(filename, subfolder, dest):
    q = "?filename=%s&type=output" % urllib.request.quote(filename)
    if subfolder:
        q += "&subfolder=%s" % urllib.request.quote(subfolder)
    data = _get("/view" + q)
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="",
                    help="逗号分隔的资产名（默认全部）")
    ap.add_argument("--wait", type=int, default=45,
                    help="队列忙时最长等待分钟数（默认 45）")
    args = ap.parse_args()

    names = [n.strip() for n in args.only.split(",") if n.strip()] \
        or list(ASSETS.keys())
    for n in names:
        if n not in ASSETS:
            print("未知资产：%s（可用：%s）" % (n, ", ".join(ASSETS.keys())))
            sys.exit(2)

    os.makedirs(OUT_DIR, exist_ok=True)
    print("== 等待 ComfyUI 队列清空 ==")
    wait_queue_free(args.wait)

    ok, fail = [], []
    for n in names:
        w, h, seed, _ = ASSETS[n]
        dest = os.path.join(OUT_DIR, "%s.png" % n)
        if os.path.exists(dest):
            print("[skip] %s 已存在（先删除再重生）" % n)
            ok.append(n)
            continue
        print("[gen ] %s  %dx%d  seed=%d …" % (n, w, h, seed))
        try:
            t0 = time.time()
            fn, sub = generate(n)
            size = download(fn, sub, dest)
            print("[ok  ] %s -> %s（%d 字节，%.0fs）"
                  % (n, dest, size, time.time() - t0))
            ok.append(n)
        except Exception as ex:
            print("[FAIL] %s：%s" % (n, ex))
            fail.append(n)
        # 每张之间让 GPU 喘口气
        time.sleep(2)

    print("\n== 完成：成功 %d，失败 %d ==" % (len(ok), len(fail)))
    if fail:
        print("失败：%s" % ", ".join(fail))
        sys.exit(1)


if __name__ == "__main__":
    main()
