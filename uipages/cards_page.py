# -*- coding: utf-8 -*-
"""人脉卡片页 mix-in（v0.63 从 ui.py 机械拆出，方法体原样未动）"""
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


class CardsPageMixin:
    def _wheel(self, e):
        try:
            self.cv.yview_scroll(int(-e.delta / 120), "units")
        except Exception:
            pass

    # ============================================================
    #  🌐 关系网络（首页）
    # ============================================================
    def build_cards(self):
        top = tk.Frame(self.tab_cards, bg=BG); top.pack(fill="x", padx=12, pady=(10, 4))
        self._page_banner(top, "banner_cards")
        self._label(top, "人脉卡片", fg=CYAN, font=FONT_MID).pack(side="left")
        self._label(top, "  点卡片内文本框直接编辑 → 保存入库", fg=MUTED, font=FONT_SM).pack(side="left")
        self._btn(top, "＋ 新建人物", self.focus_new, "primary").pack(side="right", padx=6)
        self._btn(top, "从绿泡泡拉取", lambda: self.nb.select(self.tab_conn), "green", icon="icon_conn").pack(side="right", padx=6)
        self._btn(top, "刷新", self.refresh_cards, icon="icon_refresh").pack(side="right", padx=6)

        wrap = tk.Frame(self.tab_cards, bg=BG); wrap.pack(fill="both", expand=True, padx=8, pady=6)
        self.cv = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.cv.yview)
        self.cards_frame = tk.Frame(self.cv, bg=BG)
        self.cards_frame.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        # v0.15：内框宽度跟随画布（否则卡片网格停在请求宽度，右侧大片空白）
        cwin = self.cv.create_window((0, 0), window=self.cards_frame, anchor="nw")

        def _sync_cw(e=None):
            try:
                self.cv.itemconfig(cwin, width=self.cv.winfo_width())
            except Exception:
                pass
        self.cv.bind("<Configure>", _sync_cw)
        _sync_cw()
        self.cv.configure(yscrollcommand=vsb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.cv.bind("<MouseWheel>", self._wheel)
        self.cards_frame.bind("<MouseWheel>", self._wheel)
        self.refresh_cards()

    def _pill(self, parent, text, color, bg=SURFACE):
        """小圆角胶囊标签（v0.17：层级/分数/新建标记用）"""
        w = int(images_util.text_width(text, 9)) + 20
        pcv = tk.Canvas(parent, width=w, height=20, bg=bg,
                        highlightthickness=0, bd=0)
        pcv.create_polygon(_round_pts(1, 1, w - 2, 19, 10), smooth=True,
                           splinesteps=20, fill="#F0F4FA", outline=color,
                           width=1)
        pcv.create_text(w / 2, 10, text=text, fill=color, font=FONT_SM)
        return pcv

    def _pill_update(self, pcv, text, color):
        """v0.21：原地更新胶囊（层级/分数文字变化时，免整卡重建）"""
        if pcv is None:
            return
        try:
            w = int(images_util.text_width(text, 9)) + 20
            pcv.delete("all")
            pcv.config(width=w)
            pcv.create_polygon(_round_pts(1, 1, w - 2, 19, 10), smooth=True,
                               splinesteps=20, fill="#F0F4FA", outline=color,
                               width=1)
            pcv.create_text(w / 2, 10, text=text, fill=color, font=FONT_SM)
        except Exception:
            pass

    def refresh_cards(self):
        """v0.21：增量 diff 刷新（人多卡顿的核心修复）

        旧实现每次全量 destroy + 逐张重建：50 人 = 2695 个 widget 全灭全建，
        13000+ 次 Tk 调用 ≈ 8-13 秒（切页/保存/群发后每次都这么来一遍）。
        新实现按 pid 维护 self._card_meta：
        - 已存在的卡：层级/分数变化只原地更新（顶带色/头像/胶囊文字），
          顺序变化只 grid 重排——零 widget 创建；
        - 新增的人建新卡，删除的人销毁对应卡；
        - 首次（meta 为空）才走全量构建。
        """
        # 首页新增/删除人物时会调用本方法；卡片页尚未首次打开时无需构建，
        # 之后真正打开卡片页会由 build_cards() 全量渲染最新数据。
        if "cards_frame" not in vars(self):
            return
        scored = []
        ctx = scoring.score_context()     # v0.21：批量预取，逐人零查库
        for p in db.list_persons():
            s, _, t = compute_score(p, ctx)
            scored.append((s, t, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        cols = 2

        meta = getattr(self, "_card_meta", None)
        if not meta:
            # ---------- 首次：全量构建 ----------
            for w in self.cards_frame.winfo_children():
                w.destroy()
            self._cards_imgs = []      # 头像 PhotoImage 防 GC
            self._card_meta = meta = {}
            for i, (s, t, p) in enumerate(scored):
                r, c = divmod(i, cols)
                m = self._make_card(p, p["id"], t, s, r, c, is_new=False)
                meta[("pid", p["id"])] = m
            r, c = divmod(len(scored), cols)
            meta[("new",)] = self._make_card(None, None, "新建", 0, r, c,
                                             is_new=True)
            for cc in range(cols):
                self.cards_frame.columnconfigure(cc, weight=1, uniform="cc")
            self.cv.yview_moveto(0)
            return

        # ---------- 增量：目标集合（降序 + 末尾新建卡） ----------
        targets = [(("pid", p["id"]), s, t, p) for (s, t, p) in scored]
        targets.append((("new",), 0, "新建", None))

        # 1) 删除：目标里没有的卡
        want_keys = {k for k, _, _, _ in targets}
        for key in [k for k in meta if k not in want_keys]:
            m = meta.pop(key)
            for img in m.get("imgs", []):
                if img in self._cards_imgs:
                    self._cards_imgs.remove(img)
            m["cv"].destroy()

        # 2) 新增 + 3) 原地更新 + 4) 顺序重排
        for i, (key, s, t, p) in enumerate(targets):
            r, c = divmod(i, cols)
            m = meta.get(key)
            if m is None:
                if key[0] == "new":
                    m = self._make_card(None, None, "新建", 0, r, c, is_new=True)
                else:
                    m = self._make_card(p, p["id"], t, s, r, c, is_new=False)
                meta[key] = m
                continue
            # 层级变化：顶带色 / 头像 / 层级胶囊 原地更新
            if m["tier"] != t:
                accent = TIER_COLOR.get(t, CYAN)
                m["tier"] = t
                m["accent"] = accent
                try:
                    m["band"].config(bg=accent)
                except Exception:
                    pass
                av = images_util.load_circle_photo(
                        (p or {}).get("avatar"), (46, 46)) \
                    or images_util.letter_avatar((p or {}).get("name") or "新",
                                                 46, accent)
                if av is not None and m["av_lbl"] is not None:
                    old = m["av_lbl"].cget("image")
                    m["av_lbl"].config(image=av)
                    self._cards_imgs.append(av)
                    if old and old in self._cards_imgs:
                        self._cards_imgs.remove(old)
                    m["imgs"] = [i for i in m["imgs"]
                                 if i is not old] + [av]
                self._pill_update(m["tier_pill"], t, accent)
            # 分数变化：分数胶囊原地更新
            if m["score"] != s:
                m["score"] = s
                self._pill_update(m["score_pill"], "%d 分" % s, CYAN)
            # 顺序变化：grid 重排（保留 widget，仅改 grid 信息）
            if m["rc"] != (r, c):
                m["cv"].grid(row=r, column=c, sticky="nsew", padx=8, pady=8)
                m["rc"] = (r, c)

    def _make_card(self, person, pid, tier, score, row, col, is_new=False):
        accent = TIER_COLOR.get(tier, CYAN) if not is_new else GREEN
        cv, body = rounded_card(self.cards_frame, pad=12, radius=14)
        cv.grid(row=row, column=col, sticky="nsew", padx=8, pady=8)

        # v0.20 美化①：层级色顶带——卡片身份色（核心红/重要橙/普通青/浅层灰，
        #   新建卡用绿色），3px 实带贴在卡面顶部内缘
        band = tk.Frame(body, bg=accent, height=3)
        band.pack(fill="x")

        # ---------- 头部：头像 + 姓名 + 层级/分数胶囊 ----------
        hrow = tk.Frame(body, bg=SURFACE); hrow.pack(fill="x", pady=(10, 0))
        # v0.20 美化②：右上角星芒角标（程序化定制资产 card_deco_star，
        #   青→紫渐变辉光）。必须走 pack/grid——本环境 Tk 构建下 place 于
        #   create_window 嵌入 frame 的子部件不渲染；画进底板 texture 也会
        #   被 tag_lower 埋到 inner 不透明背景之下（v0.20 两次踩坑）。
        deco = images_util.ui_asset_scaled("card_deco_star", 40, 40)
        if deco is not None:
            self._cards_imgs.append(deco)
            tk.Label(hrow, image=deco, bg=SURFACE, bd=0).pack(
                side="right", padx=(4, 2))
        # v0.20 美化③：姓氏色圆头像（无形象图时）——渐变圆盘 + 姓氏首字 +
        #   层级色辉光描边，与关系网节点卡同风格；有形象图优先用形象图
        av = images_util.load_circle_photo((person or {}).get("avatar"),
                                           (46, 46)) \
            or images_util.letter_avatar((person or {}).get("name") or "新",
                                         46, accent)
        av_lbl = None
        if av:
            self._cards_imgs.append(av)
            av_lbl = tk.Label(hrow, image=av, bg=SURFACE)
            av_lbl.pack(side="left", padx=(0, 10))
        name_ent = self._entry(hrow, 12, FONT_MID)
        name_ent.insert(0, person["name"] if person else "")
        name_ent.pack(side="left")
        tier_pill = self._pill(hrow, "新建" if is_new else tier, accent)
        tier_pill.pack(side="left", padx=(8, 4))
        score_pill = None
        if not is_new:
            score_pill = self._pill(hrow, "%d 分" % score, CYAN)
            score_pill.pack(side="left", padx=4)
            if (person or {}).get("source") == "wechat":
                self._label(hrow, "绿泡泡", fg=GREEN, bg=SURFACE,
                            font=FONT_SM, icon="icon_conn").pack(
                                side="left", padx=6)

        F = {"name": name_ent}

        # ---------- 字段值 ----------
        if person:
            imp = json.loads(person.get("important_dates") or "[]")
            vals = {
                "relation": person["relation_type"],
                "closeness": person["closeness"],
                "sentiment": person["sentiment_weight"],
                "intro_by": person.get("intro_by") or "",
                "phone": person.get("phone") or "",
                "birthday": person.get("birthday") or "",
                "address": person.get("address") or "",
                "prefs": "、".join(json.loads(person.get("prefs") or "[]")),
                "taboo": "、".join(json.loads(person.get("taboo") or "[]")),
                "important": ", ".join(
                    f"{d.get('label', '')}:{d.get('date', '')}" for d in imp),
                "notes": clean_note(person.get("notes") or ""),
            }
        else:
            vals = {"relation": "普通朋友", "closeness": 0.5, "sentiment": 0.5,
                    "intro_by": "", "phone": "", "birthday": "", "address": "",
                    "prefs": "", "taboo": "", "important": "", "notes": ""}

        # ---------- 字段网格：每行 (标签, key, 是否下拉, 跨列数) ----------
        LAYOUT = [
            (("关系", "relation", True, 2),),
            (("亲密", "closeness", False, 1), ("情感", "sentiment", False, 1)),
            (("引荐人", "intro_by", False, 1), ("电话", "phone", False, 1)),
            (("生日", "birthday", False, 1), ("重要日期", "important", False, 1)),
            (("收货地址", "address", False, 2),),
            (("喜好", "prefs", False, 1), ("禁忌", "taboo", False, 1)),
            (("备注", "notes", False, 2),),
        ]
        grid = tk.Frame(body, bg=SURFACE)
        grid.pack(fill="x", pady=(6, 0))
        grid.columnconfigure(0, weight=1, uniform="fg")
        grid.columnconfigure(1, weight=1, uniform="fg")
        for r, rowdef in enumerate(LAYOUT):
            c = 0
            for label, key, is_combo, span in rowdef:
                rf = tk.Frame(grid, bg=SURFACE)
                rf.grid(row=r, column=c, columnspan=span, sticky="ew",
                        pady=5, padx=(0, 8) if span == 1 else 0)
                # v0.20：标签宽按字数自适应（CJK 每字约 2 单位），
                # 修「重要日期/收货地址」4 字标签被 width=5 裁掉末字的问题
                tk.Label(rf, text=label, bg=SURFACE, fg=MUTED, font=FONT_SM,
                         width=2 * len(label) + 1, anchor="w").pack(side="left")
                if is_combo:
                    w = ttk.Combobox(rf, values=db.RELATION_TYPES, font=FONT_SM,
                                     style="Dark.TCombobox")
                    w.set(vals.get(key, ""))
                else:
                    w = self._entry(rf, 20, FONT_SM)
                    w.insert(0, str(vals.get(key, "")))
                w.pack(side="left", fill="x", expand=True)
                F[key] = w
                c += span

        # v0.63：场景标签（可多选）——决定主页「场景卡」里这个人出现在哪一类。
        # 自动映射只是默认值，真正的归属以这里勾选为准。
        scene_vars = {}
        sc_row = tk.Frame(body, bg=SURFACE)
        sc_row.pack(fill="x", pady=(4, 0))
        tk.Label(sc_row, text="场景", bg=SURFACE, fg=MUTED, font=FONT_SM,
                 width=5, anchor="w").pack(side="left")
        cur_scenes = ((person or {}).get("scenes")
                      or db.default_scenes(vals["relation"]))
        for sc in db.list_scenes():
            v = tk.BooleanVar(value=(sc in cur_scenes))
            tk.Checkbutton(sc_row, text=sc, variable=v, bg=SURFACE, fg=TEXT,
                           activebackground=SURFACE, selectcolor=SURFACE,
                           font=FONT_SM).pack(side="left", padx=(0, 8))
            scene_vars[sc] = v
        self._label(sc_row, "主页场景卡里显示在哪一类", fg=MUTED,
                    font=FONT_XS).pack(side="left", padx=4)

        def save():
            name = F["name"].get().strip()
            if not name:
                self.status_var.set("姓名不能为空"); return
            cur = db.get_person(pid) if pid else None
            data = {
                "scenes": [s for s in db.list_scenes() if s in scene_vars and scene_vars[s].get()],
                "name": name,
                "relation_type": F["relation"].get(),
                "closeness": _f(F["closeness"].get()),
                "sentiment_weight": _f(F["sentiment"].get()),
                "intro_by": F["intro_by"].get().strip(),
                "address": F["address"].get().strip(),
                "phone": F["phone"].get().strip(),
                "birthday": F["birthday"].get().strip(),
                "important_dates": _parse_imp(F["important"].get()),
                "prefs": json.dumps(_split(F["prefs"].get()), ensure_ascii=False),
                "taboo": json.dumps(_split(F["taboo"].get()), ensure_ascii=False),
                # 展示过的是清洗后的人话；用户没改就写回原文，别把圈子/亲属元数据冲掉
                "notes": (old_raw if F["notes"].get().strip() == clean_note(old_raw)
                          else F["notes"].get().strip()),
                "source": (cur or {}).get("source", "manual"),
                "avatar": (cur or {}).get("avatar"),
            }
            newpid = db.save_person(data, pid)
            self.status_var.set(f"已保存「{name}」")
            self.refresh_cards(); self.refresh_network(); self.refresh_combos()

        def delete():
            if not pid: return
            if self._ask("确认删除", f"删除「{person['name']}」及其记录？"):
                db.delete_person(pid)
                self.status_var.set(f"已删除「{person['name']}」")
                self.refresh_cards(); self.refresh_network(); self.refresh_combos()
                self._conn_after_person_delete(person["name"])

        # v0.20 美化④：操作栏上方细分隔线（字段区 ↔ 动作区层次分离）
        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(11, 9))
        brow = tk.Frame(body, bg=SURFACE); brow.pack(fill="x")
        self._btn(brow, "保存", save, "primary", icon="icon_save").pack(side="left", padx=(0, 5))
        if not is_new:
            self._btn(brow, "删除", delete, "danger",
                      icon="icon_del").pack(side="left", padx=(0, 5))
        self._btn(brow, "送礼", lambda: self.go_gift(pid) if pid else None, icon="icon_gift").pack(side="left", padx=(0, 5))
        self._btn(brow, "祝福", lambda: self.go_greet(pid) if pid else None, icon="icon_star").pack(side="left")

        # v0.21：返回卡片 meta——refresh_cards 增量刷新据此做 diff
        # （层级/分数原地更新、顺序 grid 重排，消灭全量 destroy+重建）。
        # imgs：该卡持有的 PhotoImage（删除时从 _cards_imgs 摘除，防引用堆积）。
        return {"cv": cv, "body": body, "band": band, "tier_pill": tier_pill,
                "score_pill": score_pill, "av_lbl": av_lbl, "accent": accent,
                "tier": tier, "score": score, "pid": pid,
                "rc": (row, col), "imgs": [i for i in (av, deco) if i]}

    def focus_new(self):
        self.nb.select(self.tab_cards)
        self.status_var.set("在人脉卡片页最后一张绿色「新建」卡片中填写并保存")

    # ============================================================
    #  🎁 智能送礼
    # ============================================================
