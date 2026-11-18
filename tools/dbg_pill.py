# -*- coding: utf-8 -*-
"""v0.18 调试：dump smooth_pill 实际产出的图片"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import images_util

_orig = images_util.smooth_pill
_dump_n = [0]


def _patched(text, fg, fill="#0E1622", outline=None, size=9):
    ph = _orig(text, fg, fill=fill, outline=outline, size=size)
    _dump_n[0] += 1
    print(f"PILL-CALL n={_dump_n[0]} text={text!r} fg={fg} size={size} "
          f"-> {'None' if ph is None else (ph.width(), ph.height())}")
    return ph


images_util.smooth_pill = _patched
import ui
ui.images_util.smooth_pill = _patched

import tkinter as tk
out = "_v018_linkpill.png"
_real_mainloop = tk.Misc.mainloop


def fake_mainloop(self):
    self.nb.select(self.tab_net)
    self.update()

    def _job():
        # 列出画布上 linkpill item 用的图片尺寸
        c = self.web_canvas
        for it in c.find_withtag("linkpill"):
            print("LINKPILL item", it, "coords", c.coords(it),
                  "img size", (c.itemcget(it, "image") or "")[:40])
        self.destroy()
    self.after(1500, _job)
    _real_mainloop(self)


tk.Misc.mainloop = fake_mainloop
app = ui.App()
app.mainloop()
print("done, pill calls:", _dump_n[0])
