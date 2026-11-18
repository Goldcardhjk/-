# -*- coding: utf-8 -*-
"""v0.20 定制资产生成（ComfyUI）

用户反馈（截图五连之三）：
- 「近期提醒」「网络概览」标题没有专门的资产 → 定制两枚发光标题图标
- 人脉卡片页太丑，「可以给他定制资产」→ 定制卡面星芒角标

产物落 images/ui_raw/，后处理由 prep_v020.py 完成（黑底提 alpha → 40px）。

用法：.venv/Scripts/python.exe tools/gen_v020.py
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

# name -> (w, h, seed, prompt)
ASSETS = {
    # 近期提醒标题图标：发光闹钟 + 提醒波纹圈
    "icon_remind": (512, 512, 2001,
        "a single glowing alarm clock icon with two small ringing waves above "
        "the bell, cyan glow outline, rounded simple shapes, thick clean "
        "outline, minimal flat icon, no text, isolated on pure black "
        "background, centered"),
    # 网络概览标题图标：发光仪表盘 + 上升迷你柱状条
    "icon_overview": (512, 512, 2002,
        "a single glowing dashboard gauge icon, a semicircular dial with a "
        "needle and three small rising bars beside it, orange to amber glow "
        "outline, rounded simple shapes, thick clean outline, minimal flat "
        "icon, no text, isolated on pure black background, centered"),
    # 人脉卡片页卡面角标：右上角星芒 + 柔光点缀（贴在玻璃卡角落）
    "card_deco_star": (512, 512, 2003,
        "a single four point sparkle star with a small companion star and a "
        "few tiny dots, cyan to violet gradient glow, delicate thin lines, "
        "minimal decorative corner ornament, positioned in the upper right "
        "corner, other areas empty, no text, isolated on pure black "
        "background"),
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
    pos = ("flat illustration, blue pencil children picture book style, "
           "cyan and violet soft glow, high quality, " + ASSETS[name][3])
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
              "inputs": {"text": NEG, "clip": ["3", 1]}},
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
    t0 = time.time()
    while True:
        try:
            q = json.loads(_get("/queue"))
        except Exception as ex:
            print("  [warn] 队列查询失败：%s" % ex)
            return
        n_run = len(q.get("queue_running", []))
        n_pend = len(q.get("queue_pending", []))
        if n_run == 0 and n_pend == 0:
            return
        waited = (time.time() - t0) / 60.0
        if waited >= max_wait_min:
            raise RuntimeError("等待 %.0f 分钟后队列仍忙" % max_wait_min)
        print("  [wait] 队列忙（running=%d, pending=%d），已等 %.1f 分钟…"
              % (n_run, n_pend, waited))
        time.sleep(20)


def generate(name, poll_timeout=600):
    w, h, seed, _ = ASSETS[name]
    wf = build_workflow(name, w, h, seed)
    pid = _post("/prompt", {"prompt": wf,
                            "client_id": str(uuid.uuid4())})["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < poll_timeout:
        try:
            hist = json.loads(_get("/history/%s" % pid))
        except Exception:
            hist = {}
        if pid in hist and hist[pid].get("outputs"):
            for node_out in hist[pid]["outputs"].values():
                for img in (node_out.get("images") or []):
                    if img.get("type") == "output":
                        fn = img["filename"]
                        data = _get("/view?filename=%s&subfolder=%s&type=%s"
                                    % (fn, img.get("subfolder", ""),
                                       img["type"]))
                        out = os.path.join(OUT_DIR, fn)
                        os.makedirs(OUT_DIR, exist_ok=True)
                        with open(out, "wb") as f:
                            f.write(data)
                        print("  [ok] %s -> %s (%d bytes)"
                              % (name, out, len(data)))
                        return out
        time.sleep(3)
    raise RuntimeError("生成超时：%s" % name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--wait", type=float, default=30)
    a = ap.parse_args()
    names = ([n.strip() for n in a.only.split(",") if n.strip()]
             or list(ASSETS))
    os.makedirs(OUT_DIR, exist_ok=True)
    for n in names:
        print("[gen] %s …" % n)
        wait_queue_free(a.wait)
        generate(n)
    print("全部完成：%s" % ", ".join(names))


if __name__ == "__main__":
    main()
