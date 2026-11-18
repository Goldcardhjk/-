# -*- coding: utf-8 -*-
"""v0.18 专项复现：关系网连接药丸文字（image#4 无字胶囊）

用法：.venv/Scripts/python.exe tools/shot_linkpill.py
临时把唯一连接的分数改成 70（琥珀色，贴近用户截图配色），
进关系网络页截图后分析药丸区域是否有文字像素，最后恢复 50。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from shot import grab


def main():
    db.init_db()
    conn = db.get_conn()
    conn.execute("UPDATE links SET score=70 WHERE id=1")
    conn.commit()
    conn.close()

    import tkinter as tk
    out = "_v018_linkpill.png"
    result = {"size": None}
    _real_mainloop = tk.Misc.mainloop

    def fake_mainloop(self):
        self.nb.select(self.tab_net)
        self.update()

        def _job():
            try:
                result["size"] = grab(self.winfo_id(), out)
            except Exception as e:
                print("SHOT-ERR:", e)
            self.destroy()
        self.after(1400, _job)
        _real_mainloop(self)

    tk.Misc.mainloop = fake_mainloop
    from ui import App
    app = App()
    app.mainloop()
    print("SHOT:", result["size"], out)

    # 恢复
    conn = db.get_conn()
    conn.execute("UPDATE links SET score=50 WHERE id=1")
    conn.commit()
    conn.close()

    # 分析：找药丸（#0E1622 填充 + 琥珀描边），检查内部文字像素
    from PIL import Image
    im = Image.open(out).convert("RGB")
    w, h = im.size
    px = im.load()
    from collections import Counter
    # 全图找 #0E1622 像素团
    dark = [(x, y) for y in range(h) for x in range(w)
            if px[x, y] == (14, 22, 34)]
    print("dark px total:", len(dark))
    if dark:
        xs = [p[0] for p in dark]
        ys = [p[1] for p in dark]
        print("dark bbox:", min(xs), min(ys), max(xs), max(ys))
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        # 药丸内部（去边缘 4px）的琥珀像素 = 文字
        amber_in = 0
        for y in range(y0 + 4, y1 - 3):
            for x in range(x0 + 4, x1 - 3):
                r, g, b = px[x, y]
                if r > 150 and 100 < g < 200 and b < 140 and r - b > 60:
                    amber_in += 1
        print("amber text px inside pill:", amber_in)
        im.crop((max(0, x0 - 30), max(0, y0 - 30),
                 min(w, x1 + 30), min(h, y1 + 30))).save("_crop_linkpill.png")
        print("crop saved: _crop_linkpill.png")


if __name__ == "__main__":
    main()
