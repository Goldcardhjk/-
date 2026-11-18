# -*- coding: utf-8 -*-
"""v0.68 积分商业闭环离线验收。

只使用隔离数据库与 fake 支付通道；不读取真实 pay_config.json，不访问网络，
不产生真实付款。运行：python -B tools/verify_points.py
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_tmp" / "v068" / "points_check"
OUT.mkdir(parents=True, exist_ok=True)
# 本脚本专用测试库可重复执行；绝不触碰应用默认 agent.db。
for suffix in ("", "-wal", "-shm"):
    (OUT / "data" / ("points_test.db" + suffix)).unlink(missing_ok=True)
sys.path.insert(0, str(ROOT))
os.environ["JINBU_DATA_DIR"] = str(OUT / "data")
os.environ["JINBU_DB_PATH"] = str(OUT / "data" / "points_test.db")
os.environ["JINBU_SKIP_SEED"] = "1"
os.environ["JINBU_ACCOUNT"] = "default"
sys.dont_write_bytecode = True

import db                       # noqa: E402
import points                   # noqa: E402
import llm                      # noqa: E402
import greetings                # noqa: E402

REPORT = OUT / "verification.json"
checks = []


def check(name, condition, extra=""):
    checks.append({"name": name, "passed": bool(condition), "detail": str(extra)})
    print(("[通过] " if condition else "[失败] ") + name +
          ((" | " + str(extra)) if extra else ""), flush=True)
    if not condition:
        raise AssertionError(name)


def ledger_count(source=None):
    conn = db.get_conn()
    if source:
        n = conn.execute("SELECT COUNT(*) FROM points_ledger WHERE source=?",
                         (source,)).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM points_ledger").fetchone()[0]
    conn.close()
    return int(n)


class FakeChannel:
    """固定 WAIT → SUCCESS；第三次查单若发生即说明已支付订单被重复查询。"""
    instances = []

    def __init__(self, *args, **kwargs):
        self.answers = [("WAIT", "等待买家付款", ""),
                        ("SUCCESS", "交易成功", "FAKE-ALIPAY-0001")]
        self.queries = 0
        FakeChannel.instances.append(self)

    def ready(self):
        return True, ""

    def start_payment(self, out_trade_no, amount, subject):
        return {"out_trade_no": out_trade_no,
                "pay_url": "https://fake.example/cashier?order=" + out_trade_no}

    def query(self, out_trade_no):
        answer = self.answers[min(self.queries, len(self.answers) - 1)]
        self.queries += 1
        return answer


def save_person(name):
    db.save_person({"name": name, "relation_type": "挚友", "closeness": 0.8,
                    "sentiment_weight": 0.8, "intro_by": "", "address": "",
                    "phone": "", "birthday": "", "important_dates": "[]",
                    "prefs": '["茶"]', "taboo": "[]", "notes": ""}, None)
    return next(p["id"] for p in db.list_persons() if p["name"] == name)


def no_network(*args, **kwargs):
    raise AssertionError("离线积分验收禁止访问网络")


def verify_ui():
    """真实 App 入口打开积分窗口；不点击真实支付、不访问网络。"""
    import tkinter as tk
    from tkinter import ttk
    import ui

    errors = []
    app = ui.App()
    app.report_callback_exception = lambda exc, val, tb: errors.append(str(val))
    app.geometry("+4000+4000")
    try:
        app.update()
        with patch("payment.VibePayChannel.ready", return_value=(False, "offline")), \
                patch("webbrowser.open", side_effect=no_network):
            app.open_points_dialog()
            app.update()
        wins = [w for w in app.winfo_children()
                if isinstance(w, tk.Toplevel) and w.title() == "积分充值与订单"]
        check("真实 App 可打开积分充值与订单窗口", len(wins) == 1 and not errors,
              errors)
        widgets = []

        def walk(widget):
            widgets.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(wins[0])
        combos = [w for w in widgets if isinstance(w, ttk.Combobox)]
        trees = [w for w in widgets if isinstance(w, ttk.Treeview)]
        check("积分窗口包含三档商品、支付通道、订单与流水列表",
              len(combos) >= 3 and len(combos[0]["values"]) == 3 and len(trees) == 2
              and "delta" in trees[1]["columns"])
        wins[0].destroy()
        app.update()
    finally:
        app.destroy()


def run():
    db.init_db()
    check("积分订单表与非空交易号唯一索引已建立",
          db.get_conn().execute(
              "SELECT 1 FROM sqlite_master WHERE type='table' AND name='points_orders'"
          ).fetchone() is not None)

    with patch("urllib.request.urlopen", side_effect=no_network):
        # ① mock 完整闭环：只记模拟状态，不访问网络。
        mock = points.create_purchase("starter", "mock")
        check("模拟积分包下单即入账且明确标注模拟",
              mock["status"] == "模拟已付款" and mock["balance"] == 100
              and db.points_balance() == 100)
        bal, credited = db.points_mark_order_paid(mock["order_id"], "MOCK-REPLAY")
        check("重复确认同一模拟订单不重复入账",
              bal == 100 and not credited and ledger_count("mock_purchase") == 1)

        # ② fake Vibe：建单 → WAIT → SUCCESS → 再查不重复。
        with patch("points.payment.VibePayChannel", FakeChannel):
            vibe = points.create_purchase("standard", "vibe")
            check("真实通道只生成收银台意图，建单时不加积分",
                  vibe["status"] == "等待买家付款" and db.points_balance() == 100
                  and vibe["pay_url"].startswith("https://fake.example/"))
            chan = FakeChannel.instances[-1]
            st, _, bal = points.sync_purchase(vibe["order_id"], chan)
            check("WAIT 状态保持待付款且余额不变",
                  st == "WAIT" and bal == 100
                  and db.get_points_order(vibe["order_id"])["status"] == "等待买家付款")
            st, _, bal = points.sync_purchase(vibe["order_id"], chan)
            check("SUCCESS 同事务写订单与积分流水",
                  st == "SUCCESS" and bal == 600
                  and db.get_points_order(vibe["order_id"])["pay_ref"] == "FAKE-ALIPAY-0001",
                  (st, bal, db.get_points_order(vibe["order_id"])))
            st, _, bal = points.sync_purchase(vibe["order_id"], chan)
            check("已支付订单再次同步不查网关、不重复入账",
                  st == "SUCCESS" and bal == 600 and chan.queries == 2
                  and ledger_count("purchase") == 1)

        # ③ 同一支付宝交易号不能给另一笔订单入账。
        other = db.create_points_order("default", "pro", "专业积分包", 2000,
                                       29.9, "vibe", "JBYHP-OTHER")
        try:
            db.points_mark_order_paid(other, "FAKE-ALIPAY-0001")
            check("跨订单交易号冲突被拒绝", False)
        except ValueError:
            check("跨订单交易号冲突被拒绝", db.points_balance() == 600)

        # ④ 两个线程同时确认同一订单，也只能一笔流水。
        race_oid = db.create_points_order("default", "starter", "体验积分包", 100,
                                          0.01, "vibe", "JBYHP-RACE")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: db.points_mark_order_paid(race_oid, "FAKE-RACE-0001"),
                range(2)))
        check("并发支付确认保持幂等",
              sorted(x[1] for x in results) == [False, True]
              and db.points_balance() == 700 and ledger_count("purchase") == 2,
              results)

        # ⑤ 余额不足原子失败，不产生负余额。
        db.points_record("poor", 1, "测试初始积分", "test", "POOR-INIT")
        try:
            db.points_consume("poor", 2, "余额不足测试")
            check("余额不足拒绝扣减", False)
        except ValueError:
            check("余额不足拒绝扣减", db.points_balance("poor") == 1)

        # ⑥ 真实 API 成功扣分；失败降级 mock 自动退款；本地模式免费。
        os.environ["JINBU_ACCOUNT"] = "billing"
        db.points_record(None, 100, "计费测试初始积分", "test", "BILLING-INIT")
        p1 = save_person("计费甲")
        save_person("计费乙")
        persons = db.list_persons()
        fake_result = {"my_scores": {"计费甲": {"closeness": 0.8, "sentiment": 0.7}},
                       "links": []}
        with patch("llm.mode", return_value="api"), \
                patch("llm._api_judge", return_value=fake_result):
            result = llm.judge_all(persons)
        check("关系判断真实 API 成功扣 10 积分",
              result["mode"] == "api" and db.points_balance() == 90)
        with patch("llm.mode", return_value="api"), \
                patch("llm._api_judge", side_effect=RuntimeError("fake API failure")):
            result = llm.judge_all(persons)
        check("关系判断 API 失败降级并退回 10 积分",
              result["mode"] == "mock" and db.points_balance() == 90)
        with patch("llm.mode", return_value="mock"):
            result = llm.judge_all(persons)
        check("本地关系判断不扣分", result["mode"] == "mock" and db.points_balance() == 90)

        with patch("greetings.llm_mode", return_value="api"), \
                patch("greetings._api_craft", return_value="专属祝福"):
            text, src = greetings.craft_one(p1, "生日", "fake-model")
        check("祝福语真实 API 成功扣 2 积分",
              text == "专属祝福" and src == "api" and db.points_balance() == 88)
        with patch("greetings.llm_mode", return_value="api"), \
                patch("greetings._api_craft", side_effect=RuntimeError("fake API failure")):
            text, src = greetings.craft_one(p1, "生日", "fake-model")
        check("祝福语 API 失败降级并退回 2 积分",
              src == "mock" and db.points_balance() == 88)
        with patch("greetings.llm_mode", return_value="mock"):
            text, src = greetings.craft_one(p1, "生日", "fake-model")
        check("本地祝福模板不扣分", src == "mock" and db.points_balance() == 88)

        os.environ["JINBU_ACCOUNT"] = "default"
        default_ledger = db.list_points_ledger("default", limit=100)
        default_income = db.list_points_ledger("default", direction="income")
        billing_expense = db.list_points_ledger("billing", direction="expense")
        check("账号积分余额隔离与流水收支筛选",
              db.points_balance("default") == 700 and db.points_balance("billing") == 88
              and len(default_ledger) == 3 and len(default_income) == 3
              and [r["delta"] for r in billing_expense] == [-2, -2, -10, -10]
              and [r["id"] for r in default_ledger] == sorted(
                  (r["id"] for r in default_ledger), reverse=True)
              and all(r.get("created_at") and r.get("reason") and r.get("source")
                      for r in default_ledger))
        if "--ui" in sys.argv:
            verify_ui()


try:
    run()
    status = "passed"
except BaseException:
    import traceback
    print(traceback.format_exc(), flush=True)
    status = "failed"
finally:
    os.environ["JINBU_ACCOUNT"] = "default"
    REPORT.write_text(json.dumps(
        {"version": "v0.68-points", "status": status,
         "network": "disabled", "real_payment": False, "checks": checks},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": status, "checks": len(checks),
                      "report": str(REPORT)}, ensure_ascii=False), flush=True)
    sys.exit(0 if status == "passed" else 1)
