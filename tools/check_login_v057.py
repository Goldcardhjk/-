"""登录页多账号渲染自检：真实 Tk 窗口截图后自动关闭。"""
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path("D:/进步壹号/进步壹号Agent")
OUT = ROOT / "_tmp/v057/login_accounts.png"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import login
from shot import grab

real_loop = tk.Misc.mainloop
errors = []


def fake_mainloop(self, n=0):
    def capture():
        try:
            self.update_idletasks()
            self.update()
            grab(self.winfo_id(), str(OUT))
        except BaseException as exc:
            errors.append(repr(exc))
        finally:
            for job in self.tk.splitlist(self.tk.call("after", "info")):
                self.after_cancel(job)
            self.destroy()

    self.after(900, capture)
    real_loop(self, n)


tk.Misc.mainloop = fake_mainloop
try:
    picked = login.run_login([
        {"slug": "a", "name": "工作账号", "avatar": "", "created": ""},
        {"slug": "b", "name": "生活账号", "avatar": "", "created": ""},
    ])
finally:
    tk.Misc.mainloop = real_loop
print({"screenshot": str(OUT), "exists": OUT.is_file(), "picked": picked, "errors": errors}, flush=True)
sys.exit(0 if OUT.is_file() and not errors else 1)
