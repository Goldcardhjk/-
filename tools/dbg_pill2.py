# -*- coding: utf-8 -*-
"""v0.18 调试：dump _cached_photo 实际渲染出的 PIL 图"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import images_util

_orig_cached = images_util._cached_photo


def _patched_cached(key, render):
    pil = render()
    if isinstance(key, tuple) and key and key[0] == "pill":
        fn = f"_dbg_pill_{key[1]}_{key[2]}_{key[5]}.png".replace(" ", "_")
        if pil is not None:
            pil.save(fn)
            # 统计文字色像素
            amber = sum(
                1 for y in range(pil.height) for x in range(pil.width)
                if (lambda c: c[0] > 150 and 100 < c[1] < 200 and c[2] < 140
                    and c[0] - c[2] > 60)(pil.getpixel((x, y))))
            print("PILL-RENDER", fn, pil.size, "amber px:", amber)
        else:
            print("PILL-RENDER None", key)
    return _orig_cached(key, lambda: pil)


images_util._cached_photo = _patched_cached

import tkinter as tk
_real_mainloop = tk.Misc.mainloop


def fake_mainloop(self):
    self.nb.select(self.tab_net)
    self.update()

    def _job():
        self.destroy()
    self.after(1500, _job)
    _real_mainloop(self)


tk.Misc.mainloop = fake_mainloop
import ui
app = ui.App()
app.mainloop()
print("done")
