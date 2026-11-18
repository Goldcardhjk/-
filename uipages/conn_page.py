# -*- coding: utf-8 -*-
"""绿泡泡连接器页 mix-in（v0.63 从 ui.py 机械拆出，方法体原样未动）"""
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


class ConnPageMixin:
    def build_connector(self):
        pad = tk.Frame(self.tab_conn, bg=BG)
        pad.pack(fill="both", expand=True, padx=14, pady=8)

        head = tk.Frame(pad, bg=BG); head.pack(fill="x")
        self._page_banner(head, "banner_conn")
        self._label(head, "绿泡泡连接器", fg=GREEN, font=FONT_LG,
                    icon="icon_conn").pack(side="left")
        self.conn_count_lbl = self._label(head, "", fg=MUTED, font=FONT_SM)
        self.conn_count_lbl.pack(side="left", padx=10, pady=(6, 0))
        self._label(head, "浏览好友卡片 → 一键拉取 → 自动生成人脉卡片与关系连线",
                    fg=MUTED, font=FONT_XS).pack(side="right", pady=(8, 0))

        bar = tk.Frame(pad, bg=BG); bar.pack(fill="x", pady=(3, 0))
        self._btn(bar, "连接沙盒", self.connect_sandbox, "green", icon="icon_link").pack(side="left", padx=(0, 8))
        self._btn(bar, "一键拉取全部", self.import_all, "primary", icon="icon_import").pack(side="left", padx=(0, 8))
        self._btn(bar, "大模型判断关系", self.run_llm_judge, "green", icon="ai_avatar").pack(side="left", padx=(0, 8))
        self._btn(bar, "去看关系网络", lambda: self.nb.select(self.tab_net), "ghost", icon="icon_net").pack(side="left")
        self.conn_status = self._label(bar, "", fg=GREEN, font=FONT_SM)
        self.conn_status.pack(side="right")
        self.llm_mode_lbl = self._label(bar, "", fg=PURPLE, font=FONT_XS)
        self.llm_mode_lbl.pack(side="right", padx=(0, 12))
        self.llm_mode_lbl.config(text=llm.mode_label())

        # 分组筛选
        self.conn_group = tk.StringVar(value="全部")
        gf = tk.Frame(pad, bg=BG); gf.pack(fill="x", pady=(8, 4))
        self.conn_group_btns = {}
        counts = wechat_sandbox.group_counts()
        for g in wechat_sandbox.GROUPS:
            b = self._btn(gf, f"{g} {counts.get(g, 0)}",
                          lambda gg=g: self._filter_conn(gg))
            b.pack(side="left", padx=(0, 6))
            self.conn_group_btns[g] = b
        self._label(gf, "点卡片右下角「拉取」单拉，或上方一键全拉",
                    fg=MUTED, font=FONT_SM).pack(side="left", padx=8)

        # 卡片网格（可滚动）
        wrap = tk.Frame(pad, bg=BG); wrap.pack(fill="both", expand=True)
        self.conn_canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.conn_canvas.yview)
        self.conn_grid = tk.Frame(self.conn_canvas, bg=BG)
        self.conn_grid.bind("<Configure>",
                            lambda e: self.conn_canvas.configure(
                                scrollregion=self.conn_grid.bbox("all")))
        conn_cwin = self.conn_canvas.create_window((0, 0), window=self.conn_grid,
                                                   anchor="nw")
        # v0.18：内框宽度跟随画布（仿人脉卡片页 _sync_cw）——否则三列
        # 卡片网格停在请求宽度，画布右侧留大片空白（用户截图反馈）
        def _sync_conn_cw(e=None):
            try:
                w = self.conn_canvas.winfo_width()
                if w < 8:
                    return
                self.conn_canvas.itemconfig(conn_cwin, width=w)
                if not getattr(self, "_conn_sized", False):
                    # v0.23：首次映射后重建（防抖 120ms）——此前
                    # build_connector 在 notebook 隐藏页上渲染的卡片，
                    # 其 canvas window item 被 Tk 判定「未映射创建」，
                    # 页面显示后不重贴内容：Label 会自愈重画，但
                    # Frame/圆角卡底/头像行/胶囊 tag 停在旧剪裁上不
                    # 绘制（用户看到「只有文字行的空卡框」——v0.23
                    # conn 卡片美化首轮截图的回归）。防抖等宽度稳定
                    # （映射过程宽度连续 1→…→1545，早重建会停留在中
                    # 间宽度的坏帧）；之后拖窗口只传宽度不重建
                    # （宽度传播正常，无需重绘）。
                    # 试错记录：itemconfig state=hidden→normal 强制
                    # 重贴会适得其反（整窗内容清空），弃用。
                    if getattr(self, "_conn_size_job", None):
                        self.after_cancel(self._conn_size_job)
                    self._conn_size_job = self.after(
                        120, self._conn_first_build)
            except Exception:
                pass
        self.conn_canvas.bind("<Configure>", _sync_conn_cw)
        _sync_conn_cw()
        self.conn_canvas.configure(yscrollcommand=vsb.set)
        self.conn_canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _wheel(e):
            self.conn_canvas.yview_scroll(int(-e.delta / 120), "units")
        self.conn_canvas.bind("<MouseWheel>", _wheel)
        self.conn_grid.bind("<MouseWheel>", _wheel)

        self.connect_sandbox()      # 进入页面即连接，少一步操作

    def _conn_first_build(self):
        """v0.23：画布首次映射且宽度稳定后重建连接器卡片网格

        修复「notebook 隐藏页上渲染的 canvas window item 映射后不重贴」：
        Frame 类子件（圆角卡/顶带/头像行/胶囊）停在旧剪裁不绘制。
        只跑一次（_conn_sized 门控）；用户拖窗口调大小走宽度传播，
        不触发重建（避免拖拽中卡片闪烁）。
        """
        self._conn_size_job = None
        if getattr(self, "_conn_sized", False):
            return
        self._conn_sized = True
        if hasattr(self, "conn_grid"):
            self._render_conn_grid()
            self._nudge_conn_view()

    def _nudge_conn_view(self):
        """v0.23：强制 Tk 重贴 conn_grid 全部 canvas window item

        重建卡片只修好首行——后续行的 Frame 子件仍停在旧剪裁上
        （用户截图：第二/三行只有文字行、缺头像行与胶囊 tag）。
        两条触发路径：
        ① 内容超过一屏：yview 微动归位（滚 2% 再回顶）触发 canvas
           完整重绘，用户无感知；
        ② 内容不足一屏（如「家人」筛选 4 人）：无滚动范围可用，
           退化为 window item 宽度 -1px→+1px 微缩微涨，强制重排张贴。
        """
        try:
            cv = self.conn_canvas
            cv.update_idletasks()
            bb = cv.bbox("all")
            if bb and cv.winfo_height() < bb[3] - bb[1]:
                cv.yview_moveto(0.02)
                cv.update_idletasks()
                cv.yview_moveto(0.0)
                cv.update_idletasks()
            else:
                w = max(cv.winfo_width(), 8)
                wins = cv.find_withtag("window")
                for it in wins:
                    cv.itemconfig(it, width=max(w - 1, 1))
                cv.update_idletasks()
                for it in wins:
                    cv.itemconfig(it, width=w)
                cv.update_idletasks()
        except Exception:
            pass

    def _filter_conn(self, g):
        self.conn_group.set(g)
        self._render_conn_grid()

    def connect_sandbox(self):
        info = wechat_sandbox.connect()
        self.sandbox = wechat_sandbox.list_contacts()
        existing = {p["name"] for p in db.list_persons()}
        self.conn_imported = {c["name"] for c in self.sandbox if c["name"] in existing}
        self.conn_status.config(text=f"{info['message']}")
        if hasattr(self, "conn_count_lbl"):
            self.conn_count_lbl.config(
                text=f"已在库 {len(self.conn_imported)} / {len(self.sandbox)} 位")
        if hasattr(self, "conn_group_btns"):
            for g, b in self.conn_group_btns.items():
                b.config(relief="sunken" if g == self.conn_group.get() else "raised")
        self._render_conn_grid()

    def _render_conn_grid(self):
        # v0.23：卡片 PhotoImage 防 GC 列表（头像/胶囊），每次渲染重建
        self._conn_imgs = []
        # 自愈（v0.19）：人物可能已在关系卡/资料页从人脉库删除，
        # conn_imported 缓存未同步会让被删者仍显示「已拉取」而无法
        # 重新拉取——用库内现存人名重算，被删者恢复「拉取」按钮
        if hasattr(self, "conn_imported"):
            existing = {p["name"] for p in db.list_persons()}
            self.conn_imported &= existing
            if hasattr(self, "conn_count_lbl") and hasattr(self, "sandbox"):
                self.conn_count_lbl.config(
                    text=f"已在库 {len(self.conn_imported)} / {len(self.sandbox)} 位")
        for w in self.conn_grid.winfo_children():
            w.destroy()
        contacts = wechat_sandbox.list_contacts(self.conn_group.get())
        if not contacts:
            tk.Label(self.conn_grid, text="（该分组暂无好友）", bg=BG, fg=MUTED,
                     font=FONT_SM).grid(row=0, column=0, padx=8, pady=8)
            return
        # v0.59：16 位沙盒好友用 4 列铺成 4×4 卡片墙，整页一屏看完；
        # 分组筛选（≤5 人）仍用 3 列，卡片更宽、文字不挤。
        try:
            avail = self.conn_canvas.winfo_width()
        except Exception:
            avail = 0
        cols = 4 if (len(contacts) >= 12 and avail >= 1180) else 3
        wrap = 330 if cols == 4 else 440
        for i, c in enumerate(contacts):
            r, cidx = divmod(i, cols)
            self._conn_card(self.conn_grid, c, wrap).grid(row=r, column=cidx,
                                                          sticky="nsew",
                                                          padx=5, pady=5)
        for cc in range(cols):
            self.conn_grid.columnconfigure(cc, weight=1, uniform="cc")

    def _conn_card(self, parent, c, wrap=440):
        """v0.59 沙盒好友卡片（紧凑版）：16 人 4×4 一屏看完

        v0.24 的分层卡（头像/胶囊/喜好/meta/note 五行）单卡 209px，
        4 行要 893px，一屏放不下，演示时第四行永远在滚动区外。
        v0.59 在不丢资料的前提下压到约 125px：
        ① 头像 40→32、内边距 12→10；
        ② 关系胶囊与喜好并排（胶囊 9→8）；
        ③ 生日/引荐/关系合一行、备注一行，按列宽截断而不折行。
        """
        imported = c["name"] in self.conn_imported
        col = wechat_sandbox.REL_COLOR.get(c["relation_type"], MUTED)
        cv, card = rounded_card(parent, pad=10, radius=14)
        max_chars = max(14, int(wrap / 11))

        def _clip(txt, n=max_chars):
            txt = str(txt or "")
            return txt if len(txt) <= n else txt[:n - 1] + "…"

        hd = tk.Frame(card, bg=SURFACE)
        hd.pack(fill="x", padx=10, pady=(8, 0))

        # 渐变头像（去辉光简洁版；PIL 不可用时退化为平涂圆）
        av = images_util.letter_avatar(c["name"], 32, col, glow=False)
        if av is not None:
            self._conn_imgs.append(av)          # PhotoImage 防 GC
            tk.Label(hd, image=av, bg=SURFACE).pack(side="left")
        else:
            avc = tk.Canvas(hd, width=32, height=32, bg=SURFACE,
                            highlightthickness=0, bd=0)
            avc.create_oval(2, 2, 30, 30, fill=col, outline=BG, width=2)
            avc.create_text(16, 16, text=c["name"][:1], fill="#06222B",
                            font=FONT_SM)
            avc.pack(side="left")

        info = tk.Frame(hd, bg=SURFACE)
        info.pack(side="left", padx=(8, 4), fill="x", expand=True)
        tk.Label(info, text=_clip(c["name"], 8), bg=SURFACE, fg=TEXT,
                 font=(UI_FONT, 10, "bold")).pack(anchor="w")
        if c.get("remark"):
            tk.Label(info, text=_clip(c["remark"], 10), bg=SURFACE, fg=MUTED,
                     font=FONT_XS).pack(anchor="w")

        # 右侧状态位：已拉取 = 绿色小字；未拉取 = 扁平绿胶囊（可点）
        if imported:
            tk.Label(hd, text="✓ 已拉取", bg=SURFACE, fg=GREEN,
                     font=FONT_XS).pack(side="right")
        else:
            pull = images_util.smooth_pill(
                " 拉取 ", "#06222B", fill=GREEN, size=8)
            if pull is not None:
                self._conn_imgs.append(pull)
                pl = tk.Label(hd, image=pull, bg=SURFACE, cursor="hand2")
                pl.bind("<Button-1>",
                        lambda e, n=c["name"]: self.import_one(n))
                pl.pack(side="right")
            else:
                tl = tk.Label(hd, text="拉取", bg=SURFACE, fg=GREEN,
                              font=FONT_XS, cursor="hand2")
                tl.bind("<Button-1>",
                        lambda e, n=c["name"]: self.import_one(n))
                tl.pack(side="right")

        # 关系胶囊 + 喜好同一行（关系色是唯一强调色）
        tag = tk.Frame(card, bg=SURFACE)
        tag.pack(fill="x", padx=10, pady=(2, 0))
        rel_pill = images_util.smooth_pill(
            f" {c['relation_type']} ", "#06222B", fill=col, size=8)
        if rel_pill is not None:
            self._conn_imgs.append(rel_pill)
            tk.Label(tag, image=rel_pill, bg=SURFACE).pack(side="left")
        else:
            tk.Label(tag, text=f" {c['relation_type']} ", bg=col,
                     fg="#06222B", font=FONT_XS).pack(side="left")
        if c.get("prefs"):
            rest = max(8, max_chars - len(c["relation_type"]) - 4)
            tk.Label(tag, text=_clip("喜好：" + "、".join(c["prefs"][:3]), rest),
                     bg=SURFACE, fg=TEXT, font=FONT_XS).pack(
                         side="left", padx=(6, 0))

        # 一行 meta（生日/引荐/关系）+ 一行备注，超宽截断
        meta = []
        if c.get("birthday"):
            meta.append("生日 " + c["birthday"])
        if c.get("intro_by"):
            meta.append(f"{c['intro_by']} 引荐")
        links = wechat_sandbox.contact_links(c)
        if links:
            meta.append("关系 " + "、".join(t for t, _ in links[:2]))
        detail = "  ·  ".join(meta)
        if c.get("note"):
            detail = (detail + "  ·  " if detail else "") + c["note"]
        if detail:
            tk.Label(card, text=_clip(detail, max_chars), bg=SURFACE,
                     fg=MUTED, font=FONT_XS, anchor="w").pack(
                         fill="x", padx=10, pady=(2, 6))
        return cv

    def import_one(self, name):
        c = next((x for x in wechat_sandbox.list_contacts() if x["name"] == name), None)
        if not c:
            return
        if name in self.conn_imported:
            self.status_var.set(f"「{name}」已在人脉库中")
            return
        db.save_person(wechat_sandbox.contact_to_person(c))
        self.conn_imported.add(name)
        n = self._sync_sandbox_links()
        self.status_var.set(f"已拉取「{name}」" + (f"，同步 {n} 条关系连线" if n else ""))
        self.refresh_cards(); self.refresh_network(); self.refresh_combos()
        self._render_conn_grid()
        self.conn_count_lbl.config(
            text=f"已在库 {len(self.conn_imported)} / {len(self.sandbox)} 位")

    def import_all(self):
        new = [c for c in wechat_sandbox.list_contacts()
               if c["name"] not in self.conn_imported]
        if not new:
            self.status_var.set("沙盒好友已全部拉取")
            return
        for c in new:
            db.save_person(wechat_sandbox.contact_to_person(c))
            self.conn_imported.add(c["name"])
        n = self._sync_sandbox_links()
        self.status_var.set(f"一键拉取完成：新增 {len(new)} 位，同步 {n} 条关系连线")
        self.refresh_cards(); self.refresh_network(); self.refresh_combos()
        self.connect_sandbox()
        # v0.5：拉取后由大模型判断人物间关系 + 与我的关系分
        self.run_llm_judge(silent=True)
        # v0.59：状态栏数字与图面一致（含引荐补全后的真实条数）
        self.status_var.set(
            f"一键拉取完成：新增 {len(new)} 位，人物关系 {len(db.list_links())} 条")

    def run_llm_judge(self, silent=False):
        """🤖 大模型判断关系：人物间关系+分数、与我的关系分，写库并刷新网络"""
        persons = db.list_persons()
        if len(persons) < 2:
            if not silent:
                self._info("大模型判断关系", "至少需要 2 位人物才能判断关系")
            return
        # 沙盒内的好友关系作为权威补充信息喂给大模型
        extra = []
        for c in wechat_sandbox.list_contacts():
            if c["name"] in {p["name"] for p in persons}:
                extra.extend((c["name"], t, r) for t, r in wechat_sandbox.contact_links(c))
        try:
            r = llm.judge_all(persons, extra)
        except Exception as ex:
            if not silent:
                self._error("大模型判断失败", str(ex))
            else:
                self.conn_status.config(text="自动关系判断未执行：%s" % ex)
            return
        name2id = {p["name"]: p["id"] for p in persons}
        # 1) 与我的关系分 → 亲密度/情感权重
        n_me = 0
        for name, info in (r.get("my_scores") or {}).items():
            pid = name2id.get(name)
            if not pid:
                continue
            try:
                db.set_degree(pid, float(info.get("closeness", 0.5)),
                              float(info.get("sentiment", 0.5)))
                n_me += 1
            except Exception:
                pass
        # 2) 人物间关系 + 关系分数
        n_lk = 0
        for l in (r.get("links") or []):
            a, b = name2id.get(l.get("a")), name2id.get(l.get("b"))
            if not a or not b or a == b:
                continue
            if db.add_link(a, b, l.get("relation") or "认识",
                           l.get("note") or "", l.get("score", 50)):
                n_lk += 1
        self.refresh_cards(); self.refresh_network(); self.refresh_combos()
        if "cost" in r and "balance" in r:
            tag = "云端 API" if r.get("mode") == "api" else "云端模拟"
            billing = " · 扣 %d 积分，余额 %d" % (r["cost"], r["balance"])
        else:
            tag = "API 实连" if r.get("mode") == "api" else "本地模拟"
            billing = " · 扣 10 积分，余额 %d" % db.points_balance() if r.get("mode") == "api" else " · 免费"
        self.conn_status.config(text=f"大模型（{tag}）：更新与我的关系分 {n_me} 人，"
                                     f"推断关系 {n_lk} 条{billing}")
        if not silent:
            self._info(
                "大模型判断关系",
                f"模式：{tag}\n与我的关系分：更新 {n_me} 人\n人物间关系：推断 {n_lk} 条\n\n"
                "关系分已写入连线（颜色/长短随分数变化），可随时手动补充修正。")

    def _sync_sandbox_links(self):
        """把沙盒内人物之间的连接同步进 links 表（两端都已在库才建）"""
        persons = db.list_persons()
        name2id = {p["name"]: p["id"] for p in persons}
        n = 0
        for link in llm.explicit_family_links(persons):
            if db.add_link(name2id[link["a"]], name2id[link["b"]],
                           link["relation"], link["note"], link["score"]):
                n += 1
        for c in wechat_sandbox.list_contacts():
            a = name2id.get(c["name"])
            if not a:
                continue
            for tgt, rel in wechat_sandbox.contact_links(c):
                if rel in llm.FAMILY_RELATIONS:
                    continue
                b = name2id.get(tgt)
                if b and b != a and db.add_link(a, b, rel):
                    n += 1
        return n

    # ============================================================
    #  公共刷新 & 跳转
    # ============================================================
