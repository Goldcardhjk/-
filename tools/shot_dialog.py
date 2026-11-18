# -*- coding: utf-8 -*-
"""v0.16 深色模态对话框截图验证：触发 _info → 定时截图 → 自动关闭"""
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

    def _find_dialog():
        for w in app.winfo_children():
            if isinstance(w, tk.Toplevel):
                return w
        return None

    def _shot():
        w = _find_dialog()
        if w is not None:
            try:
                grab(w.winfo_id(), "_v016_dialog.png")
                print("DIALOG-SHOT ok")
            except Exception as e:
                print("DIALOG-SHOT-ERR:", e)
            app.after(300, w.destroy)

    app.after(500, _shot)
    r = app._info("保存成功",
                  "人物资料已保存到本地数据库（D 盘 SQLite）。\n"
                  "关系网络与评分已同步刷新。")
    app.destroy()
    print("dialog result:", r)


if __name__ == "__main__":
    main()
