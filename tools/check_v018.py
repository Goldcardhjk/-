# -*- coding: utf-8 -*-
"""v0.18 功能验证：模型选择器 + 分段祝福模型 + 连接器宽度同步

用法：.venv/Scripts/python.exe tools/check_v018.py
不改动真实 llm_config.json（快照→跑→还原/删除）。
"""
import os
import sys
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
db.init_db()

import llm
CFG = llm.CONFIG_PATH
_backup = None
if os.path.exists(CFG):
    _backup = CFG + ".v018bak"
    shutil.copy2(CFG, _backup)

FAIL = []


def check(name, cond, extra=""):
    print(("  [ok] " if cond else "  [FAIL] ") + name +
          (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(name)


import tkinter as tk
from ui import App

app = App()
app.geometry("1600x920+4000+4000")
app.update()

# ---------- ① 顶栏模型选择器 ----------
check("顶栏模型下拉存在且有候选",
      hasattr(app, "gr_model") and len(app.gr_model["values"]) >= 5,
      getattr(app, "gr_model", None) and len(app.gr_model["values"]))
app.gr_model.set("hunyuan-standard")
app._gr_on_model()
check("切换全局模型写配置", llm.load_config().get("model") == "hunyuan-standard",
      llm.load_config().get("model"))
check("模式标签含模型与成本",
      "hunyuan-standard" in app.gr_mode.cget("text")
      and "中成本" in app.gr_mode.cget("text"),
      app.gr_mode.cget("text"))

# ---------- ② 分段祝福模型 ----------
check("四层分段下拉齐全", len(app._gr_tier_cbs) == 4,
      list(app._gr_tier_cbs))
cb, cost = app._gr_tier_cbs["核心"]
cb.set("hunyuan-turbos-latest")
app._gr_on_tier_model("核心")
tm = llm.load_config().get("greet_tier_models") or {}
check("核心层模型写配置", tm.get("核心") == "hunyuan-turbos-latest", tm)
check("核心层成本提示刷新", "中成本" in cost.cget("text"), cost.cget("text"))

import greetings
check("model_for_tier(核心) 取分段模型",
      greetings.model_for_tier("核心") == "hunyuan-turbos-latest",
      greetings.model_for_tier("核心"))
check("model_for_tier(浅层) 回退全局模型",
      greetings.model_for_tier("浅层") == "hunyuan-standard",
      greetings.model_for_tier("浅层"))

# craft_one 按层级走分段模型（mock 模式不发起请求，只验证不炸）
pid = next(iter(app.person_map.values()))
text, src = greetings.craft_one(pid, "春节")
check("craft_one 默认按分层模型跑通", bool(text), src)

app._gr_tier_reset()
tm2 = llm.load_config().get("greet_tier_models") or {}
check("全部统一清空分段配置", not tm2, tm2)
check("重置后核心下拉回跟随全局",
      app._gr_tier_cbs["核心"][0].get() == greetings.FOLLOW_GLOBAL)

# ---------- ③ 连接器页宽度同步 ----------
check("conn_canvas 存在且内框已同步宽度",
      hasattr(app, "conn_canvas") and hasattr(app, "conn_grid"))
app.nb.select(app.tab_conn)
app.update()
cw = app.conn_canvas.winfo_width()
gw = app.conn_grid.winfo_width()
check("连接器内框宽度 ≈ 画布宽度（右侧无空白）",
      abs(cw - gw) <= 2, "canvas=%d grid=%d" % (cw, gw))

# ---------- ④ 横幅无图标 / API 窗无帮助文字 ----------
import inspect
src_ui = inspect.getsource(app._page_banner)
check("_page_banner 不再叠加图标", "icl.place" not in src_ui)
src_cfg = inspect.getsource(app.open_llm_config)
check("API 配置窗无帮助文字段", "help_lbl" not in src_cfg)

# ---------- ⑤ 胶囊文字渲染（image#4 修复） ----------
# 拦截 _cached_photo 拿到实际渲染的 PIL 图（超采样画布文字必须同倍放大，
# 否则缩回后文字只剩 1/4——用户截图「连接标签里写的字看不见」）
import images_util
from PIL import Image
_captured = {}
_orig_cached = images_util._cached_photo


def _cap(key, render):
    pil = render()
    if isinstance(key, tuple) and key and key[0] == "pill":
        _captured["pill"] = pil
    return _orig_cached(key, lambda: pil)


images_util._cached_photo = _cap
images_util.smooth_pill("引荐 70", "#C79761", size=9)
images_util._cached_photo = _orig_cached
pil = _captured.get("pill")
check("smooth_pill 渲染出 PIL 图", pil is not None and pil.width > 30,
      pil and pil.size)
if pil is not None:
    pil = pil.convert("RGB")
    # 文字像素 = 中间区域明显亮于填充底(14,22,34)的像素（抗锯齿边缘也算）
    n_text = sum(1 for y in range(3, pil.height - 3)
                 for x in range(6, pil.width - 6)
                 if pil.getpixel((x, y))[0] > 60 and pil.getpixel((x, y))[1] > 45)
    check("修复后胶囊文字像素可见（>40）", n_text > 40, n_text)
    pil.save("_dbg_fixed_9.png")

app.destroy()

# ---------- 还原配置 ----------
if _backup:
    shutil.move(_backup, CFG)
elif os.path.exists(CFG):
    os.remove(CFG)

print()
print("===== v0.18 功能验证：%d 通过 / %d 失败 =====" % (
    len([f for f in FAIL]) == 0 and (13 - len(FAIL)) or 0, len(FAIL)))
print("FAILED:", FAIL if FAIL else "无")
sys.exit(1 if FAIL else 0)
