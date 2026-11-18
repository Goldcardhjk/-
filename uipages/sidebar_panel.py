# -*- coding: utf-8 -*-
"""右侧任务栏 mix-in（v0.63 从 ui.py 机械拆出，方法体原样未动）"""
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


class SidebarMixin:
    def _chat_add(self, tag, text):
        """向聊天区追加一条消息。

        v0.59：长答复不再无条件 see("end")——「优先找：某某」是第一句，
        自动滚到底会把它顶出视野，观众只看到答谢方案。改成停在答复开头。
        """
        self.chat_text.configure(state="normal")
        prefix = {"user": "你：", "ai": "助手：", "sys": "· "}.get(tag, "")
        start = self.chat_text.index("end-1c")
        self.chat_text.insert("end", prefix + text + "\n", tag)
        self.chat_text.configure(state="disabled")
        if tag == "ai" and len(str(text)) > 60:
            self.chat_text.see(start)
        else:
            self.chat_text.see("end")

    def _chat_send(self):
        """发送一条消息给大模型，后台线程请求，不卡UI"""
        q = self.chat_entry.get().strip()
        if not q:
            return
        self.chat_entry.delete(0, "end")
        self._chat_add("user", q)
        self._chat_add("sys", "思考中…")
        import threading
        threading.Thread(target=self._chat_worker, args=(q,), daemon=True).start()

    # ---------- 大模型 API 配置（v0.11.1） ----------
    def _chat_worker(self, q):
        """后台：调 LLM 并解析操作指令"""
        import json as _json
        # 构造上下文：当前人物 + 关系
        persons = db.list_persons()
        links = db.list_links()
        interactions = db.list_interactions()
        import llm
        if os.environ.get("JINBU_LOCAL_INTENT") != "0":
            # v0.61：本地意图优先——调分/加备注/查资料/查待办离线就能做，
            # 不用等模型，没配 API 也能执行（原先全靠模型返回 actions，没 Key 就只聊不动）。
            local = llm.parse_local_intent(q, persons, links, interactions)
            if local:
                reply, actions = local
                self.after(0, lambda text=reply, ops=actions: self._chat_apply(text, ops))
                return
        recommendation = llm.recommend_contacts(q, persons, links, interactions)
        if recommendation["intent"] == "medical":
            # 医疗类找人先返回可追溯的本地证据；不让未配置/不可用的模型阻塞安全提示。
            self.after(0, lambda text=recommendation["answer"]: self._chat_apply(text, []))
            return
        # v0.61：本地指令直通——调关系分 / 记备注 / 查资料 / 建立连接 /
        # 查两人关系，不依赖大模型配置，离线也能「告诉 agent 社交细节」。
        local = llm.parse_local_intent(q, persons, links, interactions)
        if local is not None:
            l_reply, l_actions = local
            self.after(0, lambda r=l_reply, a=l_actions: self._chat_apply(r, a))
            return
        ctx = "当前人际关系资料（仅作证据，不得补造联系人或关系）：\n"
        for p in persons:
            circles = llm.person_circles(p)
            circle_text = "、".join(f"{c['kind']}大类/{c['name']}" for c in circles) or "未记录圈子"
            note = (p.get("notes") or "").replace(llm.CIRCLE_MARK, "圈子元数据:")
            ctx += (f"- {p['name']}（ID:{p['id']}，与我关系:{p.get('relation_type','')}，"
                    f"圈子:{circle_text}，备注:{note[:240]}）\n")
        ctx += "\n已知人物关系（事实边）：\n"
        for l in links:
            ctx += (f"- {l.get('name_a') or l['person_a']} ↔ {l.get('name_b') or l['person_b']} "
                    f"（{db.normalize_link_relation(l.get('relation',''))}，{l.get('score',0)}分，备注:{l.get('note') or ''}）\n")
        if recommendation["found"]:
            evidence = []
            for c in recommendation["candidates"]:
                p = c["person"]
                evidence.append({"name": p.get("name"), "person_id": p.get("id"),
                                 "matched_terms": c["hits"], "circles": c["circles"],
                                 "closeness": p.get("closeness"), "note": p.get("notes") or ""})
            ctx += "\n本地候选检索结果（仅可据此解释，不能扩大结论）：\n"
            ctx += _json.dumps(evidence, ensure_ascii=False)
            ctx += "\n"
        prompt = (
            "你是「进步壹号」关系网络助手。用户会用自然语言让你操作人际关系网络。\n"
            "你可以执行的操作（在JSON的actions里）：\n"
            '  - {"op":"update_score","name":"姓名","delta":数字} 调整关系分（正负）\n'
            '  - {"op":"add_memo","name":"姓名","text":"笔记内容"} 给某人加备注记忆\n'
            '  - {"op":"add_link","a":"姓名A","b":"姓名B","relation":"认识","score":50} 建立连接\n'
            "请只输出JSON，格式：{\"reply\":\"给用户的回复\",\"actions\":[...]}。\n"
            "泛化合作关系使用合作伙伴；保留具体项目、交易名称，不凭相同联系人分类补边。\n"
            "回答找人问题时，必须优先使用本地候选检索结果；明确区分直接能力证据与关系路径，不能把同属家人/同学大类当成认识或具备专业能力的证据。若候选检索为空，说明当前资料不足并建议补充。\n"
            "如果只是聊天不用操作，actions为空数组。\n\n"
            + ctx + "\n用户说：" + q
        )
        try:
            import llm
            cfg = llm.load_config()
            base = cfg.get("base_url", "").rstrip("/")
            key = cfg.get("api_key", "")
            model = self.chat_model.get() or cfg.get("model", "")
            if not (base and key and model):
                raise RuntimeError("未配置API")
            body = _json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            }).encode("utf-8")
            req = __import__("urllib.request", fromlist=["request"]).Request(
                f"{base}/chat/completions", data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"})
            import urllib.request as _ur
            with _ur.urlopen(req, timeout=30) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            # 提取JSON
            s, e = content.find("{"), content.rfind("}")
            result = _json.loads(content[s:e+1]) if s >= 0 else {"reply": content, "actions": []}
            reply = result.get("reply", content)
            actions = result.get("actions", [])
            # 在UI线程执行操作
            self.after(0, lambda: self._chat_apply(reply, actions))
        except Exception as ex:
            # 注意：except as 的 ex 在块结束时会自动 del，lambda 延迟执行会
            # NameError——必须先提取到局部变量（经典闭包陷阱）
            msg = (f"（未配置API或请求失败：{ex}）\n"
                   "未配置大模型时我仍能本地处理：查资料/生日/地址、调关系分"
                   "（如「奶奶 关系分+5」）、记备注（「给奶奶加备注：…」）、"
                   "建立连接（「阿豪和小琳是大学同学」）、查两人关系"
                   "（「小琳和阿豪是什么关系」）、找谁帮忙类问题。\n"
                   "点「API」按钮配置大模型（腾讯混元 Lite 永久免费），"
                   "即可自由对话。")
            self.after(0, lambda: self._chat_add("ai", msg))

    def _chat_apply(self, reply, actions):
        """在UI线程执行LLM返回的操作"""
        # 删掉"思考中…"
        self.chat_text.configure(state="normal")
        # 删除最后一行（思考中）
        idx = self.chat_text.get("end-2l", "end-1c").find("思考中")
        if idx >= 0:
            self.chat_text.delete("end-2l", "end-1c")
        self.chat_text.configure(state="disabled")
        self._chat_add("ai", reply)
        # 执行操作
        done = []
        persons = {p["name"]: p for p in db.list_persons()}
        for act in actions:
            try:
                op = act.get("op")
                if op == "update_score":
                    p = persons.get(act["name"])
                    if p:
                        new_close = max(0, min(1,
                            float(p.get("closeness") or 0.5) + float(act.get("delta", 0)) / 100.0))
                        db.save_person({**p, "closeness": new_close}, pid=p["id"])
                        done.append(f"已调整 {act['name']} 的关系分")
                elif op == "add_memo":
                    p = persons.get(act["name"])
                    if p:
                        old = p.get("memo_md") or ""
                        db.set_memo(p["id"], (old + "\n- " + act["text"]).strip())
                        done.append(f"已给 {act['name']} 加备注")
                elif op == "add_link":
                    a = persons.get(act["a"]); b = persons.get(act["b"])
                    if a and b:
                        db.add_link(a["id"], b["id"], db.normalize_link_relation(act.get("relation", "认识")),
                                   score=int(act.get("score", 50)))
                        done.append(f"已建立 {act['a']} ↔ {act['b']} 连接")
            except Exception as ex:
                done.append(f"操作失败：{ex}")
        if done:
            self._chat_add("sys", "已执行：" + "；".join(done))
            self.refresh_network()

    # ---------- 会议纪要 / 社交细节导入 ----------
    def quick_link(self):
        """右任务栏「快速补关系」：双下拉 + 关系类型 + 关系分 → 建立连接"""
        na, nb = self.ql_a.get().strip(), self.ql_b.get().strip()
        if not na or not nb:
            self._warn("快速补关系", "请先选择两个人物")
            return
        if na == nb:
            self._warn("快速补关系", "不能连接同一个人")
            return
        a, b = self.person_map.get(na), self.person_map.get(nb)
        if not a or not b:
            self._warn("快速补关系", "人物不存在，请先从下拉列表选择")
            return
        rel = self.ql_rel.get() or "认识"
        try:
            score = int(float(self.ql_score.get()))
        except Exception:
            score = 50
        db.add_link(a, b, rel, "", score)
        self.status_var.set(f"已连接「{na}」—[{rel} · {score}分]→「{nb}」")
        self.refresh_network(); self.refresh_combos()

    def _refresh_rank(self, items):
        for w in self.rank_host.winfo_children():
            w.destroy()
        self.rank_ids = []
        self._rank_imgs = []             # 徽章 PhotoImage（保持引用防 GC）
        shown = items[:8]                      # 右栏可滚动，展示前 8 位
        for i, it in enumerate(shown):
            pid = it["pid"]
            self.rank_ids.append(pid)
            color = TIER_COLOR.get(it["tier"], MUTED)
            # v0.14：程序化排名徽章 rank_1..8（金/银/铜/冷钢），
            # 替代原「🥇🥈🥉 emoji + 第四名起裸数字」的断层样式
            badge = images_util.ui_asset_scaled("rank_%d" % (i + 1), 18, 18)
            if badge is not None:
                self._rank_imgs.append(badge)
            state = {"hover": False, "cv": None, "w": 0}

            def _draw(cv, w, pid=pid, it=it, color=color, badge=badge,
                      rank=i + 1, state=state):
                state["cv"], state["w"] = cv, w
                cv.delete("rowcontent")
                h = 36
                if state["hover"]:
                    _rounded(cv, 1, 1, w - 2, h - 2, 9,
                             fill=_mix(CYAN, SURFACE2, 0.10),
                             outline=_mix(CYAN, SURFACE2, 0.55), width=1,
                             tags=("rowcontent",))
                # 排名徽章（缺失退化为编号文字）
                if badge is not None:
                    cv.create_image(16, 18, image=badge, anchor="c",
                                    tags=("rowcontent",))
                else:
                    cv.create_text(16, 18, text=str(rank), fill=MUTED,
                                   font=FONT_SM, anchor="c",
                                   tags=("rowcontent",))
                # 姓名 + 分值
                cv.create_text(30, 17, text=it["name"], fill=TEXT,
                               font=FONT_SM, anchor="w", tags=("rowcontent",))
                cv.create_text(w - 56, 17, text=f'{it["score"]} 分',
                               fill=color, font=FONT_SM, anchor="e",
                               tags=("rowcontent",))
                # 层级 chip：预混底色圆角胶囊 + 提亮层级色文字
                chw = 40
                cx0 = w - 10 - chw
                _rounded(cv, cx0, 9, cx0 + chw, 25, 8,
                         fill=_mix(color, SURFACE2, 0.82),
                         outline=_mix(color, SURFACE2, 0.45), width=1,
                         tags=("rowcontent",))
                cv.create_text(cx0 + chw / 2, 17, text=it["tier"],
                               fill=_mix(color, "#FFFFFF", 0.28),
                               font=FONT_XS, anchor="c",
                               tags=("rowcontent",))
                # 分数条：轨道 + 已得分量（层级色），一眼看出分值高低
                bx0, bx1, by = 30, w - 10, 30
                _rounded(cv, bx0, by, bx1, by + 3, 1.5, fill=SURFACE3,
                         outline="", tags=("rowcontent",))
                fw = max(4.0, (bx1 - bx0) *
                         min(100, max(0, it["score"])) / 100.0)
                _rounded(cv, bx0, by, bx0 + fw, by + 3, 1.5, fill=color,
                         outline="", tags=("rowcontent",))

            row = row_card(self.rank_host, height=36, on_draw=_draw,
                           cursor="hand2")
            row.pack(fill="x", pady=2)
            row.bind("<Button-1>", lambda e, p=pid: self.open_profile(p))
            row.bind("<Double-Button-1>", lambda e, p=pid: self.open_profile(p))
            row.bind("<Enter>",
                     lambda e, s=state, dr=_draw: self._row_hover(s, True, dr))
            row.bind("<Leave>",
                     lambda e, s=state, dr=_draw: self._row_hover(s, False, dr))
        if len(items) > len(shown):
            tk.Label(self.rank_host,
                     text=f"还有 {len(items) - len(shown)} 位，去「人脉卡片」查看",
                     bg=SURFACE, fg=MUTED,
                     font=FONT_XS, anchor="w").pack(fill="x", pady=(4, 0))

    @staticmethod
    def _row_hover(state, on, draw):
        """行悬停切换：重画行内容（高亮叠层随 rowcontent 标签一起清重绘）"""
        if state["hover"] == on:
            return
        state["hover"] = on
        cv, w = state["cv"], state["w"]
        if cv is not None and w:
            try:
                draw(cv, w)
            except Exception:
                pass

    # ---------- 右侧：近期提醒 ----------
    def _refresh_reminders(self, items):
        for w in self.remind_host.winfo_children():
            w.destroy()
        from datetime import datetime as _dt, date as _d
        today = _d.today()
        rows = []
        for it in items:
            p = it["_p"]
            d = days_to_next_event(p.get("birthday"), p.get("important_dates"))
            if d is None:
                continue
            label = "倒计时"
            best = None
            if p.get("birthday"):
                try:
                    bd = _dt.strptime(p["birthday"], "%Y-%m-%d").date().replace(year=today.year)
                    if bd < today:
                        bd = bd.replace(year=bd.year + 1)
                    best = ((bd - today).days, "生日")
                except Exception:
                    pass
            try:
                for im in json.loads(p.get("important_dates") or "[]"):
                    ds = (im.get("date") or "").strip()
                    if not ds:
                        continue
                    try:
                        imd = _dt.strptime(ds, "%Y-%m-%d").date()
                    except Exception:
                        continue
                    if imd < today:
                        continue
                    dd = (imd - today).days
                    if best is None or dd < best[0]:
                        best = (dd, im.get("label") or "纪念日")
            except Exception:
                pass
            if best:
                d, label = best
            rows.append((d, it["name"], label, it["pid"]))
        rows.sort(key=lambda x: x[0])
        if not rows:
            tk.Label(self.remind_host, text="（暂无重要日期）", bg=SURFACE,
                     fg=MUTED, font=FONT_SM).pack(anchor="w", padx=4, pady=4)
            return
        for d, name, label, pid in rows[:8]:
            color = RED if d <= 7 else (YELLOW if d <= 30 else MUTED)
            dot = RED if d <= 7 else (YELLOW if d <= 30 else "#46536B")
            # 标签紧跟姓名（按实测字宽定位，长姓名不再叠字）
            lx = 29 + images_util.text_width(name, 9) + 8
            # 倒计时胶囊：预混底色 + 提亮文字，右侧对齐
            ptxt = f"{d} 天"
            pw = max(34.0, images_util.text_width(ptxt, 8) + 14)

            # v0.61：30 天内的提醒带「祝福 / 送礼」行动胶囊（点中即跳转对应
            # 页面并预选人物）——提醒不只是看，还要能直接采取维护行动。
            # 位置相对倒计时胶囊左排；空间不够（长姓名）时自动省略。
            def _zones(w, d=d, lx=lx, pw=pw, label=label):
                px0 = w - 10 - pw
                if d > 30:
                    return px0, None
                label_w = images_util.text_width(label, 8)
                if px0 - 84 <= lx + label_w + 6:
                    return px0, None
                return px0, ((px0 - 84, px0 - 50), (px0 - 42, px0 - 8))

            def _draw(cv, w, d=d, name=name, label=label, color=color,
                      dot=dot, lx=lx, pw=pw, ptxt=ptxt, zn=_zones):
                # v0.21：圆点加一圈同色微光环（暗底上也「亮」得出来）
                cv.create_oval(9, 10, 21, 22,
                               outline=_mix(dot, SURFACE2, 0.55), width=1,
                               tags=("rowcontent",))
                cv.create_oval(11, 12, 19, 20, fill=dot, outline="",
                               tags=("rowcontent",))
                cv.create_text(26, 16, text=name, fill=TEXT,
                               font=FONT_SM, anchor="w", tags=("rowcontent",))
                cv.create_text(lx, 16, text=label,
                               fill=_mix(MUTED, "#FFFFFF", 0.22),
                               font=FONT_XS, anchor="w", tags=("rowcontent",))
                px0, zs = zn(w)
                if zs:
                    for (zx0, zx1), ztxt, zcol in (
                            (zs[0], "祝福", PURPLE), (zs[1], "送礼", ORANGE)):
                        _rounded(cv, zx0, 8, zx1, 24, 8,
                                 fill=_mix(zcol, SURFACE2, 0.84),
                                 outline=_mix(zcol, SURFACE2, 0.42), width=1,
                                 tags=("rowcontent",))
                        cv.create_text((zx0 + zx1) / 2, 16, text=ztxt,
                                       fill=_mix(zcol, "#FFFFFF", 0.30),
                                       font=FONT_XS, anchor="c",
                                       tags=("rowcontent",))
                _rounded(cv, px0, 8, px0 + pw, 24, 8,
                         fill=_mix(color, SURFACE2, 0.84),
                         outline=_mix(color, SURFACE2, 0.42), width=1,
                         tags=("rowcontent",))
                cv.create_text(px0 + pw / 2, 16, text=ptxt,
                               fill=_mix(color, "#FFFFFF", 0.30),
                               font=FONT_XS, anchor="c",
                               tags=("rowcontent",))

            def _on_row_click(e, p=pid, n=name, lab=label, zn=_zones):
                _px0, zs = zn(e.widget.winfo_width())
                if zs:
                    if zs[0][0] <= e.x <= zs[0][1]:
                        return self._remind_act_greet(p, n, lab)
                    if zs[1][0] <= e.x <= zs[1][1]:
                        return self._remind_act_gift(p, n)
                self.open_profile(p)

            row = row_card(self.remind_host, height=32, on_draw=_draw,
                           cursor="hand2")
            row.pack(fill="x", pady=2)
            row.bind("<Button-1>", _on_row_click)

    # ---------- 提醒行动（v0.61）：从提醒直接采取维护行动 ----------
    def _remind_act_greet(self, pid, name, label=None):
        """跳到祝福语页并预选人物；生日类提醒自动选「生日」模板并直接撰写。"""
        self.nb.select(self.tab_greet)
        self._sync_tabs()
        if name in list(self.gr_person.cget("values")):
            self.gr_person.set(name)
        if label == "生日" and "生日" in HOLIDAY_TEMPLATES:
            self.gr_holiday.set("生日")
            self.gen_greeting()
            self.status_var.set(f"已为「{name}」写好生日祝福，可复制后发送")
        else:
            self.status_var.set(f"已为「{name}」预选祝福对象，选节日后点「生成专属祝福」")

    def _remind_act_gift(self, pid, name):
        """跳到送礼页并自动出推荐清单。"""
        self.nb.select(self.tab_gift)
        self._sync_tabs()
        if name in list(self.g_person.cget("values")):
            self.g_person.set(name)
            self.on_gift_person()
        self.status_var.set(f"已为「{name}」生成送礼推荐，选礼物后可一键付款")

    def _due_reminders(self, within=7):
        """列出 within 天内的生日/纪念日：[(天数, 姓名, 标签, pid)]，按天数升序。

        v0.63：跟随当前使用场景——家庭场景只提醒家里的人，办公场景只提醒
        工作伙伴；「全部」才看全量。"""
        scene = getattr(self, "_scene", "全部")
        rows = []
        for p in db.list_persons():
            if scene != "全部" and scene not in (p.get("scenes") or []):
                continue
            ev = llm._nearest_event(p)
            if ev and ev[0] <= within:
                rows.append((ev[0], p["name"], ev[1], p["id"]))
        rows.sort(key=lambda x: x[0])
        return rows

    def popup_due_reminders(self, force=False):
        """启动提醒弹窗（v0.61）：7 天内的生日/纪念日一次列清，
        每行带「写祝福 / 去送礼」行动按钮——打开软件就知道这周该维护谁。
        非模态，不抢焦点；仅 main.py 真实启动时自动弹出（测试默认关闭）。
        """
        if not force and not STARTUP_POPUP:
            return None
        rows = self._due_reminders(7)
        if not rows:
            return None
        win = tk.Toplevel(self)
        win.title("近期提醒")
        win.configure(bg=BG)
        win.resizable(False, False)
        self.update_idletasks()
        x = self.winfo_x() + max(0, self.winfo_width() - 420)
        y = self.winfo_y() + 90
        win.geometry(f"380x{96 + 44 * len(rows) + 20}+{x}+{y}")
        self._label(win, "近期提醒 · 该维护谁了", fg=CYAN, bg=BG,
                    font=FONT_MID, icon="icon_remind").pack(
            anchor="w", padx=16, pady=(12, 2))
        tk.Label(win, text="7 天内的生日 / 纪念日，点行动按钮直接处理：",
                 bg=BG, fg=MUTED, font=FONT_SM).pack(anchor="w", padx=16)
        for d, name, label, pid in rows:
            r = tk.Frame(win, bg=SURFACE, highlightthickness=1,
                         highlightbackground=BORDER)
            r.pack(fill="x", padx=16, pady=3)
            col = RED if d <= 7 else YELLOW
            txt = "今天" if d == 0 else (f"明天" if d == 1 else f"{d} 天后")
            tk.Label(r, text=f"{name} · {label}（{txt}）", bg=SURFACE,
                     fg=col, font=FONT_SM).pack(side="left", padx=(10, 4),
                                                pady=6)
            self._btn(r, "去送礼",
                      lambda p=pid, n=name, w=win: (
                          w.destroy(), self._remind_act_gift(p, n)),
                      "ghost").pack(side="right", padx=(2, 8), pady=3)
            self._btn(r, "写祝福",
                      lambda p=pid, n=name, lab=label, w=win: (
                          w.destroy(), self._remind_act_greet(p, n, lab)),
                      "ghost").pack(side="right", padx=2, pady=3)
        self._btn(win, "知道了", win.destroy, "primary").pack(pady=(8, 10))
        return win

    # ---------- 右侧：网络概览 ----------
    def _refresh_overview(self, items):
        for w in self.stat_host.winfo_children():
            w.destroy()
        total = len(items)
        core = sum(1 for i in items if i["tier"] == "核心")
        avg = round(sum(i["score"] for i in items) / total, 1) if total else 0
        soon = 0
        for it in items:
            d = days_to_next_event(it["_p"].get("birthday"), it["_p"].get("important_dates"))
            if d is not None and d <= 30:
                soon += 1
        grid = tk.Frame(self.stat_host, bg=SURFACE)
        grid.pack(fill="x")
        # v0.28：去掉图标（icon 参数保留但不再渲染）——四个小图标在 130px
        # 宽的格子里色彩杂乱、还带一圈灰边，用户要求「不要加那些灰色的条条，
        # 可以通过调整位置来实现功能的」。层级改由字号 + 语义色表达。
        tiles = [("人物总数", total, CYAN),
                 ("核心人物", core, RED),
                 ("平均分值", avg, ORANGE),
                 ("30天内提醒", soon, YELLOW)]
        for i, (lab, val, col) in enumerate(tiles):
            r, cidx = divmod(i, 2)
            stat_tile(grid, val, lab, col, width=132, height=64).grid(
                row=r, column=cidx, sticky="nsew", padx=(0, 12), pady=(2, 8))
        for cc in range(2):
            grid.columnconfigure(cc, weight=1, uniform="tiles")

    # ---------- 资料卡弹窗 ----------
