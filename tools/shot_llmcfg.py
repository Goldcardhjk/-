# -*- coding: utf-8 -*-
"""v0.18 定位辅助：打开大模型 API 配置窗 → 定时截图 → 自动关闭"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import tkinter as tk
import db
db.init_db()

from ui import App
from tools.shot import grab


def main():
    app = App()
    app.update()
    app.refresh_network()
    app.update()
    app.nb.select(app.tab_greet)
    app.update()

    dlg = {}

    def _shot():
        for w in app.winfo_children():
            if isinstance(w, tk.Toplevel):
                dlg["w"] = w
        w = dlg.get("w")
        if w is not None:
            try:
                grab(w.winfo_id(), "_v018_llmcfg.png")
                print("LLMCFG-SHOT ok")
            except Exception as e:
                print("LLMCFG-SHOT-ERR:", e)
            app.after(300, app.destroy)

    app.after(600, _shot)
    app.open_llm_config()
    app.mainloop()          # open_llm_config 非阻塞，靠 mainloop 撑到截图完成


if __name__ == "__main__":
    main()
