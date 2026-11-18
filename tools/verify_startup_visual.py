# -*- coding: utf-8 -*-
"""隔离的可视启动检查：仅在加载阶段停留数秒，便于人工/截图核验。"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_tmp" / "v069" / "startup_visual_data"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["JINBU_DATA_DIR"] = str(OUT)
os.environ["JINBU_DB_PATH"] = str(OUT / "agent.db")

import ui

# 功能顺序由 verify_startup.py 验收；此脚本只保留真实加载页用于视觉检查。
ui.App._build_ui = lambda self: None
ui.App._enter_main = lambda self: None
app = ui.App()
app.geometry("1100x720+80+60")
app._startup_progress("正在构建关系首页…", 68)
timer = getattr(app, "_startup_timer", None)
if timer is not None:
    app.after_cancel(timer)
app.mainloop()
