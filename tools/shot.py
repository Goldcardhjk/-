# -*- coding: utf-8 -*-
"""全点位截图工具（v0.14 起复用）

用法：.venv/Scripts/python.exe tools/shot.py <页面> [输出.png]
页面：home / cards / gift / greet / conn / login

原理（v0.12 踩坑沉淀）：
- 子进程内建 App（ui.App 自含 mainloop 调用由 patch tk.Misc.mainloop 拦截）
- PrintWindow 截图：Win32 API 必须显式 argtypes（64 位句柄截断问题）；
  BITMAPINFOHEADER.biCompression 必须保持 0（误设 32 会返回 0 行全黑且无报错）；
  正 height + FLIP_TOP_BOTTOM 翻转；PW_RENDERFULLCONTENT=2
"""
import sys
import os
import ctypes
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def grab(hwnd, out_path):
    """PrintWindow 截取指定窗口为 PNG"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    PW_RENDERFULLCONTENT = 2
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int,
                                             ctypes.c_int]
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HBITMAP]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]
    rc = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    w, h = rc.r - rc.l, rc.b - rc.t
    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)
    ok = user32.PrintWindow(hwnd, memdc, PW_RENDERFULLCONTENT)
    if not ok:
        raise RuntimeError("PrintWindow failed")

    class BI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]
    bi = BI()
    bi.biSize = ctypes.sizeof(BI)
    bi.biWidth, bi.biHeight, bi.biPlanes = w, h, 1
    bi.biBitCount, bi.biCompression = 32, 0     # BI_RGB，必须保持 0
    stride = w * 4
    buf = ctypes.create_string_buffer(stride * h)
    rows = gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    if rows == 0:
        raise RuntimeError("GetDIBits returned 0 rows (black image risk)")
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, memdc)

    from PIL import Image
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", stride, 1)
    img = img.transpose(1)                      # FLIP_TOP_BOTTOM
    img.save(out_path)
    return img.size


def main():
    page = sys.argv[1] if len(sys.argv) > 1 else "home"
    out = sys.argv[2] if len(sys.argv) > 2 else f"_v014_{page}.png"
    import tkinter as tk
    import db
    db.init_db()
    result = {"size": None}
    _real_mainloop = tk.Misc.mainloop     # 保留真 mainloop：调度后继续跑事件循环

    def shoot_then_close(win, delay=1100):
        def _job():
            try:
                result["size"] = grab(win.winfo_id(), out)
            except Exception as e:
                print("SHOT-ERR:", e)
            win.destroy()
        win.after(delay, _job)

    if page == "login":
        import login

        def fake_mainloop(self):
            shoot_then_close(self, 1400)
            _real_mainloop(self)
        tk.Misc.mainloop = fake_mainloop
        login.run_login()
        print("LOGIN-SHOT:", result["size"], out)
        return

    from ui import App

    def fake_mainloop(self):
        tabs = {"home": "tab_net", "cards": "tab_cards", "gift": "tab_gift",
                "greet": "tab_greet", "conn": "tab_conn"}
        if page == "gift_data":
            # 有数据状态的送礼页：选人出推荐 + 造一条临时订单（截图后自删）
            from gifts import schedule_and_pay
            names = list(self.person_map.keys())
            if names:
                self.g_person.set(names[0])
                self.on_gift_person()
                oid, _ = schedule_and_pay(self.person_map[names[0]], 1,
                                          "验证礼物", "2026-11-01", 299)
                self.refresh_orders()
            self.nb.select(self.tab_gift)
        elif page == "greet_mass":
            # v0.17：群发中间态——全选 + 开跑，写到一半时截图
            self.nb.select(self.tab_greet)
            self.update()
            self._gr_sel_all()
            self.gr_holiday.set("春节")
            self.mass_greeting()
            shoot_then_close(self, 900)       # 约 2 位完成时按下快门
            if page == "greet_mass":
                _real_mainloop(self)
            return
        elif page in tabs:
            self.nb.select(getattr(self, tabs[page]))
        self.update()
        shoot_then_close(self)
        if page == "gift_data":
            # 截图后清理临时订单（_job 在 destroy 前跑，这里 after 到销毁后执行）
            def _cleanup():
                try:
                    import db as _db
                    conn = _db.get_conn()
                    conn.execute(
                        "DELETE FROM gift_orders WHERE gift_name='验证礼物'")
                    conn.commit(); conn.close()
                except Exception:
                    pass
            self.after(50, _cleanup)
        _real_mainloop(self)
    tk.Misc.mainloop = fake_mainloop
    app = App()
    app.mainloop()          # 走 fake_mainloop：截图后自动销毁
    print("SHOT:", result["size"], out)


if __name__ == "__main__":
    main()
