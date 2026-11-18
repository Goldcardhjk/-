# -*- coding: utf-8 -*-
"""智能送礼页 mix-in（v0.63 从 ui.py 机械拆出，方法体原样未动）"""
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import json
import math
import os
import random
import queue
import time

import db
import scoring
from scoring import rank_all, compute_score, days_to_next_event
from gifts import recommend, schedule_and_pay
import greetings
from greetings import generate, HOLIDAY_TEMPLATES, craft_one
import wechat_sandbox
import images_util
import llm
import payment
import netlayout
from relationship_view import build_relationship_view

# 统一导入 ui_base 的主题常量与部件（星导含全部非下划线名；
# 下划线工具函数按需显式导入，保证方法体引用原样可用）
from ui_base import *                                   # noqa: F401,F403
from ui_base import (_mix, _rounded, _round_pts, _pick_icon,   # noqa: F401
                     _wrap_by_width, _score_color, clean_note,
                     _f, _split, _parse_imp)


class GiftPageMixin:
    def build_gifting(self):
        top = tk.Frame(self.tab_gift, bg=BG); top.pack(fill="x", padx=10, pady=8)
        self._page_banner(top, "banner_gift")
        self._label(top, "为谁选礼：", fg=TEXT).pack(side="left")
        self.g_person = ttk.Combobox(top, width=18, font=FONT, style="Dark.TCombobox", state="readonly")
        self.g_person.pack(side="left", padx=6)
        self.g_person.bind("<<ComboboxSelected>>", self.on_gift_person)
        self._btn(top, "刷新", self.refresh_combos,
                  icon="icon_refresh").pack(side="left")

        # v0.15：双栏布局——左：推荐礼物 + 多商家比价；右：下单卡 + 最近订单
        body = tk.Frame(self.tab_gift, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        lcol = tk.Frame(body, bg=BG)
        lcol.pack(side="left", fill="both", expand=True, padx=(0, 10))
        rcol = tk.Frame(body, bg=BG, width=430)
        rcol.pack(side="right", fill="y")
        rcol.pack_propagate(False)

        self._label(lcol, "推荐礼物（按喜好匹配 + 性价比排序）", fg=CYAN,
                    font=FONT_MID).pack(anchor="w")
        cols = ("礼物", "类别", "到手价", "匹配", "性价比")
        self.gift_tree = ttk.Treeview(lcol, columns=cols, show="headings", height=9)
        for c, w in zip(cols, (220, 110, 90, 70, 80)):
            self.gift_tree.heading(c, text=c)
            self.gift_tree.column(c, width=w, anchor="center",
                                  stretch=(c == "礼物"))
        self.gift_tree.pack(fill="x", pady=6)
        self.gift_tree.bind("<<TreeviewSelect>>", self.on_gift_select)
        # v0.14：空状态覆盖提示（选中人物后隐藏）
        self.gift_hint = tk.Label(
            lcol, text="先在上方选择人物\n按喜好匹配 + 性价比自动推荐礼物",
            bg=SURFACE, fg=MUTED, font=FONT_SM, justify="center")
        self.gift_hint.place(in_=self.gift_tree, relx=0.5, rely=0.46,
                             anchor="c")
        self.gift_hint.lift()

        price_head = tk.Frame(lcol, bg=BG)
        price_head.pack(fill="x", pady=(12, 0))
        self._label(price_head, "多商家比价", fg=CYAN, font=FONT_MID).pack(side="left")
        self._label(price_head, "演示价 · 商家与价格均为模拟数据", fg=MUTED,
                    font=FONT_XS).pack(side="left", padx=(8, 0))
        self.g_price = scrolledtext.ScrolledText(lcol, height=8, bg=SURFACE,
                                                 fg=TEXT,
                                                 insertbackground=CYAN, font=FONT,
                                                 bd=0,
                                                 highlightthickness=1,
                                                 highlightbackground=BORDER)
        self.g_price.pack(fill="both", expand=True, pady=(6, 0))

        # 右栏上：下单卡（圆角玻璃卡承载表单）
        ocv, ocard = rounded_card(rcol, pad=12, parent_bg=BG)
        ocv.pack(fill="x", pady=(0, 12))
        self._label(ocard, "下单", fg=ORANGE, font=FONT_MID,
                    icon="icon_gift").pack(anchor="w")
        row = tk.Frame(ocard, bg=SURFACE)
        row.pack(fill="x", pady=(10, 0))
        self._label(row, "送达日期：", fg=TEXT).pack(side="left")
        self.g_date = self._entry(row, 12, FONT)
        self.g_date.pack(side="left", padx=6)
        # v0.62 支付域：通道可选。默认模拟（不扣真实资金）；
        # 选 Vibe Pay 走官方网页收款（收银台链接 → 浏览器付款 → 轮询确认到账）。
        chan_row = tk.Frame(ocard, bg=SURFACE)
        chan_row.pack(fill="x", pady=(8, 0))
        self._label(chan_row, "付款通道：", fg=TEXT).pack(side="left")
        self.g_channel = ttk.Combobox(
            chan_row, width=16, font=FONT_SM, style="Dark.TCombobox",
            state="readonly",
            values=[payment.CHANNEL_LABELS["mock"],
                    payment.CHANNEL_LABELS["vibe"]])
        self.g_channel.set(payment.CHANNEL_LABELS["mock"])
        self.g_channel.pack(side="left", padx=4)
        self.g_channel.bind("<<ComboboxSelected>>", self._on_pay_channel)
        self.pay_mode = "mock"
        pay_row = tk.Frame(ocard, bg=SURFACE)
        pay_row.pack(fill="x", pady=(8, 0))
        self.pay_btn = self._btn(pay_row, "模拟一键付款", self.do_pay, "primary",
                                 icon="icon_gift")
        self.pay_btn.pack(side="left")
        self.pay_hint_lbl = self._label(pay_row, "演示不扣款", fg=MUTED,
                                        font=FONT_XS)
        self.pay_hint_lbl.pack(side="left", padx=8)
        self._label(ocard,
                    "模拟通道不碰真实资金；选 Vibe Pay 在浏览器打开支付宝收银台，"
                    "到账自动确认（更多菜单 → Vibe Pay 支付配置）。",
                    fg=MUTED, font=FONT_XS).pack(anchor="w", pady=(10, 0))

        # 右栏下：最近订单（列宽适配 430 栏：总宽 ≤ 400，礼物列 stretch 补足）
        self._label(rcol, "最近订单", fg=CYAN, font=FONT_MID).pack(anchor="w")
        ocols = ("ID", "人物", "礼物", "金额", "日期", "状态")
        self.order_tree = ttk.Treeview(rcol, columns=ocols, show="headings", height=5)
        for c, w in zip(ocols, (36, 64, 78, 52, 80, 78)):
            self.order_tree.heading(c, text=c)
            self.order_tree.column(c, width=w, anchor="center",
                                   stretch=(c == "礼物"))
        self.order_tree.pack(fill="x", pady=(6, 0))
        # v0.14：空状态提示（有订单后隐藏）
        self.order_hint = tk.Label(rcol, text="暂无订单 · 选礼物后点「一键付款」下单",
                                   bg=SURFACE, fg=MUTED, font=FONT_SM)
        self.order_hint.place(in_=self.order_tree, relx=0.5, rely=0.5,
                              anchor="c")
        self.order_hint.lift()
        self.refresh_orders()

    def on_gift_person(self, evt=None):
        name = self.g_person.get()
        pid = self.person_map.get(name)
        if not pid: return
        p = db.get_person(pid)
        self.gift_reco = recommend(p)
        self.gift_tree.delete(*self.gift_tree.get_children())
        for r in self.gift_reco:
            self.gift_tree.insert("", "end", values=(r["gift"]["name"], r["gift"]["category"],
                                    f"¥{r['best_price']}", r["match"], r["cost_perf"]))
        # v0.14：有推荐 → 隐藏空态提示；无推荐 → 恢复显示
        if self.gift_reco:
            self.gift_hint.place_forget()
        else:
            self.gift_hint.place(in_=self.gift_tree, relx=0.5, rely=0.46,
                                 anchor="c")
            self.gift_hint.lift()
        self.g_price.delete("1.0", tk.END)
        self.g_price.insert(tk.END, f"收货地址：{p.get('address') or '（未填）'}\n")
        self.g_price.insert(tk.END, "选择上方礼物查看比价 →")

    def on_gift_select(self, evt):
        sel = self.gift_tree.selection()
        if not sel or not self.gift_reco: return
        r = self.gift_reco[self.gift_tree.index(sel[0])]
        p = db.get_person(self.person_map.get(self.g_person.get()))
        self.g_price.delete("1.0", tk.END)
        self.g_price.insert(tk.END, f"收货地址：{p.get('address') or '（未填）'}\n")
        self.g_price.insert(tk.END, f"【{r['gift']['name']}】多商家比价：\n")
        for mp in r["merchant_prices"]:
            mark = "  ← 最低" if mp["price"] == r["best_price"] else ""
            self.g_price.insert(tk.END, f"  {mp['merchant']}: ¥{mp['price']}{mark}\n")
        self.g_price.insert(tk.END, f"\n到手价：¥{r['best_price']}（已选最低渠道）")

    def _on_pay_channel(self, evt=None):
        """v0.62：通道切换——按钮文案与提示跟随通道；真实通道未配置时引导配置。"""
        sel = self.g_channel.get()
        if sel == payment.CHANNEL_LABELS["vibe"]:
            self.pay_mode = "vibe"
            self._btn_text(self.pay_btn, "Vibe Pay 付款")
            self.pay_hint_lbl.config(text="支付宝官方网页收款", fg=ORANGE)
            ok, why = payment.VibePayChannel().ready()
            if not ok:
                self.status_var.set(
                    "Vibe Pay 未就绪（%s）——点「更多 → Vibe Pay 支付配置」填密钥" % why)
            else:
                self.status_var.set("Vibe Pay 已就绪：付款打开支付宝收银台，到账自动确认")
        else:
            self.pay_mode = "mock"
            self._btn_text(self.pay_btn, "模拟一键付款")
            self.pay_hint_lbl.config(text="演示不扣款", fg=MUTED)

    def do_pay(self):
        sel = self.gift_tree.selection()
        if not sel or not self.gift_reco:
            self._warn("提示", "请先选择人物与礼物"); return
        name = self.g_person.get()
        pid = self.person_map.get(name)
        r = self.gift_reco[self.gift_tree.index(sel[0])]
        sdate = self.g_date.get().strip() or "未排期"
        if getattr(self, "pay_mode", "mock") == "vibe":
            self._vibe_pay_flow(pid, name, r, sdate)
            return
        oid, status = schedule_and_pay(pid, r["gift"]["id"], r["gift"]["name"], sdate, r["best_price"])
        self._info("模拟付款成功",
            f"订单 #{oid} 已生成\n收礼人：{name}\n礼物：{r['gift']['name']}\n"
            f"金额：¥{r['best_price']}\n状态：{status}（MVP 为模拟，不扣真实资金）")
        self.refresh_orders()

    # ---------- v0.62 支付域 → v0.67 对齐官方 Vibe Pay（网页/桌面收款） ----------
    def _vibe_pay_flow(self, pid, name, reco, sdate):
        """真实通道：建单(待付款) → 生成收银台链接并打开浏览器 → 轮询到账 → 落库。"""
        chan = payment.VibePayChannel()
        ok, why = chan.ready()
        if not ok:
            self._warn("Vibe Pay 未就绪",
                       "%s。\n\n点「更多 → Vibe Pay 支付配置」填好 APPID、"
                       "应用私钥与支付宝公钥后再试。" % why)
            return
        oid = db.create_order(pid, reco["gift"]["id"], reco["gift"]["name"],
                              sdate, reco["best_price"], "", channel="vibe")
        out_trade_no = payment.VibePayChannel.make_out_trade_no(oid)
        subject = "进步壹号送礼：%s %s" % (reco["gift"]["name"], name)
        try:
            intent = chan.start_payment(out_trade_no, reco["best_price"], subject)
        except payment.PaymentError as ex:
            db.update_order_status(oid, "下单失败")
            self._warn("Vibe Pay 生成支付链接失败", str(ex))
            self.refresh_orders()
            return
        db.update_order_status(oid, "等待买家付款")
        self.status_var.set(
            "Vibe Pay 订单 #%d 已生成，支付宝收银台已在浏览器打开" % oid)
        self._open_pay_window(oid, name, reco, intent, chan)
        self.refresh_orders()

    def _open_pay_window(self, oid, name, reco, intent, chan):
        """支付监视窗：浏览器收银台 + 本窗轮询到账；非模态不阻塞操作。

        v0.67 按官方 Vibe Pay 网页收款改版——收银台在默认浏览器打开
        （PC 扫码/登录支付均可），本窗口只做三件事：展示金额与订单号、
        提供「再次打开收银台」、轮询 alipay.trade.query 到账自动确认。"""
        import webbrowser
        win = tk.Toplevel(self)
        win.title("Vibe Pay · 支付宝收银台")
        win.configure(bg=BG)
        win.geometry("430x430+%d+%d" % (max(0, self.winfo_x() + 560),
                                        max(0, self.winfo_y() + 150)))
        win.resizable(False, False)
        self._pay_win = win                       # 防止被 grab 类循环误关其它窗时丢引用
        head = tk.Label(win, text="Vibe Pay · 支付宝收银台", bg=BG, fg=ORANGE,
                        font=FONT_MID)
        ic = images_util.ui_asset_scaled("icon_vibepay", 20, 20)
        if ic is not None:
            self._paywin_ic = ic
            head.configure(image=ic, compound="left", padx=6)
        head.pack(anchor="w", padx=16, pady=(14, 2))
        self._label(win, "已用默认浏览器打开支付宝收银台，扫码或登录支付均可",
                    bg=BG, fg=MUTED, font=FONT_SM,
                    icon="icon_qr").pack(anchor="w", padx=16)

        cv, card = rounded_card(win, pad=12, radius=14, parent_bg=BG)
        cv.pack(fill="both", expand=True, padx=14, pady=10)

        def _reopen(_e=None):
            try:
                webbrowser.open(intent["pay_url"])
                self.status_var.set("已重新打开支付宝收银台（订单 #%d）" % oid)
            except Exception as ex:                # noqa: BLE001
                self.status_var.set("打开浏览器失败：请手动访问支付链接")
                print("webbrowser.open:", ex)

        self._btn(card, "再次打开收银台", _reopen, "primary",
                  icon="icon_qr").pack(pady=(12, 4))
        # 先开一次收银台（在事件循环里跑，不阻塞本窗构建）
        win.after(200, _reopen)

        self._pay_state_lbl = tk.Label(card, text="等待买家付款…", bg=SURFACE,
                                       fg=ORANGE, font=FONT_MID)
        self._pay_state_lbl.pack(pady=(2, 8))
        self._btn(card, "取消并关闭", win.destroy, "ghost").pack(pady=(0, 10))

        # ---- 轮询查单（after 驱动，主线程安全；窗关即停） ----
        cfg_all = payment.load_pay_config()["vibe"]
        interval_ms = int(max(2, min(15, float(cfg_all.get("poll_interval", 3)))) * 1000)
        deadline = time.time() + float(cfg_all.get("timeout_sec", 900))
        stat = {"done": False}

        def _poll():
            if stat["done"] or not win.winfo_exists():
                return
            state, msg, trade_no = chan.query(intent["out_trade_no"])
            if state == "SUCCESS":
                stat["done"] = True
                db.mark_order_paid(oid, "已付款", pay_ref=trade_no)
                if win.winfo_exists():
                    self._pay_state_lbl.config(text="支付成功，到账已确认",
                                               fg=GREEN)
                    win.after(2000, win.destroy)
                self.status_var.set(
                    "Vibe Pay 支付成功：订单 #%d（支付宝交易号 %s）" % (oid, trade_no or "-"))
                self.refresh_orders()
                return
            if state == "FAIL":
                stat["done"] = True
                db.update_order_status(oid, "支付失败")
                if win.winfo_exists():
                    self._pay_state_lbl.config(text="支付失败：%s" % msg, fg=RED)
                self.refresh_orders()
                return
            if time.time() >= deadline:
                stat["done"] = True
                db.update_order_status(oid, "支付超时")
                if win.winfo_exists():
                    self._pay_state_lbl.config(text="等待超时，订单保留为待付款",
                                               fg=RED)
                return
            if win.winfo_exists():
                self._pay_state_lbl.config(text="等待买家付款…（%s）" % msg)
            win.after(interval_ms, _poll)

        win.after(interval_ms, _poll)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.focus_set()
        return win

    # ---------- Vibe Pay 支付配置（v0.62） ----------
    def refresh_orders(self):
        self.order_tree.delete(*self.order_tree.get_children())
        for o in db.list_orders()[:30]:
            status = o["status"] or ""
            if o.get("channel") == "vibe":      # v0.62：标出真实通道订单
                status = "Vibe·" + status
            self.order_tree.insert("", "end", values=(o["id"], o["person_name"], o["gift_name"],
                                    f"¥{o['amount']}", o["scheduled_date"], status))
        # v0.14：有订单隐藏空态提示
        if self.order_tree.get_children():
            self.order_hint.place_forget()
        else:
            self.order_hint.place(in_=self.order_tree, relx=0.5, rely=0.5,
                                  anchor="c")
            self.order_hint.lift()
        self.refresh_combos()

    # ============================================================
    #  ✨ 祝福语
    # ============================================================
