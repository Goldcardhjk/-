# -*- coding: utf-8 -*-
"""跨页弹窗 mix-in（v0.63 从 ui.py 机械拆出，方法体原样未动）"""
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
import points
import cloud_client
import netlayout
from relationship_view import build_relationship_view

# 统一导入 ui_base 的主题常量与部件（星导含全部非下划线名；
# 下划线工具函数按需显式导入，保证方法体引用原样可用）
from ui_base import *                                   # noqa: F401,F403
from ui_base import (_mix, _rounded, _round_pts, _pick_icon,   # noqa: F401
                     _wrap_by_width, _score_color, clean_note,
                     _f, _split, _parse_imp)


class DialogsMixin:
    def _dialog(self, title, msg, accent=CYAN, mark="i",
                buttons=(("好", True),)):
        """深色模态对话框（v0.28 重做）

        用户反馈（2026-10-28，针对「自动布局」确认框）：「这个做的太丑了」。

        旧版丑在三处装饰互相打架：系统的白色标题栏、最外层再套一层圆角
        描边卡、顶上一整条 accent 色带。新版把三处全去掉：
        - overrideredirect 自绘，没有系统标题栏；圆角 + 柔和投影，圆角外
          的像素用主窗底色做 chroma key（-transparentcolor），边缘与阴影
          自然融进背景
        - 标题与圆形徽标同一行（不再是「色带 + 图标 + 标题」三层）
        - 正文常规行距、按钮靠右下；Esc=取消 / Enter=确定；窗口可拖动

        buttons=[(文字, 返回值), ...] 主操作放最后（最右）。
        """
        import tkinter.font as tkfont
        win = tk.Toplevel(self)
        win.withdraw()
        try:
            win.overrideredirect(True)
        except Exception:
            pass
        win.configure(bg=BG)
        win.transient(self)

        DIALOG_BG = SURFACE2          # 比画布（SURFACE）亮一档，弹窗才浮得起来
        f_body = tkfont.Font(root=win, font=FONT)
        pad, badge, btn_h, body_w = 24, 26, 30, 340
        lines = _wrap_by_width(msg, f_body, body_w)
        body_h = f_body.metrics("linespace") * len(lines)
        card_w = body_w + pad * 2
        card_h = pad + badge + 16 + body_h + 20 + btn_h + pad
        sw, sh = card_w + 20, card_h + 24        # smooth_card 的投影留边

        cv = tk.Canvas(win, width=sw, height=sh, bg=BG,
                       highlightthickness=0, bd=0)
        cv.pack()
        img = images_util.smooth_card(card_w, card_h, 14, DIALOG_BG, BORDER,
                                      shadow=True)
        if img is not None:
            cv._shadow_img = img                  # 保持引用，防 GC
            cv.create_image(sw / 2, sh / 2, image=img)
        else:
            cv.create_polygon(_round_pts(10, 12, 10 + card_w, 12 + card_h, 14),
                              smooth=True, splinesteps=24, fill=DIALOG_BG,
                              outline=BORDER, width=1)

        inner = tk.Frame(cv, bg=DIALOG_BG)
        cv.create_window(10 + pad, 12 + pad, window=inner, anchor="nw",
                         width=card_w - 2 * pad)
        # 标题行：圆形徽标 + 标题同一行
        head = tk.Frame(inner, bg=DIALOG_BG)
        head.pack(fill="x")
        bc = tk.Canvas(head, width=badge, height=badge, bg=DIALOG_BG,
                       highlightthickness=0, bd=0)
        bc.pack(side="left")
        bc.create_oval(2, 2, badge - 2, badge - 2,
                       fill=_mix(DIALOG_BG, accent, 0.16), outline=accent,
                       width=2)
        bc.create_text(badge / 2, badge / 2 + 1, text=mark, fill=accent,
                       font=(UI_FONT, 11, "bold"))
        tk.Label(head, text=title, bg=DIALOG_BG, fg=TEXT, font=FONT_MID,
                 anchor="w").pack(side="left", padx=(11, 0))
        tk.Label(inner, text="\n".join(lines), bg=DIALOG_BG,
                 fg=_mix(TEXT, DIALOG_BG, 0.22), font=FONT, justify="left",
                 anchor="w", wraplength=body_w).pack(fill="x", pady=(16, 0))

        brow = tk.Frame(inner, bg=DIALOG_BG)
        brow.pack(fill="x", pady=(20, 0))
        result = {"v": buttons[0][1]}

        def _done(v):
            result["v"] = v
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()

        for label, val in reversed(buttons):
            self._btn(brow, label, lambda v=val: _done(v),
                      "primary" if val else "ghost").pack(side="right",
                                                          padx=(8, 0))

        # 圆角外的像素用主窗底色做 chroma key → 真透明，投影自然
        try:
            win.attributes("-transparentcolor", BG)
        except Exception:
            pass

        # 居中于主窗，并夹到屏幕内（小屏幕也不会跑到标题栏后面）
        try:
            self.update_idletasks()
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw, ph = self.winfo_width(), self.winfo_height()
            x = px + max(0, (pw - sw) // 2)
            y = py + max(0, (ph - sh) // 2)
            x = max(0, min(x, self.winfo_screenwidth() - sw))
            y = max(0, min(y, self.winfo_screenheight() - sh))
            win.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

        # 无标题栏 → 自己给个可拖动区域（卡片空白处）
        drag = {"x": None, "y": None}

        def _press(e):
            drag["x"] = e.x_root - win.winfo_x()
            drag["y"] = e.y_root - win.winfo_y()

        def _move(e):
            if drag["x"] is None:
                return
            win.geometry("+%d+%d" % (e.x_root - drag["x"],
                                     e.y_root - drag["y"]))
        for w in (cv, inner, head):
            w.bind("<Button-1>", _press)
            w.bind("<B1-Motion>", _move)

        win.bind("<Escape>", lambda e: _done(buttons[0][1]))
        win.bind("<Return>", lambda e: _done(buttons[-1][1]))
        win.deiconify()
        try:
            win.grab_set()
            win.focus_force()
        except Exception:
            pass
        self.wait_window(win)
        return result["v"]

    def _ask(self, title, msg):
        """是/否确认（黄标 ?）"""
        return self._dialog(title, msg, accent=YELLOW, mark="?",
                            buttons=(("取消", False), ("确定", True)))

    def _info(self, title, msg):
        """信息提示（青标 i）"""
        return self._dialog(title, msg, accent=CYAN, mark="i",
                            buttons=(("好", True),))

    def _warn(self, title, msg):
        """警告（橙标 !）"""
        return self._dialog(title, msg, accent=ORANGE, mark="!",
                            buttons=(("好", True),))

    def _error(self, title, msg):
        """错误（红标 ×）"""
        return self._dialog(title, msg, accent=RED, mark="×",
                            buttons=(("好", True),))

    def open_llm_config(self):
        """大模型 API 配置窗：选预设 → 填 Key → 测试连接 → 保存。

        内置腾讯混元免费预设（Hunyuan-Lite 永久免费）及火山方舟 /
        DeepSeek / 自定义（OpenAI 兼容）。保存写 llm_config.json。"""
        win = tk.Toplevel(self)
        win.title("大模型 API 配置")
        win.configure(bg=BG)
        win.resizable(False, False)
        W, H = 520, 500
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{W}x{H}+{max(0, (sw - W) // 2)}+{max(0, (sh - H) // 2)}")
        win.transient(self)
        win.grab_set()

        self._label(win, "大模型 API 配置", fg=CYAN, bg=BG, font=FONT_MID,
                    icon="ai_avatar").pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(win, text="选预设 → 填 Key → 测试连接 → 保存", bg=BG,
                 fg=MUTED, font=FONT_SM).pack()

        form = tk.Frame(win, bg=BG); form.pack(fill="x", padx=28, pady=10)

        def _row(label):
            tk.Label(form, text=label, bg=BG, fg=MUTED, font=FONT_SM,
                     anchor="w").pack(fill="x", pady=(8, 2))

        cfg = llm.load_config()
        labels = {k: v["label"] for k, v in llm.PRESETS.items()}

        # 预设
        _row("服务商预设")
        preset_cb = ttk.Combobox(form, values=list(labels.values()),
                                 state="readonly", font=FONT_SM,
                                 style="Dark.TCombobox")
        preset_cb.set(labels.get(cfg.get("provider", "hunyuan"),
                                 "腾讯混元（Lite 永久免费）"))
        preset_cb.pack(fill="x")

        # base_url
        _row("Base URL（OpenAI 兼容）")
        base_ent = self._entry(form, font=FONT_SM)
        base_ent.pack(fill="x", ipady=4)
        # api_key
        _row("API Key")
        key_ent = tk.Entry(form, bg=SURFACE2, fg=TEXT, insertbackground=CYAN,
                           relief="flat", highlightthickness=1,
                           highlightbackground=BORDER, highlightcolor=CYAN,
                           font=FONT_SM, show="*")
        key_ent.pack(fill="x", ipady=4)
        # model
        _row("模型")
        model_cb = ttk.Combobox(form, state="readonly", font=FONT_SM,
                                style="Dark.TCombobox")
        model_cb.pack(fill="x")

        def _apply_preset(*_a):
            pkey = next((k for k, v in labels.items() if v == preset_cb.get()),
                        "hunyuan")
            p = llm.PRESETS[pkey]
            base_ent.delete(0, "end")
            base_ent.insert(0, p["base_url"])
            vals = list(p["models"])
            if cfg.get("model") and cfg["model"] not in vals:
                vals.append(cfg["model"])
            model_cb.configure(values=vals)
            model_cb.set(p["default_model"])

        preset_cb.bind("<<ComboboxSelected>>", _apply_preset)
        _apply_preset()
        if cfg.get("base_url"):
            base_ent.delete(0, "end"); base_ent.insert(0, cfg["base_url"])
        if cfg.get("api_key"):
            key_ent.insert(0, cfg["api_key"])
        if cfg.get("model"):
            model_cb.set(cfg["model"])

        st = tk.Label(win, text="", bg=BG, fg=MUTED, font=FONT_SM, anchor="w",
                      wraplength=460, justify="left")
        st.pack(fill="x", padx=28, pady=(10, 0))

        def _test():
            st.configure(text="测试中…", fg=MUTED)
            win.update()
            ok, msg = llm.test_connection(base_ent.get().strip(),
                                          key_ent.get().strip(),
                                          model_cb.get().strip())
            st.configure(text=("成功：" if ok else "失败：") + msg,
                         fg=GREEN if ok else RED)

        def _save():
            pkey = next((k for k, v in labels.items() if v == preset_cb.get()),
                        "custom")
            llm.save_config({
                "provider": pkey,
                "base_url": base_ent.get().strip(),
                "api_key": key_ent.get().strip(),
                "model": model_cb.get().strip(),
            })
            # 同步刷新 UI：模型下拉 / 模式标签 / 状态栏
            self.chat_model.configure(values=llm.model_choices())
            self.chat_model.set(model_cb.get().strip() or "hunyuan-lite")
            if hasattr(self, "gr_model"):
                self.gr_model.configure(values=llm.model_choices())
                self.gr_model.set(model_cb.get().strip() or "hunyuan-lite")
            if hasattr(self, "_gr_refresh_mode"):
                self._gr_refresh_mode()
            if hasattr(self, "_gr_tier_cbs"):
                for t in greetings.TIER_ORDER:
                    self._gr_update_tier_cost(t)
            if hasattr(self, "llm_mode_lbl"):
                self.llm_mode_lbl.config(text=llm.mode_label())
            self.status_var.set("大模型配置已保存：" + labels[pkey])
            win.destroy()

        btns = tk.Frame(win, bg=BG); btns.pack(pady=12)
        self._btn(btns, "测试连接", _test, "green", icon="icon_search").pack(side="left", padx=6)
        self._btn(btns, "保存", _save, "primary", icon="icon_save").pack(side="left", padx=6)
        self._btn(btns, "取消", win.destroy, "ghost").pack(side="left", padx=6)

    def open_notes_import(self):
        """粘贴会议纪要或聊天记录，先解析预览，再决定是否写入关系网络。"""
        win = tk.Toplevel(self)
        win.title("导入会议纪要 / 社交细节")
        win.configure(bg=BG)
        win.geometry("660x560+140+90")
        win.transient(self)
        tk.Label(win, text="把纪要或聊天里的事实粘进来", bg=BG, fg=TEXT,
                 font=FONT_MID, anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(win, text="只认已建档人物的名字和明确说出的关系；没说到的不猜，已有关系不覆盖。",
                 bg=BG, fg=MUTED, font=FONT_XS, anchor="w").pack(fill="x", padx=14, pady=(0, 8))
        source = tk.Text(win, height=9, bg=SURFACE, fg=TEXT, font=FONT_SM,
                         bd=0, highlightthickness=1, highlightbackground=BORDER,
                         wrap="word", padx=8, pady=6)
        source.pack(fill="x", padx=14)
        preview = tk.Text(win, height=8, bg=SURFACE2, fg=TEXT, font=FONT_XS,
                          bd=0, highlightthickness=1, highlightbackground=BORDER,
                          wrap="word", padx=8, pady=6, state="disabled")
        preview.pack(fill="both", expand=True, padx=14, pady=(10, 0))
        state = {"actions": []}
        btns = tk.Frame(win, bg=BG)
        btns.pack(fill="x", padx=14, pady=12)

        def _show(lines):
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert("end", "\n".join(lines))
            preview.configure(state="disabled")

        def _parse():
            persons = db.list_persons()
            result = llm.parse_notes(source.get("1.0", "end"), persons, db.list_links())
            state["actions"] = result["actions"]
            lines = list(result["summary"])
            if result["actions"]:
                lines.append("")
                lines.append("将要执行：")
                for act in result["actions"]:
                    if act["op"] == "add_link":
                        lines.append(f"· 建立关系：{act['a']} ↔ {act['b']}（{act['relation']}）")
                    elif act["op"] == "add_memo":
                        lines.append(f"· 写备注：{act['name']} — {act['text'][:60]}")
                    elif act["op"] == "update_score":
                        lines.append(f"· 关系分：{act['name']} {'+' if act['delta'] > 0 else ''}{act['delta']}")
            _show(lines)
            apply_btn.config(state="normal" if result["actions"] else "disabled")

        def _apply():
            if not state["actions"]:
                return
            self._chat_apply("已按纪要更新关系网络。", state["actions"])
            state["actions"] = []
            win.destroy()

        self._btn(btns, "解析", _parse, "primary", icon="icon_search").pack(side="left")
        apply_btn = self._btn(btns, "写入网络", _apply, "green", icon="icon_save")
        apply_btn.pack(side="left", padx=8)
        apply_btn.config(state="disabled")
        self._btn(btns, "取消", win.destroy, "ghost").pack(side="right")
        _show(["粘好内容后点「解析」，这里会列出会写入哪些关系、备注和关系分。"])
        # 供自检脚本复用同一套解析/写入逻辑（不另写一份测试实现）。
        win.notes_source = source
        win.notes_preview = preview
        win.notes_state = state
        win.notes_parse = _parse
        win.notes_apply = _apply
        return win

    # ---------- 缩放 & 平移 ----------
    def backup_data(self):
        """把当前账号的整库备份到用户选定文件。"""
        from datetime import date as _d
        acc = db.current_account()
        path = filedialog.asksaveasfilename(
            parent=self, title="备份当前账号数据",
            defaultextension=".db",
            initialfile="进步壹号备份_%s_%s.db" % (acc, _d.today().isoformat()),
            filetypes=[("SQLite 数据备份", "*.db")])
        if not path:
            return
        try:
            out = db.backup_database(path)
        except Exception as ex:
            self._warn("备份失败", str(ex))
            return
        self.status_var.set("已备份当前账号数据：%s" % out)
        self._info("备份完成", "当前账号数据已备份到：\n%s" % out)

    def restore_data(self):
        """从备份文件整体恢复当前账号（覆盖现有数据，需确认）。"""
        path = filedialog.askopenfilename(
            parent=self, title="从备份恢复当前账号",
            filetypes=[("SQLite 数据备份", "*.db"), ("全部文件", "*.*")])
        if not path:
            return
        if not self._ask("恢复确认",
                         "将用备份文件覆盖当前账号的全部数据（人物、关系、"
                         "互动、订单），此操作不可撤销，继续？"):
            return
        try:
            db.restore_backup(path)
        except Exception as ex:
            self._warn("恢复失败", str(ex))
            return
        # 全量刷新各页：人物、网络、订单、下拉框、右侧栏
        self.refresh_combos()
        self.refresh_network()
        if hasattr(self, "refresh_orders"):
            self.refresh_orders()
        if hasattr(self, "refresh_cards"):
            self.refresh_cards()
        self.status_var.set("已从备份恢复当前账号：%s" % path)
        self._info("恢复完成", "当前账号数据已从备份恢复。")

    def toggle_linking(self):
        """进入 / 退出连线模式"""
        if self._linking:
            self.cancel_linking()
        else:
            self.start_linking_from(None)

    def start_linking_from(self, pid):
        """进入连线模式；pid 为起点（右键菜单带入）或 None（按钮进入后点选起点）"""
        self._linking = True
        self._linking_src = pid
        self.web_canvas.config(cursor="crosshair")
        self._btn_text(self.link_btn, "取消连线")
        if pid is not None:
            name = (db.get_person(pid) or {}).get("name", "")
            self.status_var.set(f"连线模式：起点「{name}」，点击目标人物建立连接（右键/ESC 取消）")
        else:
            self.status_var.set("连线模式：点击第一个人物作为起点（右键/ESC 取消）")

    def cancel_linking(self):
        if not self._linking:
            return
        self._linking = False
        self._linking_src = None
        self.web_canvas.config(cursor="")
        self._btn_text(self.link_btn, "连线")
        self.status_var.set("已退出连线模式")

    def _linking_click(self, e):
        sx, sy = self.web_canvas.canvasx(e.x), self.web_canvas.canvasy(e.y)
        pid = self._node_at(sx, sy)
        if pid is None:
            self.cancel_linking()
            return
        if self._linking_src is None:
            self._linking_src = pid
            self._hover(pid, True)
            name = (db.get_person(pid) or {}).get("name", "")
            self.status_var.set(f"已选起点「{name}」，再点一个目标人物（右键/ESC 取消）")
        elif pid == self._linking_src:
            self.cancel_linking()
        else:
            a, b = self._linking_src, pid
            self.cancel_linking()
            self.open_link_dialog(a, b)

    def open_link_dialog(self, a, b):
        """建立 A→B 连接：选关系类型 + 关系分数 + 备注"""
        pa, pb = db.get_person(a), db.get_person(b)
        if not pa or not pb:
            return
        win = tk.Toplevel(self)
        win.title(f"连接关系 · {pa['name']} ↔ {pb['name']}")
        win.configure(bg=BG)
        win.geometry("430x310")
        win.resizable(False, False)
        win.transient(self)

        tk.Frame(win, bg=CYAN, height=3).pack(fill="x")
        cv, box = rounded_card(win, pad=14, radius=14, parent_bg=BG)
        cv.pack(fill="both", expand=True, padx=12, pady=12)
        tk.Label(box, text=f"{pa['name']}  —[关系]→  {pb['name']}",
                 bg=SURFACE, fg=TEXT, font=FONT_MID).pack(pady=(4, 12))
        tk.Label(box, text="关系类型", bg=SURFACE, fg=MUTED,
                 font=FONT_SM, anchor="w").pack(fill="x")
        rel = ttk.Combobox(box, values=db.RELATION_LINK_TYPES, width=16,
                           font=FONT_SM, style="Dark.TCombobox")
        rel.set("认识")
        rel.pack(pady=(2, 10))
        srow = tk.Frame(box, bg=SURFACE); srow.pack(fill="x")
        tk.Label(srow, text="关系分数", bg=SURFACE, fg=MUTED,
                 font=FONT_SM, anchor="w").pack(side="left")
        score_var = tk.StringVar(value="50")
        ttk.Spinbox(srow, from_=0, to=100, width=6, font=FONT_SM,
                    style="Dark.TSpinbox",
                    textvariable=score_var).pack(side="left", padx=8)
        tk.Label(srow, text="越高线越短、颜色越暖、线越粗", bg=SURFACE,
                 fg=MUTED, font=FONT_XS).pack(side="left")
        tk.Label(box, text="备注（可选，如：大学同学 / 项目搭档）", bg=SURFACE,
                 fg=MUTED, font=FONT_SM, anchor="w").pack(fill="x", pady=(10, 0))
        note = self._entry(box, 22, FONT_SM)
        note.pack(fill="x", pady=(2, 12))

        def save():
            try:
                score = int(float(score_var.get()))
            except Exception:
                score = 50
            db.add_link(a, b, rel.get(), note.get().strip(), score)
            self.status_var.set(
                f"已连接「{pa['name']}」—[{rel.get()} · {score}分]→「{pb['name']}」")
            self.refresh_network()
            win.destroy()

        brow = tk.Frame(box, bg=SURFACE)
        brow.pack(fill="x")
        self._btn(brow, "建立连接", save, "primary", icon="icon_link").pack(side="left", padx=(0, 6))
        self._btn(brow, "取消", win.destroy, "ghost").pack(side="left")
        note.bind("<Return>", lambda e: save())
        rel.focus_set()

    # ---------- 连线命中与菜单 ----------
    def _link_at(self, x, y):
        """命中检测：返回坐标附近的连接 id，否则 None"""
        for it in self.web_canvas.find_overlapping(x - 4, y - 4, x + 4, y + 4):
            for t in self.web_canvas.gettags(it):
                if t.startswith("link_"):
                    try:
                        return int(t.split("_")[1])
                    except Exception:
                        pass
        return None

    def _link_menu(self, e, lid):
        lnk = getattr(self, "_link_style", {}).get(lid, {}).get("route", {}).get("lnk")
        if not lnk:
            lnk = next((l for l in db.list_links() if l["id"] == lid), None)
        if not lnk:
            return
        records = lnk.get("records") or [lnk]
        label = lnk.get("label") or db.normalize_link_relation(lnk["relation"])
        m = tk.Menu(self, tearoff=0, bg=SURFACE2, fg=TEXT,
                    activebackground=CYAN, activeforeground="#06222B",
                    relief="flat", font=FONT_SM)
        m.add_command(label=f"{lnk['name_a']} —[{label}]— {lnk['name_b']}",
                      state="disabled")
        m.add_separator()
        m.add_command(label=f"打开「{lnk['name_a']}」资料卡",
                      command=lambda: self.open_profile(lnk["person_a"]))
        m.add_command(label=f"打开「{lnk['name_b']}」资料卡",
                      command=lambda: self.open_profile(lnk["person_b"]))
        m.add_separator()
        if len(records) == 1:
            m.add_command(label="删除此连接",
                          command=lambda i=records[0]["id"]: self.confirm_delete_link(i))
        else:
            deletes = tk.Menu(m, tearoff=0, bg=SURFACE2, fg=TEXT,
                              activebackground=CYAN, activeforeground="#06222B",
                              relief="flat", font=FONT_SM)
            for record in records:
                relation = db.normalize_link_relation(record["relation"])
                note = " ".join((record.get("note") or "").split())
                note = note[:32] + ("…" if len(note) > 32 else "")
                description = (f"{record['name_a']} → {record['name_b']} · {relation}"
                               f" · {note or '无备注'} · ID {record['id']}")
                deletes.add_command(label=description,
                                    command=lambda i=record["id"]: self.confirm_delete_link(i))
            m.add_cascade(label="选择要删除的关系记录", menu=deletes)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def confirm_delete_link(self, lid):
        lnk = next((l for l in db.list_links() if l["id"] == lid), None)
        if not lnk:
            return
        if not self._ask("删除连接",
                         f"确定删除这一条连接记录？\n"
                         f"{lnk['name_a']} → {lnk['name_b']}\n"
                         f"关系：{db.normalize_link_relation(lnk['relation'])}\n"
                         f"ID：{lid}\n备注：{lnk.get('note') or '无'}"):
            return
        db.delete_link(lid)
        self.refresh_network()
        self.status_var.set("已删除一条人物连接")

    # ---------- 右侧：排名 ----------
    def record_contact(self, pid):
        p = db.get_person(pid)
        if not p:
            return
        from datetime import date
        win = tk.Toplevel(self)
        win.title(f"记录联系 · {p['name']}")
        win.configure(bg=SURFACE)
        win.geometry("430x360")
        win.transient(self)
        win.grab_set()
        self._contact_dialog = win
        tk.Label(win, text=f"记录与「{p['name']}」已发生的联系", bg=SURFACE,
                 fg=CYAN, font=FONT_MID).pack(anchor="w", padx=16, pady=(16, 6))
        tk.Label(win, text="填写实际联系的日期、类型和备注，点击保存后记入互动历史。",
                 bg=SURFACE, fg=MUTED, font=FONT_SM,
                 wraplength=390, justify="left").pack(anchor="w", padx=16, pady=(0, 10))
        fields = {}
        for label, key, initial in (("联系日期", "date", date.today().isoformat()),
                                    ("联系类型", "type", "电话")):
            row = tk.Frame(win, bg=SURFACE)
            row.pack(fill="x", padx=16, pady=5)
            tk.Label(row, text=label, width=8, anchor="w", bg=SURFACE,
                     fg=MUTED, font=FONT_SM).pack(side="left")
            if key == "type":
                entry = ttk.Combobox(row, values=("电话", "见面", "聊天", "邮件", "其他"),
                                     font=FONT_SM, style="Dark.TCombobox")
            else:
                entry = self._entry(row, 24, FONT_SM)
            entry.insert(0, initial)
            entry.pack(side="left", fill="x", expand=True)
            fields[key] = entry
        tk.Label(win, text="备注", bg=SURFACE, fg=MUTED,
                 font=FONT_SM).pack(anchor="w", padx=16, pady=(8, 4))
        note = tk.Text(win, height=4, bg=SURFACE2, fg=TEXT, font=FONT_SM,
                       insertbackground=CYAN, relief="flat", wrap="word")
        note.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        fields["note"] = note
        win._contact_fields = fields

        def cancel():
            win.destroy()

        def save():
            type_text = fields["type"].get().strip()
            date_text = fields["date"].get().strip()
            if not type_text:
                self._error("无法保存联系", "请填写联系类型。")
                return
            try:
                parsed_date = date.fromisoformat(date_text)
                if parsed_date.isoformat() != date_text:
                    raise ValueError
            except ValueError:
                self._error("无法保存联系", "请填写有效的日期，格式为 YYYY-MM-DD。")
                return
            try:
                db.add_interaction(pid, type_text,
                                   note=note.get("1.0", "end-1c").strip(), date=date_text)
            except Exception as ex:
                self._error("保存联系失败", str(ex))
                return
            win.destroy()
            for child in self.winfo_children():
                if getattr(child, "_contact_pid", None) == pid:
                    child._refresh_interactions()
            self.refresh_cards()
            frozen = getattr(self, "_freeze_layout", False)
            self._freeze_layout = True
            try:
                self.refresh_network()
            finally:
                self._freeze_layout = frozen
            self.status_var.set(f"已记录与「{p['name']}」的{type_text}")

        win._save_contact = save
        win._cancel_contact = cancel
        win.protocol("WM_DELETE_WINDOW", cancel)
        win.bind("<Escape>", lambda e: cancel())
        buttons = tk.Frame(win, bg=SURFACE)
        buttons.pack(fill="x", padx=16, pady=(0, 14))
        self._btn(buttons, "保存联系", save, "primary", icon="icon_save").pack(side="left")
        self._btn(buttons, "取消", cancel, "ghost").pack(side="left", padx=8)
        fields["type"].focus_set()
        return win

    def open_profile(self, pid):
        p = db.get_person(pid)
        if not p:
            return
        win = tk.Toplevel(self)
        win.title(f"资料卡 · {p['name']}")
        win.configure(bg=SURFACE)
        win.geometry("480x700")
        win.minsize(440, 540)
        win.transient(self)

        score, det, tier = compute_score(p)
        color = TIER_COLOR.get(tier, MUTED)
        tk.Frame(win, bg=color, height=4).pack(fill="x")

        # ---- 头部：圆角卡（头像 + 姓名 + 层级胶囊） ----
        hcv, hd = rounded_card(win, pad=14, radius=14, parent_bg=SURFACE)
        hcv.pack(fill="x", padx=14, pady=(12, 4))
        av = images_util.load_circle_photo(p.get("avatar"), (76, 76)) \
            or images_util.load_photo(p.get("avatar"), (76, 76))
        if av:
            self._detail_img = av
            tk.Label(hd, image=av, bg=SURFACE).pack(side="left")
        else:
            cv = tk.Canvas(hd, width=76, height=76, bg=SURFACE,
                           highlightthickness=0, bd=0)
            cv.create_oval(4, 4, 72, 72, outline="#1C3A4A", width=2)
            cv.create_oval(9, 9, 67, 67, fill=color, outline="")
            cv.create_text(38, 38, text=p["name"][:1], fill="#06222B",
                           font=(UI_FONT, 20, "bold"))
            cv.pack(side="left")
        info = tk.Frame(hd, bg=SURFACE)
        info.pack(side="left", padx=14, anchor="n")
        tk.Label(info, text=p["name"], bg=SURFACE, fg=TEXT,
                 font=(UI_FONT, 17, "bold")).pack(anchor="w")
        # 层级 + 分数胶囊
        pcv = tk.Canvas(info, height=26, bg=SURFACE, highlightthickness=0, bd=0)
        ptxt = f"{tier} · {score} 分"
        pw = int(len(ptxt) * 8.5) + 26
        pcv.configure(width=pw)
        pcv.create_polygon(_round_pts(1, 1, pw - 2, 24, 12), smooth=True,
                           splinesteps=20, fill="#F0F4FA", outline=color,
                           width=1)
        pcv.create_text(pw / 2, 13, text=ptxt, fill=color, font=FONT_SM)
        pcv.pack(anchor="w", pady=(6, 0))
        if p.get("source") == "wechat":
            self._label(info, "绿泡泡拉取", fg=GREEN, bg=SURFACE, font=FONT_SM,
                       icon="icon_conn").pack(anchor="w", pady=(4, 0))
        acts = tk.Frame(hd, bg=SURFACE)
        acts.pack(side="right", anchor="n", pady=2)
        self._btn(acts, "更换形象", lambda: self.change_avatar(pid),
                  "ghost", icon="icon_avatar").pack(side="top", fill="x", pady=(0, 4))
        self._btn(acts, "重置位置", lambda: self.reset_node_pos(pid),
                  "ghost", icon="icon_refresh").pack(side="top", fill="x")

        # ---- 中部：可滚动表单 ----
        sc_host = tk.Frame(win, bg=SURFACE)
        sc_host.pack(fill="both", expand=True, padx=(6, 0))
        sc = tk.Canvas(sc_host, bg=SURFACE, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(sc_host, orient="vertical", command=sc.yview)
        body = tk.Frame(sc, bg=SURFACE)
        body.bind("<Configure>", lambda e: sc.configure(scrollregion=body.bbox("all")))
        sc.create_window((0, 0), window=body, anchor="nw")
        sc.configure(yscrollcommand=vsb.set)
        sc.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _wheel(e):
            try:
                sc.yview_scroll(int(-e.delta / 120), "units")
            except Exception:
                pass
        sc.bind("<MouseWheel>", _wheel)
        body.bind("<MouseWheel>", _wheel)

        F = {}
        def field(label, key, value="", width=24, combo=None):
            rf = tk.Frame(body, bg=SURFACE); rf.pack(fill="x", pady=3, padx=12)
            tk.Label(rf, text=label, bg=SURFACE, fg=MUTED, font=FONT_SM,
                     width=7, anchor="w").pack(side="left")
            if combo:
                w = ttk.Combobox(rf, values=combo, width=width, font=FONT_SM,
                                 style="Dark.TCombobox"); w.set(value)
            else:
                w = self._entry(rf, width, FONT_SM); w.insert(0, str(value))
            w.pack(side="left", fill="x", expand=True); F[key] = w

        def pair(l1, k1, v1, l2, k2, v2):
            rf = tk.Frame(body, bg=SURFACE); rf.pack(fill="x", pady=3, padx=12)
            for label, key, val in [(l1, k1, v1), (l2, k2, v2)]:
                tk.Label(rf, text=label, bg=SURFACE, fg=MUTED, font=FONT_SM,
                         width=6, anchor="w").pack(side="left")
                w = self._entry(rf, 8, FONT_SM); w.insert(0, str(val))
                w.pack(side="left", fill="x", expand=True, padx=(0, 5)); F[key] = w

        field("关系", "relation", p["relation_type"], combo=db.RELATION_TYPES)
        pair("亲密", "closeness", p["closeness"], "情感", "sentiment", p["sentiment_weight"])
        field("引荐人", "intro_by", p.get("intro_by") or "")
        pair("电话", "phone", p.get("phone") or "", "生日", "birthday", p.get("birthday") or "")
        field("收货地址", "address", p.get("address") or "", width=26)
        field("喜好", "prefs", "、".join(json.loads(p.get("prefs") or "[]")), width=26)
        field("禁忌", "taboo", "、".join(json.loads(p.get("taboo") or "[]")), width=26)
        imp = json.loads(p.get("important_dates") or "[]")
        field("重要日期", "important",
              ", ".join(f"{d.get('label','')}:{d.get('date','')}" for d in imp), width=26)
        field("备注", "notes", clean_note(p.get("notes") or ""), width=26)

        # 记忆笔记入口
        memo = p.get("memo_md") or ""
        mrow = tk.Frame(body, bg=SURFACE)
        mrow.pack(fill="x", pady=(8, 2), padx=12)
        self._label(mrow, f"记忆笔记（{len(memo)} 字）", fg=PURPLE, bg=SURFACE,
                   font=FONT_MID, icon="icon_edit").pack(side="left")
        self._btn(mrow, "编辑", lambda: self.open_memo(pid, win), "ghost", icon="icon_edit").pack(side="right")
        self._btn(mrow, "关系程度", lambda: self.open_slider(pid),
                  "ghost", icon="icon_slider").pack(side="right", padx=(0, 6))

        # 关系连接（可增删，改动即时刷新关系网络）
        conn_host = tk.Frame(body, bg=SURFACE)
        conn_host.pack(fill="x", padx=12, pady=(4, 2))

        def draw_conns():
            for w in conn_host.winfo_children():
                w.destroy()
            head = tk.Frame(conn_host, bg=SURFACE)
            head.pack(fill="x")
            self._label(head, "关系连接", fg=CYAN, bg=SURFACE, font=FONT_MID,
                       icon="icon_link").pack(side="left")
            self._btn(head, "连接人物",
                      lambda: (win.destroy(), self.start_linking_from(pid)),
                      "ghost", icon="icon_add").pack(side="right")
            lks = db.links_of(pid)
            if not lks:
                tk.Label(conn_host, text="（还没有连接：点「连接人物」，或在关系网络中右键其他人物连线）",
                         bg=SURFACE, fg=MUTED, font=FONT_SM,
                         anchor="w").pack(fill="x", pady=2)
                return
            for lk in lks:
                mine_a = lk["person_a"] == pid
                other_name = lk["name_b"] if mine_a else lk["name_a"]
                arrow = "→" if mine_a else "←"
                lscore = lk.get("score")
                lscore = 50 if lscore is None else int(lscore)
                col = _score_color(lscore)
                holder = {"btn": None}

                def _draw(cv, w, lk=lk, arrow=arrow, lscore=lscore, col=col,
                          other_name=other_name, holder=holder):
                    old = holder.get("btn")
                    if old is not None:
                        try:
                            old.destroy()
                        except Exception:
                            pass
                    cv.create_text(14, 16,
                                   text=f"{arrow} [{db.normalize_link_relation(lk['relation'])} · {lscore}分]",
                                   fill=col, font=FONT_SM, anchor="w",
                                   tags=("rowcontent",))
                    cv.create_text(186, 16, text=other_name, fill=TEXT,
                                   font=FONT_SM, anchor="w",
                                   tags=("rowcontent",))
                    if lk.get("note"):
                        cv.create_text(186 + len(other_name) * 10 + 14, 16,
                                       text=f"· {lk['note']}", fill=MUTED,
                                       font=FONT_SM, anchor="w",
                                       tags=("rowcontent",))
                    b = self._btn(cv, "断开",
                                  lambda i=lk["id"]: (db.delete_link(i),
                                                      draw_conns(),
                                                      self.refresh_network()),
                                  "danger")
                    holder["btn"] = b
                    cv.create_window(w - 14, 16, window=b, anchor="e",
                                     tags=("rowcontent",))

                row = row_card(conn_host, height=32, on_draw=_draw,
                               bg=SURFACE2, border=BORDER, radius=9)
                row.pack(fill="x", pady=2)

        draw_conns()

        # 分数拆解
        self._label(body, "社交价值拆解", fg=CYAN, bg=SURFACE, font=FONT_MID,
                   icon="icon_chart").pack(fill="x", pady=(8, 2), padx=12)
        eb = scrolledtext.ScrolledText(body, height=6, bg=SURFACE2, fg=MUTED,
                                       font=FONT_SM, bd=0, highlightthickness=1,
                                       highlightbackground=BORDER)
        eb.pack(fill="x", padx=12, pady=(0, 6))
        d = days_to_next_event(p.get("birthday"), p.get("important_dates"))
        eb.insert(tk.END, f"下次重要日期：{d if d is not None else '—'} 天后\n")
        for k, v in det.items():
            eb.insert(tk.END, f"  {k}: {v:.2f}\n")
        eb.config(state="disabled")

        # 只重绘互动区域，保留资料表单尚未保存的编辑。
        interaction_host = tk.Frame(body, bg=SURFACE)
        interaction_host.pack(fill="x", padx=12, pady=(6, 8))

        def draw_interactions():
            for child in interaction_host.winfo_children():
                child.destroy()
            all_interactions = db.list_interactions(pid)
            self._label(interaction_host, f"互动记录（{len(all_interactions)}）",
                       fg=ORANGE, bg=SURFACE, font=FONT_MID,
                       icon="icon_log").pack(fill="x", pady=(0, 2))
            for it in all_interactions[:6]:
                row = tk.Frame(interaction_host, bg=SURFACE2, highlightthickness=1,
                               highlightbackground=BORDER)
                row.pack(fill="x", pady=2)
                tk.Label(row, text=it["date"], bg=SURFACE2, fg=MUTED,
                         font=FONT_SM).pack(side="left", padx=8, pady=4)
                tk.Label(row, text=it["type"], bg=SURFACE2, fg=TEXT,
                         font=FONT_SM).pack(side="left", pady=4)
                tk.Label(row, text=it["note"] or "", bg=SURFACE2, fg=MUTED,
                         font=FONT_SM, anchor="w").pack(side="left", padx=6, pady=4)
            if not all_interactions:
                tk.Label(interaction_host, text="（暂无互动记录）", bg=SURFACE,
                         fg=MUTED, font=FONT_SM, anchor="w").pack(fill="x")

        win._contact_pid = pid
        win._refresh_interactions = draw_interactions
        draw_interactions()

        def save():
            old_raw = p.get("notes") or ""
            data = {
                "name": p["name"], "relation_type": F["relation"].get(),
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
                "source": p.get("source", "manual"),
                "avatar": p.get("avatar"),
            }
            db.save_person(data, pid)
            self.status_var.set(f"已保存「{p['name']}」")
            self.refresh_cards(); self.refresh_combos(); self.refresh_network()
            win.destroy()

        brow = tk.Frame(win, bg=SURFACE)
        brow.pack(fill="x", padx=16, pady=(4, 14))
        self._btn(brow, "保存", save, "primary", icon="icon_save").pack(side="left", padx=(0, 6))
        self._btn(brow, "送礼", lambda: (win.destroy(), self.go_gift(pid)), icon="icon_gift").pack(side="left", padx=(0, 6))
        self._btn(brow, "祝福", lambda: (win.destroy(), self.go_greet(pid)), icon="icon_star").pack(side="left")
        self._btn(brow, "记录联系", lambda: self.record_contact(pid),
                  "ghost", icon="icon_log").pack(side="left", padx=(6, 0))

    def confirm_delete(self, pid):
        p = db.get_person(pid)
        if not p:
            return
        if self._ask("确认删除", f"删除「{p['name']}」及其所有记录？"):
            db.delete_person(pid)
            self.status_var.set(f"已删除「{p['name']}」")
            self.refresh_cards(); self.refresh_combos(); self.refresh_network()
            self._conn_after_person_delete(p["name"])

    def _conn_after_person_delete(self, name):
        """人物删除后同步绿泡泡连接器：被删者移出「已拉取」集合，
        卡片恢复「拉取」按钮，可重新导入（v0.19 用户反馈）"""
        if not hasattr(self, "conn_imported"):
            return                      # 连接器页尚未构建
        self.conn_imported.discard(name)
        try:
            self._render_conn_grid()
        except Exception:
            pass

    # ---------- 记忆笔记 md 编辑器 ----------
    def open_memo(self, pid, parent=None):
        p = db.get_person(pid)
        if not p:
            return
        win = tk.Toplevel(self)
        win.title(f"记忆笔记 · {p['name']}")
        win.configure(bg=SURFACE)
        win.geometry("560x520")
        win.transient(parent or self)
        win.grab_set()

        tk.Label(win, text=f"关于 {p['name']} 的记忆笔记", bg=SURFACE,
                 fg=PURPLE, font=FONT_MID).pack(anchor="w", padx=14, pady=(12, 4))
        tk.Label(win, text="用 Markdown 记录：共同经历、喜好细节、上次聊到的话题、送礼反馈…",
                 bg=SURFACE, fg=MUTED, font=FONT_SM).pack(anchor="w", padx=14)

        txt = scrolledtext.ScrolledText(win, bg=SURFACE2, fg=TEXT, font=("Consolas", 10),
                                        bd=0, highlightthickness=1, highlightbackground=BORDER,
                                        insertbackground=CYAN)
        txt.pack(fill="both", expand=True, padx=14, pady=10)
        txt.insert(tk.END, p.get("memo_md") or "")

        def save():
            db.set_memo(pid, txt.get("1.0", tk.END).strip())
            self.status_var.set(f"记忆笔记已保存（{p['name']}）")
            win.destroy()

        brow = tk.Frame(win, bg=SURFACE); brow.pack(fill="x", padx=14, pady=(0, 12))
        self._btn(brow, "保存笔记", save, "primary", icon="icon_save").pack(side="left")
        self._btn(brow, "取消", win.destroy, "ghost").pack(side="left", padx=6)

    # ---------- 关系程度滑块 ----------
    def open_slider(self, pid):
        p = db.get_person(pid)
        if not p:
            return
        win = tk.Toplevel(self)
        win.title(f"调整关系程度 · {p['name']}")
        win.configure(bg=SURFACE)
        win.geometry("420x340")
        win.transient(self)
        win.grab_set()

        tk.Frame(win, bg=CYAN, height=3).pack(fill="x")
        tk.Label(win, text=f"调整与「{p['name']}」的关系", bg=SURFACE,
                 fg=CYAN, font=FONT_MID).pack(anchor="w", padx=16, pady=(12, 8))

        # v0.21：滑块预览每次拖动都算分——ctx 预取一次，回调零查库
        ctx = scoring.score_context()
        cur_score, _, cur_tier = compute_score(p, ctx)
        score_lbl = tk.Label(win, text=f"当前：{cur_score} 分 · {cur_tier}",
                             bg=SURFACE, fg=TIER_COLOR.get(cur_tier, MUTED), font=FONT_MID)
        score_lbl.pack(anchor="w", padx=16, pady=(0, 10))

        def make_slider(label, key, val):
            rf = tk.Frame(win, bg=SURFACE); rf.pack(fill="x", padx=16, pady=4)
            tk.Label(rf, text=label, bg=SURFACE, fg=TEXT, font=FONT, width=8,
                     anchor="w").pack(side="left")
            v = tk.DoubleVar(value=float(val))
            s = ttk.Scale(rf, from_=0, to=1, variable=v, orient="horizontal",
                          command=lambda x: on_change())
            s.pack(side="left", fill="x", expand=True, padx=8)
            v_lbl = tk.Label(rf, text=f"{v.get():.2f}", bg=SURFACE, fg=CYAN,
                             font=FONT_SM, width=5)
            v_lbl.pack(side="left")
            return v, v_lbl

        clos_v, clos_lbl = make_slider("亲密度", "closeness", p.get("closeness", 0.5))
        sent_v, sent_lbl = make_slider("情感权重", "sentiment", p.get("sentiment_weight", 0.5))

        def on_change():
            clos_lbl.config(text=f"{clos_v.get():.2f}")
            sent_lbl.config(text=f"{sent_v.get():.2f}")
            # 实时预览分数
            tmp = dict(p)
            tmp["closeness"] = clos_v.get()
            tmp["sentiment_weight"] = sent_v.get()
            s, _, t = compute_score(tmp, ctx)
            score_lbl.config(text=f"预览：{s} 分 · {t}",
                             fg=TIER_COLOR.get(t, MUTED))

        def save():
            old_raw = p.get("notes") or ""
            data = {
                "name": p["name"], "relation_type": p["relation_type"],
                "closeness": clos_v.get(), "sentiment_weight": sent_v.get(),
                "intro_by": p.get("intro_by", ""), "address": p.get("address", ""),
                "phone": p.get("phone", ""), "birthday": p.get("birthday", ""),
                "important_dates": json.dumps(json.loads(p.get("important_dates") or "[]"), ensure_ascii=False),
                "prefs": p.get("prefs", "[]"), "taboo": p.get("taboo", "[]"),
                "notes": p.get("notes", ""), "source": p.get("source", "manual"),
                "avatar": p.get("avatar"),
            }
            db.save_person(data, pid)
            self.status_var.set(f"已调整「{p['name']}」的关系程度")
            self.refresh_cards(); self.refresh_combos(); self.refresh_network()
            win.destroy()

        brow = tk.Frame(win, bg=SURFACE); brow.pack(fill="x", padx=16, pady=14)
        self._btn(brow, "保存", save, "primary", icon="icon_save").pack(side="left", padx=(0, 6))
        self._btn(brow, "取消", win.destroy, "ghost").pack(side="left")

    # ============================================================
    #  🃏 人脉卡片（保留）
    # ============================================================
    def open_pay_config(self):
        """Vibe Pay（支付宝当面付）配置窗：APPID/网关/应用私钥/支付宝公钥
        → 测试配置（解析两把钥匙）→ 保存写 pay_config.json。"""
        win = tk.Toplevel(self)
        win.title("Vibe Pay 支付配置")
        win.configure(bg=BG)
        win.resizable(False, False)
        W, H = 620, 600
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{W}x{H}+{max(0, (sw - W) // 2)}+{max(0, (sh - H) // 2)}")
        win.transient(self)

        head = self._label(win, "Vibe Pay 支付配置", fg=ORANGE, bg=BG,
                           font=FONT_MID, icon="icon_vibepay")
        if head is not None:
            head.pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(win, text="支付宝开放平台 · 当面付：预下单生成二维码，程序轮询确认到账",
                 bg=BG, fg=MUTED, font=FONT_SM).pack(anchor="w", padx=16)

        form = tk.Frame(win, bg=BG)
        form.pack(fill="both", expand=True, padx=16, pady=8)

        cfg = payment.load_pay_config()["vibe"]

        def _row(label):
            tk.Label(form, text=label, bg=BG, fg=MUTED, font=FONT_SM,
                     anchor="w").pack(fill="x", pady=(8, 2))

        _row("APPID（支付宝开放平台应用 ID）")
        app_ent = self._entry(form, 40, FONT_SM)
        app_ent.pack(fill="x", ipady=4)
        app_ent.insert(0, cfg.get("app_id", ""))

        _row("网关地址")
        gw_ent = self._entry(form, 40, FONT_SM)
        gw_ent.pack(fill="x", ipady=4)
        gw_ent.insert(0, cfg.get("gateway", ""))

        _row("应用私钥（PKCS#8 / PKCS#1 PEM，含 BEGIN/END 行）")
        priv_txt = scrolledtext.ScrolledText(
            form, height=7, bg=SURFACE, fg=TEXT, insertbackground=CYAN,
            font=("Consolas", 9), bd=0, highlightthickness=1,
            highlightbackground=BORDER)
        priv_txt.pack(fill="x")
        priv_txt.insert("1.0", cfg.get("private_key", ""))

        _row("支付宝公钥（SPKI PEM，验签用）")
        pub_txt = scrolledtext.ScrolledText(
            form, height=6, bg=SURFACE, fg=TEXT, insertbackground=CYAN,
            font=("Consolas", 9), bd=0, highlightthickness=1,
            highlightbackground=BORDER)
        pub_txt.pack(fill="x")
        pub_txt.insert("1.0", cfg.get("alipay_public_key", ""))

        st = tk.Label(win, text="", bg=BG, fg=MUTED, font=FONT_SM,
                      anchor="w", wraplength=560, justify="left")
        st.pack(fill="x", padx=16, pady=(10, 0))

        def _collect():
            return dict(cfg, app_id=app_ent.get().strip(),
                        gateway=gw_ent.get().strip(),
                        private_key=priv_txt.get("1.0", "end").strip(),
                        alipay_public_key=pub_txt.get("1.0", "end").strip())

        def _test():
            trial = payment.VibePayChannel(_collect())
            ok, why = trial.ready()
            st.config(text=("配置可用" if ok else "配置未通过") + ("：" + why if why else ""), 
                      fg=GREEN if ok else RED)
            win.update()

        def _save():
            allcfg = payment.load_pay_config()
            allcfg["vibe"].update(_collect())
            payment.save_pay_config(allcfg)
            ok, why = payment.VibePayChannel(_collect()).ready()
            self.status_var.set(
                "Vibe Pay 配置已保存（%s）" % ("可用" if ok else "未就绪：%s" % why))
            win.destroy()

        brow = tk.Frame(win, bg=BG)
        brow.pack(pady=12)
        self._btn(brow, "测试配置", _test, "ghost", icon="icon_star").pack(
            side="left", padx=6)
        self._btn(brow, "保存并关闭", _save, "primary", icon="icon_save").pack(
            side="left", padx=6)
        tk.Label(win, text="私钥只存本机 pay_config.json，不写入数据库与备份；迁移电脑时单独安全转移。",
                 bg=BG, fg=MUTED, font=FONT_XS).pack(pady=(0, 10))

    def open_scene_manager(self):
        """v0.65 使用场景管理：新建、改名（含预设）、删除、上下排序。

        场景定义存在当前账号的 scenes 表，按账号隔离。预设「办公/学校/家庭」
        与普通场景一视同仁，都可改名删除；内置「全部」是视图，不进表。
        删除时会把挂在该场景的人自动改挂到其它场景，人物本身不会丢。
        """
        win = tk.Toplevel(self)
        win.title("使用场景管理")
        win.configure(bg=BG)
        win.resizable(False, False)
        W, H = 640, 560
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{W}x{H}+{max(0, (sw - W) // 2)}+{max(0, (sh - H) // 2)}")
        win.transient(self)

        head = self._label(win, "使用场景管理", fg=CYAN, bg=BG,
                           font=FONT_MID, icon="icon_slider")
        if head is not None:
            head.pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(win, text="场景按账号独立保存。预设场景也能改名、删除；「全部」是内置视图，不能改动。",
                 bg=BG, fg=MUTED, font=FONT_SM, wraplength=W - 34,
                 justify="left").pack(anchor="w", padx=16, pady=(2, 8))

        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=16)
        lb = tk.Listbox(body, width=34, height=8, bg=SURFACE, fg=TEXT,
                        selectbackground=CYAN, selectforeground=BG,
                        font=FONT_SM, bd=0, highlightthickness=1,
                        highlightbackground=BORDER, activestyle="none")
        lb.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=lb.yview)
        sb.pack(side="left", fill="y")
        lb.configure(yscrollcommand=sb.set)

        ops = tk.Frame(body, bg=BG)
        ops.pack(side="left", padx=(12, 0))
        names, dirty = [], {"v": False}

        def _status(txt, warn=False):
            status.config(text=txt, fg=ORANGE if warn else GREEN)

        def _refresh(select=None):
            names[:] = db.list_scenes()
            lb.delete(0, "end")
            for n in names:
                cnt = db.scene_people_count(n)
                tag = "预设" if n in db.SCENES else "自定义"
                lb.insert("end", "  %s   · %d 人 · %s" % (n, cnt, tag))
            if select and select in names:
                lb.selection_set(names.index(select))

        def _sel():
            cur = lb.curselection()
            return names[cur[0]] if cur else None

        def _on_select(_e=None):
            n = _sel()
            if n:
                name_ent.delete(0, "end")
                name_ent.insert(0, n)
        lb.bind("<<ListboxSelect>>", _on_select)

        def _add():
            ok, msg = db.add_scene(name_ent.get())
            if not ok:
                _status(msg, True)
                return
            name_ent.delete(0, "end")
            dirty["v"] = True
            _refresh(msg)
            _status("已新增场景「%s」" % msg)

        def _rename():
            n = _sel()
            if not n:
                _status("先选中一个场景", True)
                return
            v = name_ent.get().strip()
            if not v or v == n:
                _status("先在输入框里改成新名字", True)
                return
            ok, msg = db.rename_scene(n, v)
            if not ok:
                _status(msg, True)
                return
            name_ent.delete(0, "end")
            dirty["v"] = True
            _refresh(v)
            if getattr(self, "_scene", db.BUILTIN_SCENE) == n:
                self._scene = v
                db.save_scene(v)
            _status("已改名：%s" % msg)

        def _delete():
            n = _sel()
            if not n:
                _status("先选中一个场景", True)
                return
            cnt = db.scene_people_count(n)
            tip = "删除场景「%s」？" % n
            if cnt:
                tip += "\n有 %d 人挂在这个场景，他们会自动改挂到其它场景，人物不会丢。" % cnt
            if not self._ask("删除场景", tip):
                return
            moved = db.delete_scene(n)
            dirty["v"] = True
            _refresh()
            name_ent.delete(0, "end")
            if getattr(self, "_scene", db.BUILTIN_SCENE) == n:
                self._scene = db.BUILTIN_SCENE
            _status("已删除「%s」，处理 %d 人" % (n, moved))

        def _move(delta):
            n = _sel()
            if n and db.move_scene(n, delta):
                dirty["v"] = True
                _refresh(n)
                _status("顺序已调整")

        self._btn(ops, "改名", _rename, "ghost").pack(fill="x", pady=3)
        self._btn(ops, "↑ 上移", lambda: _move(-1), "ghost").pack(fill="x", pady=3)
        self._btn(ops, "↓ 下移", lambda: _move(1), "ghost").pack(fill="x", pady=3)
        self._btn(ops, "删除", _delete, "danger").pack(fill="x", pady=(14, 3))

        form = tk.Frame(win, bg=BG)
        form.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(form, text="名称", bg=BG, fg=MUTED, font=FONT_SM).pack(side="left", padx=(0, 6))
        name_ent = self._entry(form, 20, FONT_SM)
        name_ent.configure(bg="#EAF3FD", highlightbackground=CYAN,
                           highlightthickness=1)
        name_ent.pack(side="left", ipady=5, padx=(0, 8))
        self._btn(form, "＋ 新增", _add, "primary", icon="icon_add").pack(side="left", padx=3)
        self._btn(form, "保存改名", _rename, "ghost", icon="icon_save").pack(side="left", padx=3)

        tk.Label(win, text="选中场景会自动填入原名，改好点「保存改名」；新增前先清空输入框。删除预设场景同样生效。",
                 bg=BG, fg=MUTED, font=FONT_XS, wraplength=W - 34,
                 justify="left").pack(anchor="w", padx=16, pady=(6, 0))
        status = self._label(win, "", fg=GREEN, bg=BG, font=FONT_SM)
        status.pack(anchor="w", padx=16, pady=(4, 0))

        def _close():
            win.destroy()
            if dirty["v"]:
                self._rebuild_scene_cards(refresh=True)
                self.status_var.set("场景已更新")
        self._btn(win, "完成", _close, "primary", icon="icon_save").pack(pady=(6, 10))
        win.protocol("WM_DELETE_WINDOW", _close)
        _refresh()

    # ---------- 云端账号与服务端模型计费 ----------
    def open_cloud_account_dialog(self):
        """登录后启用云端计费；模型 API Key 只在服务端，不进入此窗口。"""
        win = tk.Toplevel(self)
        win.title("云端账号与 AI")
        win.configure(bg=BG)
        win.geometry("520x500+%d+%d" % (max(0, self.winfo_x() + 300),
                                        max(0, self.winfo_y() + 130)))
        win.resizable(False, False)
        self._label(win, "云端账号与 AI", fg=CYAN, bg=BG, font=FONT_MID,
                    icon="ai_avatar").pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(win, text="登录后，关系判断与祝福语由服务端预扣积分、调用模型、失败退款。",
                 bg=BG, fg=MUTED, font=FONT_SM, wraplength=480,
                 justify="left").pack(anchor="w", padx=18, pady=(0, 10))
        cv, card = rounded_card(win, pad=14, radius=14, parent_bg=BG)
        cv.pack(fill="both", expand=True, padx=14, pady=8)
        cfg = cloud_client.load_config()

        def field(label, value="", show=None):
            row = tk.Frame(card, bg=SURFACE)
            row.pack(fill="x", pady=5)
            tk.Label(row, text=label, bg=SURFACE, fg=TEXT, font=FONT_SM,
                     width=10, anchor="w").pack(side="left")
            entry = tk.Entry(row, bg=SURFACE2, fg=TEXT, insertbackground=CYAN,
                             bd=0, font=FONT, show=show)
            entry.pack(side="left", fill="x", expand=True, ipady=5)
            entry.insert(0, value)
            return entry

        server_entry = field("服务器", cfg["server_url"])
        user_entry = field("用户名", cloud_client.current_user().get("username", ""))
        pass_entry = field("密码", "", "•")
        state_lbl = tk.Label(card, text="", bg=SURFACE, fg=MUTED,
                             font=FONT_SM, wraplength=440, justify="left")
        state_lbl.pack(anchor="w", pady=(10, 6))

        def _save_url():
            url = server_entry.get().strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                raise ValueError("服务器地址必须以 http:// 或 https:// 开头")
            cloud_client.save_config({"server_url": url})

        def _refresh(message=""):
            active = cloud_client.is_active()
            user = cloud_client.current_user()
            text = message or ("云端已启用：%s · 余额 %s 积分" % (
                user.get("username", "已登录"), cloud_client.cached_balance())
                if active else "当前为本地模式；登录后自动启用云端计费")
            state_lbl.config(text=text, fg=GREEN if active else MUTED)
            self.refresh_points_chip()

        def _test():
            try:
                _save_url()
                health = cloud_client.health()
                mode = health.get("model_mode")
                state_lbl.config(text="连接成功 · 服务端模型模式：%s" % mode, fg=GREEN)
            except Exception as ex:
                state_lbl.config(text=str(ex), fg=RED)

        def _auth(action):
            try:
                _save_url()
                result = (cloud_client.register if action == "register" else cloud_client.login)(
                    user_entry.get().strip(), pass_entry.get())
                pass_entry.delete(0, tk.END)
                _refresh("云端已启用：%s · 余额 %s 积分" % (
                    result["user"]["username"], result["balance"]))
                self.status_var.set("云端账号已登录，AI 调用将由服务端计费")
            except Exception as ex:
                state_lbl.config(text=str(ex), fg=RED)

        def _toggle():
            try:
                cloud_client.set_enabled(not cloud_client.is_active())
                _refresh()
            except Exception as ex:
                state_lbl.config(text=str(ex), fg=RED)

        def _logout():
            cloud_client.logout()
            pass_entry.delete(0, tk.END)
            _refresh("已退出云端账号，恢复本地模式")

        row1 = tk.Frame(card, bg=SURFACE); row1.pack(fill="x", pady=4)
        self._btn(row1, "测试连接", _test, "ghost", icon="icon_refresh").pack(side="left")
        self._btn(row1, "注册并登录", lambda: _auth("register"), "primary").pack(side="left", padx=6)
        self._btn(row1, "登录", lambda: _auth("login"), "primary").pack(side="left")
        row2 = tk.Frame(card, bg=SURFACE); row2.pack(fill="x", pady=4)
        self._btn(row2, "启用/停用云端", _toggle, "ghost").pack(side="left")
        self._btn(row2, "退出账号", _logout, "ghost").pack(side="left", padx=6)
        self._btn(row2, "本地模型 API 配置", self.open_llm_config, "ghost").pack(side="left")
        tk.Label(card, text="新账号自动获得 100 体验积分。密码仅用于本次登录，不写入本地配置；"
                            "正式公网部署请使用 HTTPS。",
                 bg=SURFACE, fg=MUTED, font=FONT_XS, wraplength=440,
                 justify="left").pack(anchor="w", pady=(10, 2))
        self._btn(win, "完成", win.destroy, "ghost").pack(pady=(0, 10))
        _refresh()

    # ---------- v0.68 积分域：商品下单 / 查单 / 幂等入账 ----------
    def open_points_dialog(self):
        """积分充值闭环：体验领取、积分包下单、收银台、轮询查单与订单记录。"""
        import threading
        import webbrowser

        win = tk.Toplevel(self)
        win.title("积分充值与订单")
        win.configure(bg=BG)
        win.geometry("900x760+%d+%d" % (max(0, self.winfo_x() + 220),
                                        max(0, self.winfo_y() + 60)))
        win.minsize(780, 680)
        self._label(win, "积分账户", fg=ORANGE, bg=BG, font=FONT_MID,
                    icon="icon_star").pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, text="真实大模型 API：关系洞察 10 积分 · 祝福语 2 积分；"
                            "本地模板与本地检索免费。",
                 bg=BG, fg=MUTED, font=FONT_SM).pack(anchor="w", padx=16, pady=(2, 8))

        bal_lbl = self._label(win, "", bg=BG, font=FONT_MID)
        bal_lbl.pack(anchor="w", padx=16, pady=(0, 6))

        cv, card = rounded_card(win, pad=12, radius=14, parent_bg=BG)
        cv.pack(fill="x", padx=14, pady=6)
        free_state = {"done": bool(db.get_meta("points_free_gift_claimed"))}
        state = {"current": None, "stop": threading.Event(),
                 "queue": queue.Queue(), "closed": False}

        def _refresh_bal():
            if cloud_client.is_active():
                bal_lbl.config(text="云端余额：%s 积分（%s）" % (
                    cloud_client.cached_balance(),
                    cloud_client.current_user().get("username", "已登录")))
            else:
                bal_lbl.config(text="当前余额：%d 积分" % db.points_balance())
            if hasattr(self, "refresh_points_chip"):
                self.refresh_points_chip()          # 页头「积分 N」同步

        def _claim():
            if cloud_client.is_active():
                self.status_var.set("云端新账号注册时已自动获得 100 体验积分")
                _refresh_bal()
                return
            if free_state["done"]:
                self.status_var.set("体验积分已领取过")
                return
            try:
                bal = db.points_record(None, db.POINTS_FREE_GIFT,
                                       "体验积分赠送", "grant",
                                       "FREE100:" + db.current_account())
            except ValueError:
                free_state["done"] = True
                db.set_meta("points_free_gift_claimed", "1")
                self.status_var.set("体验积分已领取过，未重复入账")
                return
            db.set_meta("points_free_gift_claimed", "1")
            free_state["done"] = True
            _refresh_bal()
            _refresh_ledger()
            self.status_var.set("已领取 100 体验积分（余额 %d）" % bal)

        head = tk.Frame(card, bg=SURFACE)
        head.pack(fill="x", pady=(2, 8))
        self._btn(head, "领取 100 体验积分", _claim, "ghost",
                  icon="icon_star").pack(side="left")
        self._btn(head, "支付设置", self.open_pay_config, "ghost",
                  icon="icon_vibepay").pack(side="right")
        pay_state = tk.Label(head, text="", bg=SURFACE, fg=MUTED, font=FONT_SM)
        pay_state.pack(side="left", padx=12)

        form = tk.Frame(card, bg=SURFACE)
        form.pack(fill="x", pady=4)
        tk.Label(form, text="积分包", bg=SURFACE, fg=TEXT,
                 font=FONT_SM).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        product_cb = ttk.Combobox(form, values=points.product_choices(),
                                  state="readonly", width=31, font=FONT)
        product_cb.current(1)
        product_cb.grid(row=0, column=1, sticky="ew", pady=5)
        tk.Label(form, text="支付通道", bg=SURFACE, fg=TEXT,
                 font=FONT_SM).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        channel_cb = ttk.Combobox(
            form, values=[payment.CHANNEL_LABELS["vibe"], payment.CHANNEL_LABELS["mock"]],
            state="readonly", width=31, font=FONT)
        if cloud_client.is_active():
            try:
                ready = cloud_client.health().get("model_mode") == "api"
            except Exception:
                ready = False
        else:
            ready, _why = payment.VibePayChannel().ready()
        channel_cb.current(0 if ready else 1)
        channel_cb.grid(row=1, column=1, sticky="ew", pady=5)
        form.columnconfigure(1, weight=1)

        orders_box = tk.Frame(win, bg=BG)
        orders_box.pack(fill="both", expand=True, padx=14, pady=(4, 6))
        notebook = ttk.Notebook(orders_box)
        notebook.pack(fill="both", expand=True)
        orders_tab = tk.Frame(notebook, bg=BG)
        ledger_tab = tk.Frame(notebook, bg=BG)
        notebook.add(orders_tab, text="积分订单")
        notebook.add(ledger_tab, text="积分流水")

        tk.Label(orders_tab, text="选中订单后可重新打开收银台或手动查单",
                 bg=BG, fg=MUTED, font=FONT_SM).pack(anchor="w", pady=(6, 4))
        tree = ttk.Treeview(
            orders_tab, columns=("product", "points", "amount", "channel", "status", "trade"),
            show="headings", height=10)
        for col, title, width in (
                ("product", "商品", 130), ("points", "积分", 70),
                ("amount", "金额", 75), ("channel", "通道", 90),
                ("status", "状态", 115), ("trade", "商户订单号", 220)):
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor="center")
        order_scroll = ttk.Scrollbar(orders_tab, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=order_scroll.set)
        tree.pack(side="left", fill="both", expand=True, pady=(0, 6))
        order_scroll.pack(side="right", fill="y", pady=(0, 6))

        ledger_head = tk.Frame(ledger_tab, bg=BG)
        ledger_head.pack(fill="x", pady=(6, 4))
        tk.Label(ledger_head, text="收支方向", bg=BG, fg=MUTED,
                 font=FONT_SM).pack(side="left")
        ledger_filter = ttk.Combobox(
            ledger_head, values=("全部流水", "只看收入", "只看支出"),
            state="readonly", width=10, font=FONT_SM)
        ledger_filter.current(0)
        ledger_filter.pack(side="left", padx=8)
        self._btn(ledger_head, "刷新流水", lambda: _refresh_ledger(), "ghost",
                  icon="icon_refresh").pack(side="right")
        ledger_tree = ttk.Treeview(
            ledger_tab,
            columns=("time", "kind", "delta", "balance", "reason", "ref"),
            show="headings", height=9)
        for col, title, width in (
                ("time", "时间", 140), ("kind", "类型", 90),
                ("delta", "积分变动", 75), ("balance", "变动后余额", 85),
                ("reason", "事由", 220), ("ref", "关联凭证 / 交易号", 200)):
            ledger_tree.heading(col, text=title)
            ledger_tree.column(col, width=width, anchor="center")
        ledger_scroll = ttk.Scrollbar(ledger_tab, orient="vertical", command=ledger_tree.yview)
        ledger_tree.configure(yscrollcommand=ledger_scroll.set)
        ledger_tree.pack(side="left", fill="both", expand=True, pady=(0, 6))
        ledger_scroll.pack(side="right", fill="y", pady=(0, 6))
        ledger_filter.bind("<<ComboboxSelected>>", lambda _event: _refresh_ledger())

        def _refresh_ledger():
            direction = {"全部流水": "all", "只看收入": "income",
                         "只看支出": "expense"}.get(ledger_filter.get(), "all")
            try:
                rows = (cloud_client.list_ledger(limit=100, direction=direction)
                        if cloud_client.is_active()
                        else db.list_points_ledger(limit=100, direction=direction))
            except Exception as ex:
                pay_state.config(text="流水读取失败：%s" % ex, fg=RED)
                rows = []
            source_labels = {
                "grant": "赠送", "purchase": "充值", "mock_purchase": "模拟充值",
                "consume": "消费", "refund": "退款", "test": "测试",
            }
            for iid in ledger_tree.get_children():
                ledger_tree.delete(iid)
            for row in rows:
                delta = int(row["delta"])
                source = row.get("source", "")
                kind = source_labels.get(source, "收入" if delta > 0 else "支出")
                ledger_tree.insert("", "end", iid="ledger-%s" % row["id"], values=(
                    row.get("created_at", ""), kind,
                    ("%+d" % delta), row["balance_after"], row.get("reason", ""),
                    row.get("ref_trade_no") or "—"))

        def _refresh_orders():
            selected = tree.selection()
            try:
                orders = cloud_client.list_orders() if cloud_client.is_active() else db.list_points_orders(limit=50)
            except Exception as ex:
                pay_state.config(text="订单读取失败：%s" % ex, fg=RED)
                orders = []
            for iid in tree.get_children():
                tree.delete(iid)
            for order in orders:
                tree.insert("", "end", iid=str(order["id"]), values=(
                    order["product_name"], order["points"], "¥%.2f" % order["amount"],
                    payment.CHANNEL_LABELS.get(order["channel"], order["channel"]),
                    order["status"], order["out_trade_no"]))
            for iid in selected:
                if tree.exists(iid):
                    tree.selection_set(iid)
            _refresh_ledger()

        def _drain():
            if state["closed"]:
                return
            try:
                while True:
                    kind, payload = state["queue"].get_nowait()
                    if kind == "result":
                        oid, st, msg, bal = payload
                        if st == "SUCCESS":
                            pay_state.config(text="订单 #%d：%s（余额 %d）" % (oid, msg, bal), fg=GREEN)
                            state["stop"].set()
                        elif st == "FAIL":
                            pay_state.config(text="订单 #%d：%s" % (oid, msg), fg=RED)
                        else:
                            pay_state.config(text="订单 #%d：%s" % (oid, msg), fg=ORANGE)
                        _refresh_bal()
                        _refresh_orders()
                    elif kind == "error":
                        pay_state.config(text="查单暂时失败：%s（将自动重试）" % payload, fg=ORANGE)
            except queue.Empty:
                pass
            if not state["closed"]:
                win.after(100, _drain)

        def _sync_selected():
            sel = tree.selection()
            if not sel:
                self._warn("提示", "请先选中一笔积分订单")
                return
            oid = int(sel[0])
            try:
                if cloud_client.is_active():
                    order = next((o for o in cloud_client.list_orders()
                                  if int(o["id"]) == oid), None)
                else:
                    order = db.get_points_order(oid)
            except Exception as ex:
                pay_state.config(text=str(ex), fg=RED)
                return
            if not order:
                return
            if order["channel"] == "mock" or order["status"] in ("已付款", "模拟已付款"):
                _refresh_orders(); _refresh_bal()
                self.status_var.set("该订单已到账，无需重复入账")
                return
            pay_state.config(text="正在查询订单 #%d…" % oid, fg=ORANGE)

            def worker():
                try:
                    if cloud_client.is_active():
                        data = cloud_client.sync_order(oid)
                        st, msg, bal = data["state"], data["message"], data["balance"]
                    else:
                        st, msg, bal = points.sync_purchase(oid)
                    state["queue"].put(("result", (oid, st, msg, bal)))
                except Exception as ex:
                    state["queue"].put(("error", str(ex)))

            threading.Thread(target=worker, daemon=True).start()

        def _reopen():
            current = state["current"]
            if current and current.get("pay_url"):
                webbrowser.open(current["pay_url"])
                return
            sel = tree.selection()
            if not sel:
                self.status_var.set("请先选中待付款订单")
                return
            oid = int(sel[0])
            try:
                if cloud_client.is_active():
                    purchase = cloud_client.reopen_order(oid)
                    _watch_purchase(purchase)
                    return
                order = db.get_points_order(oid)
                if not order or order["channel"] != "vibe" or order["status"] in ("已付款", "模拟已付款"):
                    self.status_var.set("该订单无需再次打开收银台")
                    return
                chan = payment.VibePayChannel()
                intent = chan.start_payment(
                    order["out_trade_no"], order["amount"],
                    "进步壹号%s %d积分" % (order["product_name"], order["points"]))
                purchase = {"order_id": oid, "out_trade_no": order["out_trade_no"],
                            "channel": "vibe", "status": order["status"],
                            "pay_url": intent["pay_url"],
                            "product": {"name": order["product_name"],
                                        "points": order["points"],
                                        "price": order["amount"]}}
                _watch_purchase(purchase)
            except Exception as ex:
                self._error("打开收银台失败", str(ex))

        def _watch_purchase(purchase):
            state["stop"].set()
            state["stop"] = threading.Event()
            stop = state["stop"]
            state["current"] = purchase
            oid = purchase["order_id"]
            if purchase.get("pay_url"):
                webbrowser.open(purchase["pay_url"])
            cfg = payment.load_pay_config()["vibe"]
            interval = max(2, min(15, float(cfg.get("poll_interval", 3))))
            deadline = time.time() + float(cfg.get("timeout_sec", 900))

            def worker():
                while not stop.is_set() and time.time() < deadline:
                    try:
                        if cloud_client.is_active():
                            data = cloud_client.sync_order(oid)
                            st, msg, bal = data["state"], data["message"], data["balance"]
                        else:
                            st, msg, bal = points.sync_purchase(oid)
                        state["queue"].put(("result", (oid, st, msg, bal)))
                        if st in ("SUCCESS", "FAIL"):
                            return
                    except Exception as ex:
                        state["queue"].put(("error", str(ex)))
                    stop.wait(interval)
                if not stop.is_set():
                    bal = (cloud_client.cached_balance() if cloud_client.is_active()
                           else db.points_balance())
                    state["queue"].put(("result", (oid, "FAIL", "等待超时，可稍后手动查单", bal)))

            threading.Thread(target=worker, daemon=True).start()

        def _buy():
            try:
                key = points.product_key_from_choice(product_cb.get())
                channel = "vibe" if channel_cb.get() == payment.CHANNEL_LABELS["vibe"] else "mock"
                purchase = (cloud_client.purchase(key, channel) if cloud_client.is_active()
                            else points.create_purchase(key, channel))
            except Exception as ex:
                self._error("积分包下单失败", str(ex))
                _refresh_orders()
                return
            _refresh_orders(); _refresh_bal()
            tree.selection_set(str(purchase["order_id"]))
            if channel == "mock":
                pay_state.config(text="模拟支付成功，已入账 %d 积分（未扣真实资金）"
                                      % purchase["product"]["points"], fg=GREEN)
                self.status_var.set("模拟积分包支付完成")
            else:
                pay_state.config(text="订单 #%d 已创建，等待支付；到账后自动入账"
                                      % purchase["order_id"], fg=ORANGE)
                _watch_purchase(purchase)

        brow = tk.Frame(card, bg=SURFACE)
        brow.pack(fill="x", pady=(8, 2))
        self._btn(brow, "下单并打开收银台", _buy, "primary",
                  icon="icon_vibepay").pack(side="left")
        self._btn(brow, "手动查单", _sync_selected, "ghost",
                  icon="icon_refresh").pack(side="left", padx=6)
        self._btn(brow, "再次打开收银台", _reopen, "ghost",
                  icon="icon_qr").pack(side="left")
        tk.Label(card, text="Vibe Pay 由你本人在支付宝收银台付款；模拟通道仅用于测试，"
                            "订单与流水都会明确标注“模拟”。",
                 bg=SURFACE, fg=MUTED, font=FONT_XS).pack(anchor="w", pady=(6, 0))

        def _close():
            state["closed"] = True
            state["stop"].set()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _close)
        self._btn(win, "完成", _close, "ghost").pack(pady=(0, 10))
        _refresh_bal()
        _refresh_orders()
        _drain()

