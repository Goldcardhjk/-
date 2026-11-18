# -*- coding: utf-8 -*-
"""祝福语页 mix-in（v0.63 从 ui.py 机械拆出，方法体原样未动）"""
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


class GreetPageMixin:
    def build_greetings(self):
        # ---------- 顶栏：单人预览 + 节日 + 模型 ----------
        top = tk.Frame(self.tab_greet, bg=BG); top.pack(fill="x", padx=10, pady=8)
        self._page_banner(top, "banner_greet")
        self._label(top, "人物：", fg=TEXT).pack(side="left")
        self.gr_person = ttk.Combobox(top, width=16, font=FONT, style="Dark.TCombobox", state="readonly")
        self.gr_person.pack(side="left", padx=6)
        self._label(top, "节日：", fg=TEXT).pack(side="left")
        self.gr_holiday = ttk.Combobox(top, width=14, font=FONT, style="Dark.TCombobox",
                                       values=list(HOLIDAY_TEMPLATES.keys()), state="readonly")
        self.gr_holiday.pack(side="left", padx=6)
        # v0.18：祝福语模型选择器（此前只能改配置窗，页面上没法选）
        self._label(top, "模型：", fg=TEXT).pack(side="left")
        self.gr_model = ttk.Combobox(top, width=20, font=FONT_SM,
                                     style="Dark.TCombobox", state="readonly",
                                     values=llm.model_choices())
        self.gr_model.pack(side="left", padx=6)
        self.gr_model.set(llm.load_config().get("model")
                          or (llm.model_choices() or [""])[0])
        self.gr_model.bind("<<ComboboxSelected>>", self._gr_on_model)
        self._btn(top, "生成专属祝福", self.gen_greeting, "primary", icon="icon_star").pack(side="left", padx=8)
        self.gr_mode = self._label(top, "", fg=PURPLE, font=FONT_SM)
        self.gr_mode.pack(side="right")
        self._gr_refresh_mode()

        # ---------- 第二行：节日模板快捷 chips ----------
        hrow = tk.Frame(self.tab_greet, bg=BG); hrow.pack(fill="x", padx=10, pady=(0, 6))
        self._label(hrow, "节日模板：", fg=MUTED, font=FONT_SM).pack(side="left")
        for h in HOLIDAY_TEMPLATES.keys():
            self._btn(hrow, h, lambda hh=h: self._quick_holiday(hh),
                      "ghost").pack(side="left", padx=3)

        # ---------- 主体双栏：左预览 / 右群发面板 ----------
        body = tk.Frame(self.tab_greet, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        lcol = tk.Frame(body, bg=BG)
        lcol.pack(side="left", fill="both", expand=True, padx=(0, 10))
        rcol = tk.Frame(body, bg=BG, width=410)
        rcol.pack(side="right", fill="y")
        rcol.pack_propagate(False)

        # 左：祝福语预览卡
        tcv, tcard = rounded_card(lcol, pad=10, parent_bg=BG)
        tcv.pack(fill="both", expand=True)
        head = tk.Frame(tcard, bg=SURFACE)
        head.pack(fill="x")
        self.gr_head = self._label(head, "祝福语预览", fg=PURPLE, font=FONT_MID)
        self.gr_head.pack(side="left")
        self._btn(head, "复制", self.copy_greeting, icon="icon_copy").pack(side="right")
        self.gr_text = scrolledtext.ScrolledText(
            tcard, bg=images_util.glass_tint_ui() or SURFACE, fg=TEXT,
            insertbackground=CYAN, font=FONT, bd=0,
            highlightthickness=1, highlightbackground=BORDER)
        self.gr_text.pack(fill="both", expand=True, pady=(8, 0))
        # v0.14：空状态占位提示（生成后清除；复制时跳过占位内容）
        self._gr_hint_on = True
        self.gr_text.tag_config("hint", foreground=MUTED, justify="center",
                                spacing1=110, spacing3=10)
        self.gr_text.insert(tk.END,
                            "先选择「人物」和「节日」\n"
                            "再点「生成专属祝福」\n\n"
                            "或右侧勾选多人 → 一键群发",
                            "hint")

        # 右：节日群发面板（v0.17）
        self._build_mass_panel(rcol)

    def _gr_refresh_mode(self):
        """刷新顶栏模式标签（API/mock + 当前模型 + 社交成本）"""
        cfg = llm.load_config()
        mdl = cfg.get("model") or "（未选择）"
        if llm.mode() == "api":
            self.gr_mode.config(
                text=f"大模型：API 实连 · {mdl}（{llm.model_cost_label(mdl)}）")
        else:
            self.gr_mode.config(
                text=f"大模型：本地模拟（未配置 API）· 已选模型 "
                     f"{mdl}（{llm.model_cost_label(mdl)}）")

    def _gr_on_model(self, *_a):
        """顶栏模型选择：写全局配置并刷新标签"""
        m = (self.gr_model.get() or "").strip()
        if not m:
            return
        llm.save_config({"model": m})
        self._gr_refresh_mode()
        for t, pair in getattr(self, "_gr_tier_cbs", {}).items():
            self._gr_update_tier_cost(t)      # 「跟随全局」行的成本提示同步
        self.status_var.set(f"祝福语模型已切换：{m}"
                            f"（{llm.model_cost_label(m)}）")

    def _build_mass_panel(self, rcol):
        """群发面板：人员多选 chips + 进度条 + 结果列表 + 一键群发"""
        gcv, gcard = rounded_card(rcol, pad=12, parent_bg=BG)
        gcv.pack(fill="both", expand=True)
        self._label(gcard, "节日群发", fg=CYAN, font=FONT_MID,
                    icon="icon_star").pack(anchor="w")
        self._label(gcard,
                    "勾选对象 → 大模型按社交记忆与关系评级逐位定制撰写",
                    fg=MUTED, font=FONT_XS).pack(anchor="w", pady=(2, 6))

        # 全选 / 清空
        selrow = tk.Frame(gcard, bg=SURFACE); selrow.pack(fill="x")
        self._btn(selrow, "全选", self._gr_sel_all, "ghost",
                  icon="icon_add").pack(side="left", padx=(0, 6))
        self._btn(selrow, "清空", self._gr_sel_none, "ghost",
                  icon="icon_del").pack(side="left")
        self._gr_sel_cnt = self._label(selrow, "已选 0 人", fg=MUTED,
                                       font=FONT_SM)
        self._gr_sel_cnt.pack(side="right")

        # 人员 chips（可点击勾选/取消）
        self._gr_chip_host = tk.Frame(gcard, bg=SURFACE)
        self._gr_chip_host.pack(fill="x", pady=(8, 4))
        for cc in range(3):
            self._gr_chip_host.columnconfigure(cc, weight=1, uniform="gc")
        self._gr_sel = set()
        self._gr_chips = {}

        # ---------- 分段祝福模型（v0.18：按关系分 → 不同社交成本） ----------
        tm_head = tk.Frame(gcard, bg=SURFACE)
        tm_head.pack(fill="x", pady=(10, 3))
        self._label(tm_head, "分段祝福模型（按关系分 · 社交成本）", fg=CYAN,
                    font=FONT_SM).pack(side="left")
        self._btn(tm_head, "全部统一", self._gr_tier_reset, "ghost",
                  icon="icon_refresh").pack(side="right")
        tm_grid = tk.Frame(gcard, bg=SURFACE)
        tm_grid.pack(fill="x")
        self._gr_tier_cbs = {}
        tmodels = greetings.load_tier_models()
        for i, t in enumerate(greetings.TIER_ORDER):
            col = TIER_COLOR.get(t, MUTED)
            tk.Label(tm_grid, text=f"{t} {greetings.TIER_SCORE_RANGE[t]}",
                     bg=SURFACE, fg=col, font=FONT_XS, width=10,
                     anchor="w").grid(row=i, column=0, sticky="w", pady=1)
            cb = ttk.Combobox(tm_grid, width=16, font=FONT_XS,
                              style="Dark.TCombobox", state="readonly",
                              values=[greetings.FOLLOW_GLOBAL]
                              + llm.model_choices())
            cb.set(tmodels.get(t) or greetings.FOLLOW_GLOBAL)
            cb.grid(row=i, column=1, sticky="ew", padx=(4, 2), pady=1)
            cb.bind("<<ComboboxSelected>>",
                    lambda e, tt=t: self._gr_on_tier_model(tt))
            cost = self._label(tm_grid, "", fg=MUTED, font=FONT_XS)
            cost.grid(row=i, column=2, sticky="w", padx=(2, 0))
            self._gr_tier_cbs[t] = (cb, cost)
            self._gr_update_tier_cost(t)
        tm_grid.columnconfigure(1, weight=1)

        # 进度区
        self._label(gcard, "撰写进度", fg=CYAN, font=FONT_SM).pack(
            anchor="w", pady=(10, 3))
        self._gr_pbar = tk.Canvas(gcard, width=340, height=16, bg=SURFACE,
                                  highlightthickness=0, bd=0)
        self._gr_pbar.pack(fill="x")
        self._gr_pstat = self._label(gcard, "已完成 0 / 共 0 · 还剩 0 位",
                                     fg=MUTED, font=FONT_SM)
        self._gr_pstat.pack(anchor="w", pady=(3, 0))
        self._gr_cur = self._label(gcard, "", fg=YELLOW, font=FONT_SM)
        self._gr_cur.pack(anchor="w")

        # 结果列表（小滚动框）
        self._label(gcard, "撰写结果", fg=CYAN, font=FONT_SM).pack(
            anchor="w", pady=(10, 3))
        lwrap = tk.Frame(gcard, bg=SURFACE)
        lwrap.pack(fill="both", expand=True)
        lcv = tk.Canvas(lwrap, bg=SURFACE, highlightthickness=0, height=150)
        lsb = ttk.Scrollbar(lwrap, orient="vertical", command=lcv.yview)
        self._gr_list_host = tk.Frame(lcv, bg=SURFACE)
        self._gr_list_host.bind(
            "<Configure>", lambda e: lcv.configure(scrollregion=lcv.bbox("all")))
        lcwin = lcv.create_window((0, 0), window=self._gr_list_host, anchor="nw")

        def _sync_lw(e=None):
            try:
                lcv.itemconfig(lcwin, width=lcv.winfo_width())
            except Exception:
                pass
        lcv.bind("<Configure>", _sync_lw)
        _sync_lw()
        lcv.configure(yscrollcommand=lsb.set)
        lcv.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        def _lw(e, _cv=lcv):
            _cv.yview_scroll(int(-e.delta / 120), "units")
        lcv.bind("<MouseWheel>", _lw)
        self._gr_list_host.bind("<MouseWheel>", _lw)

        # 底部操作
        brow = tk.Frame(gcard, bg=SURFACE)
        brow.pack(fill="x", pady=(10, 0))
        self._gr_mass_btn = self._btn(brow, "一键群发祝福", self.mass_greeting,
                                      "primary", icon="icon_star")
        self._gr_mass_btn.pack(side="left", fill="x", expand=True)
        self._btn(brow, "复制全部", self.copy_all_greetings,
                  "ghost", icon="icon_copy").pack(side="left", padx=(6, 0))

        self._gr_batch = {}
        self._gr_queue = []
        self._gr_running = False
        self._gr_holiday = ""
        self._gr_rebuild_chips()
        self._gr_render_list()
        self._gr_update_progress()

    # ---------- 群发：人员选择 ----------
    def _gr_update_tier_cost(self, tier):
        """刷新某分段的成本提示（跟随全局时显示全局模型成本）"""
        pair = getattr(self, "_gr_tier_cbs", {}).get(tier)
        if not pair:
            return
        cb, cost_lbl = pair
        m = (cb.get() or "").strip()
        if not m or m == greetings.FOLLOW_GLOBAL:
            m = llm.load_config().get("model", "") or ""
            if not m and hasattr(self, "gr_model"):
                m = (self.gr_model.get() or "").strip()   # 顶栏当前选择
            tag = "全局"
        else:
            tag = ""
        if m:
            cost_lbl.config(text=f"{tag} {llm.model_cost_label(m)}".strip(),
                            fg=YELLOW if llm.model_cost(m) == 3 else MUTED)
        else:
            cost_lbl.config(text="未配置", fg=MUTED)

    def _gr_on_tier_model(self, tier):
        """分段模型选择：写 greet_tier_models 配置"""
        if getattr(self, "_gr_running", False):
            return
        pair = self._gr_tier_cbs.get(tier)
        if not pair:
            return
        m = (pair[0].get() or "").strip()
        mapping = greetings.load_tier_models()
        mapping[tier] = "" if m == greetings.FOLLOW_GLOBAL else m
        greetings.save_tier_models(mapping)
        self._gr_update_tier_cost(tier)
        show = m if m and m != greetings.FOLLOW_GLOBAL else "全局模型"
        self.status_var.set(f"「{tier}」圈层祝福模型：{show}")

    def _gr_tier_reset(self):
        """全部统一：清空分段配置，四层都跟随全局模型"""
        if getattr(self, "_gr_running", False):
            self._warn("提示", "群发撰写进行中，请稍候")
            return
        greetings.save_tier_models({})
        for t in greetings.TIER_ORDER:
            pair = self._gr_tier_cbs.get(t)
            if pair:
                pair[0].set(greetings.FOLLOW_GLOBAL)
            self._gr_update_tier_cost(t)
        self.status_var.set("四层已全部统一为全局祝福模型")

    def _gr_rebuild_chips(self):
        """按关系分从高到低重建人员 chips（保持已选状态）"""
        for w in self._gr_chip_host.winfo_children():
            w.destroy()
        self._gr_chips = {}
        scored = []
        ctx = scoring.score_context()     # v0.21：批量预取，逐人零查库
        for p in db.list_persons():
            s, _, t = compute_score(p, ctx)
            scored.append((s, t, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        for i, (s, t, p) in enumerate(scored):
            r, c = divmod(i, 3)
            chip = tk.Label(self._gr_chip_host,
                            text=f"{p['name']}", bg=SURFACE2, fg=MUTED,
                            font=FONT_SM, padx=8, pady=3, cursor="hand2")
            chip.bind("<Button-1>", lambda e, pid=p["id"]: self._gr_toggle(pid))
            chip.grid(row=r, column=c, sticky="ew", padx=2, pady=2)
            self._gr_chips[p["id"]] = (chip, t)
        # 清掉已不存在的人物
        live = {p["id"] for p in db.list_persons()}
        self._gr_sel &= live
        self._gr_sync_chips()

    def _gr_toggle(self, pid):
        if self._gr_running:
            self._warn("提示", "群发撰写进行中，请稍候")
            return
        if pid in self._gr_sel:
            self._gr_sel.discard(pid)
        else:
            self._gr_sel.add(pid)
        self._gr_sync_chips()

    def _gr_sync_chips(self):
        for pid, (chip, _t) in self._gr_chips.items():
            on = pid in self._gr_sel
            chip.configure(bg=SURFACE3 if on else SURFACE2,
                           fg=TEXT if on else MUTED,
                           highlightbackground=CYAN if on else SURFACE2,
                           highlightthickness=1 if on else 0)
        self._gr_sel_cnt.config(text=f"已选 {len(self._gr_sel)} 人")

    def _gr_sel_all(self):
        if self._gr_running:
            return
        self._gr_sel = {p["id"] for p in db.list_persons()}
        self._gr_sync_chips()

    def _gr_sel_none(self):
        if self._gr_running:
            return
        self._gr_sel.clear()
        self._gr_sync_chips()

    # ---------- 群发：主流程 ----------
    def mass_greeting(self):
        """一键群发：对勾选人物按社交记忆+关系评级逐位定制撰写"""
        if self._gr_running:
            return
        holiday = self.gr_holiday.get()
        if not holiday:
            self._warn("提示", "请先选择节日"); return
        pids = [pid for pid in self._gr_sel if db.get_person(pid)]
        if not pids:
            self._warn("提示", "请先在右侧勾选群发对象"); return
        # 关系分高的先写（重要的人优先拿到祝福）
        # v0.21：一次 list_persons + score_context，替代逐人 get_person+compute_score
        by_id = {p["id"]: p for p in db.list_persons()}
        ctx = scoring.score_context()
        pids.sort(key=lambda pid: -compute_score(by_id[pid], ctx)[0])
        self._gr_batch = {}
        for pid in pids:
            self._gr_batch[pid] = {"name": by_id[pid]["name"],
                                   "state": "wait", "text": "", "src": ""}
        self._gr_queue = list(pids)
        self._gr_holiday = holiday
        self._gr_running = True
        self._gr_q = queue.Queue()          # 线程→UI 结果队列
        self._btn_text(self._gr_mass_btn, "撰写中…")
        self._gr_cur.config(text="开始撰写…")
        self._gr_render_list()
        self._gr_update_progress()
        import threading
        threading.Thread(target=self._gr_worker, daemon=True).start()
        self._gr_poll()                     # 主线程轮询（after 从主线程调度）

    def _gr_worker(self):
        """后台线程：逐位撰写。结果只进队列，不碰 UI、不调 after——
        tkinter 的 after() 从工作线程调用要求主线程恰在事件循环内
        （冒烟环境只有周期性 update 会直接 RuntimeError），故用队列回传。"""
        import time
        while self._gr_queue:
            pid = self._gr_queue.pop(0)
            self._gr_q.put(("writing", pid, None, None))
            t0 = time.time()
            try:
                text, src = craft_one(pid, self._gr_holiday)
            except Exception as ex:
                text, src = f"（撰写失败：{ex}）", "mock"
            rest = 0.35 - (time.time() - t0)     # 进度条至少可见一瞬
            if rest > 0:
                time.sleep(rest)
            self._gr_q.put(("done", pid, text, src))

    def _gr_poll(self):
        """主线程轮询队列：应用撰写进度（100ms 节拍）"""
        try:
            while True:
                kind, pid, text, src = self._gr_q.get_nowait()
                if kind == "writing":
                    self._gr_set_state(pid, "writing")
                else:
                    self._gr_done(pid, text, src)
        except queue.Empty:
            pass
        if self._gr_running or not self._gr_q.empty():
            self.after(100, self._gr_poll)

    def _gr_set_state(self, pid, state):
        v = self._gr_batch.get(pid)
        if not v:
            return
        v["state"] = state
        if state == "writing":
            self._gr_cur.config(text=f"正在为「{v['name']}」撰写…")
        self._gr_render_list()

    def _gr_done(self, pid, text, src):
        v = self._gr_batch.get(pid)
        if not v:
            return
        v.update(state="done", text=text, src=src)
        self._gr_update_progress()
        self._gr_render_list()
        self._gr_show(pid)
        if all(x["state"] == "done" for x in self._gr_batch.values()):
            self._gr_running = False
            self._btn_text(self._gr_mass_btn, "一键群发祝福")
            n = len(self._gr_batch)
            self._gr_cur.config(text=f"全部完成，共 {n} 位")
            self.status_var.set(f"群发祝福撰写完成：{n} 位，可逐条查看或复制全部")

    def _gr_update_progress(self):
        total = len(self._gr_batch)
        done = sum(1 for v in self._gr_batch.values() if v["state"] == "done")
        left = total - done
        self._gr_pstat.config(text=f"已完成 {done} / 共 {total} · 还剩 {left} 位")
        cv = self._gr_pbar
        cv.delete("all")
        w = 340
        cv.create_polygon(_round_pts(1, 1, w - 1, 15, 7), smooth=True,
                          splinesteps=20, fill=SURFACE2)
        if total and done:
            fw = max(12, int((w - 4) * done / total))
            cv.create_polygon(_round_pts(2, 2, fw, 14, 6), smooth=True,
                              splinesteps=20, fill=CYAN)
        pct = int(done * 100 / total) if total else 0
        cv.create_text(w - 8, 8, text=f"{pct}%", fill=TEXT, anchor="e",
                       font=FONT_SM)

    def _gr_render_list(self):
        for w in self._gr_list_host.winfo_children():
            w.destroy()
        if not self._gr_batch:
            self._label(self._gr_list_host,
                        "还没有群发任务\n勾选对象后点「一键群发祝福」",
                        fg=MUTED, font=FONT_SM, justify="center").pack(pady=24)
            return
        st_txt = {"wait": "等待中", "writing": "撰写中…", "done": "已完成"}
        st_col = {"wait": MUTED, "writing": YELLOW, "done": GREEN}
        for pid, v in self._gr_batch.items():
            row = tk.Frame(self._gr_list_host, bg=SURFACE2,
                           highlightthickness=1, highlightbackground=BORDER)
            row.pack(fill="x", pady=2)
            tk.Label(row, text="●", bg=SURFACE2, fg=st_col[v["state"]],
                     font=FONT_SM).pack(side="left", padx=(8, 3), pady=3)
            tk.Label(row, text=v["name"], bg=SURFACE2, fg=TEXT,
                     font=FONT_SM, width=8, anchor="w").pack(side="left", pady=3)
            tk.Label(row, text=st_txt[v["state"]], bg=SURFACE2,
                     fg=st_col[v["state"]], font=FONT_SM).pack(side="left",
                                                               padx=4, pady=3)
            if v["state"] == "done":
                self._btn(row, "查看", lambda p=pid: self._gr_show(p),
                          "ghost").pack(side="right", padx=(4, 5), pady=2)

    def _gr_show(self, pid):
        """把某位已完成人物的祝福语加载到预览区"""
        v = self._gr_batch.get(pid)
        if not v or not v.get("text"):
            return
        self.gr_text.delete("1.0", tk.END)
        self.gr_text.insert(tk.END, v["text"])
        self._gr_hint_on = False
        self.gr_head.config(text=f"祝福语预览 — {v['name']} · {self._gr_holiday}")

    def _quick_holiday(self, h):
        """节日模板快捷 chips：设置节日并立即生成"""
        self.gr_holiday.set(h)
        self.gen_greeting()

    def gen_greeting(self):
        name = self.gr_person.get(); holiday = self.gr_holiday.get()
        if not name or not holiday:
            self._warn("提示", "请选择人物和节日"); return
        pid = self.person_map.get(name)
        # v0.18：用顶栏选定的模型（空则按该人物层级的分段模型）
        model = (self.gr_model.get() or "").strip() or None
        try:
            text, src = craft_one(pid, holiday, model)
        except Exception as ex:
            self._warn("无法生成祝福语", str(ex))
            self.status_var.set("云端/本地模型调用失败，未生成祝福语")
            return
        self.gr_text.delete("1.0", tk.END); self.gr_text.insert(tk.END, text)
        self._gr_hint_on = False
        self.gr_head.config(text=f"祝福语预览 — {name} · {holiday}")
        if src.startswith("cloud_"):
            import cloud_client
            label = "云端大模型" if src == "cloud_api" else "云端模拟"
            extra = " · 扣 2 积分，余额 %s" % cloud_client.cached_balance()
        else:
            label = "大模型撰写" if src == "api" else "本地模板"
            extra = " · 扣 2 积分，余额 %d" % db.points_balance() if src == "api" else " · 免费"
        self.status_var.set("已生成（%s）%s" % (label, extra))

    def copy_greeting(self):
        if getattr(self, "_gr_hint_on", False):
            return
        txt = self.gr_text.get("1.0", tk.END).strip()
        if txt:
            self.clipboard_clear(); self.clipboard_append(txt)
            self.status_var.set("祝福语已复制")

    def copy_all_greetings(self):
        done = [(v["name"], v["text"]) for v in self._gr_batch.values()
                if v["state"] == "done" and v.get("text")]
        if not done:
            self._warn("提示", "还没有已完成的祝福语"); return
        txt = "\n\n".join(f"——— 给 {n} ———\n{t}" for n, t in done)
        self.clipboard_clear(); self.clipboard_append(txt)
        self.status_var.set(f"已复制 {len(done)} 条祝福语")

    # ============================================================
    #  🟢 绿泡泡连接器（人物资料主要来源）
    # ============================================================
