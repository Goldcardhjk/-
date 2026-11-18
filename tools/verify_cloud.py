# -*- coding: utf-8 -*-
"""云端账号、积分、支付与模型计费闭环离线验收。

仅启动 127.0.0.1 回环 HTTP 服务；模型为明确标识的 mock，支付为 fake 通道。
不读取真实模型密钥，不产生真实 API 或支付费用。
运行：.venv/Scripts/python.exe -B tools/verify_cloud.py --ui
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_tmp" / "v069" / "cloud_check"
OUT.mkdir(parents=True, exist_ok=True)
for name in ("cloud_test.db", "cloud_test.db-wal", "cloud_test.db-shm",
             "desktop_test.db", "desktop_test.db-wal", "desktop_test.db-shm"):
    (OUT / name).unlink(missing_ok=True)
os.environ["JINBU_SERVER_DB"] = str(OUT / "cloud_test.db")
os.environ["JINBU_CLOUD_CONFIG"] = str(OUT / "cloud_client.json")
os.environ["JINBU_CLOUD_SESSION"] = str(OUT / "cloud_session.json")
os.environ["JINBU_DATA_DIR"] = str(OUT / "desktop_data")
os.environ["JINBU_DB_PATH"] = str(OUT / "desktop_test.db")
os.environ["JINBU_SKIP_SEED"] = "1"
os.environ["JINBU_SERVER_ALLOW_MOCK"] = "1"
os.environ["JINBU_ACCOUNT"] = "default"
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import cloud_server              # noqa: E402
import cloud_client              # noqa: E402
import db                        # noqa: E402
import llm                       # noqa: E402
import greetings                 # noqa: E402

REPORT = OUT / "verification.json"
checks = []


def check(name, condition, detail=""):
    checks.append({"name": name, "passed": bool(condition), "detail": str(detail)})
    print(("[通过] " if condition else "[失败] ") + name +
          ((" | " + str(detail)) if detail else ""), flush=True)
    if not condition:
        raise AssertionError(name)


def save_person(name):
    db.save_person({"name": name, "relation_type": "挚友", "closeness": 0.8,
                    "sentiment_weight": 0.8, "intro_by": "", "address": "",
                    "phone": "", "birthday": "", "important_dates": "[]",
                    "prefs": '["茶"]', "taboo": "[]", "notes": ""}, None)
    return next(p["id"] for p in db.list_persons() if p["name"] == name)


class FakeVibe:
    answers = []
    queries = 0

    def ready(self):
        return True, ""

    def start_payment(self, out_trade_no, amount, subject):
        return {"out_trade_no": out_trade_no,
                "pay_url": "https://fake.example/cashier?order=" + out_trade_no}

    def query(self, out_trade_no):
        answer = FakeVibe.answers[min(FakeVibe.queries, len(FakeVibe.answers) - 1)]
        FakeVibe.queries += 1
        return answer


class FakeModelHandler(BaseHTTPRequestHandler):
    fail = False
    authorizations = []

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size).decode("utf-8"))
        FakeModelHandler.authorizations.append(self.headers.get("Authorization", ""))
        if FakeModelHandler.fail:
            raw = json.dumps({"error": "fake model outage"}).encode("utf-8")
            self.send_response(500)
        else:
            prompt = body.get("messages", [{}])[0].get("content", "")
            if "祝福" in prompt or "节日" in prompt:
                content = "来自云端 API 的专属祝福"
            else:
                content = json.dumps({"my_scores": {
                    "云端甲": {"closeness": 0.8, "sentiment": 0.7}}, "links": []},
                    ensure_ascii=False)
            raw = json.dumps({"choices": [{"message": {"content": content}}]},
                             ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        return


def verify_ui():
    import tkinter as tk
    from tkinter import ttk
    import ui
    errors = []
    app = ui.App()
    app.report_callback_exception = lambda exc, val, tb: errors.append(str(val))
    app.geometry("+4000+4000")
    try:
        app.update()
        app.open_cloud_account_dialog()
        app.update()
        cloud_win = [w for w in app.winfo_children()
                     if isinstance(w, tk.Toplevel) and w.title() == "云端账号与 AI"]
        check("真实 App 可打开云端账号窗口", len(cloud_win) == 1 and not errors, errors)
        cloud_win[0].destroy()
        app.update()
        with patch("webbrowser.open", side_effect=AssertionError("UI 验收禁止打开收银台")):
            app.open_points_dialog()
            app.update()
        points_win = [w for w in app.winfo_children()
                      if isinstance(w, tk.Toplevel) and w.title() == "积分充值与订单"]
        trees = []
        if points_win:
            def walk(widget):
                if isinstance(widget, ttk.Treeview):
                    trees.append(widget)
                for child in widget.winfo_children():
                    walk(child)
            walk(points_win[0])
        check("云端模式可打开订单窗口并显示积分流水",
              len(points_win) == 1 and not errors and len(trees) == 2
              and len(trees[1].get_children()) > 0, errors)
        if points_win:
            points_win[0].destroy()
            app.update()
    finally:
        app.destroy()


def run():
    service = cloud_server.CloudService(str(OUT / "cloud_test.db"))
    httpd = cloud_server.CloudHTTPServer(("127.0.0.1", 0), cloud_server.Handler)
    httpd.service = service
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    cloud_client.save_config({
        "server_url": "http://127.0.0.1:%d" % httpd.server_address[1],
        "enabled": False})
    try:
        health = cloud_client.health()
        check("回环服务健康检查且模拟模型身份明确", health["ok"] and health["model_mode"] == "mock")

        account = cloud_client.register("云端测试用户", "test-password-123")
        check("注册即登录并赠送 100 云端积分",
              account["balance"] == 100 and cloud_client.is_active()
              and account["user"]["username"] == "云端测试用户")
        try:
            cloud_client.register("云端测试用户", "another-password-123")
            check("重复用户名返回 409", False)
        except cloud_client.CloudClientError as ex:
            check("重复用户名返回 409", ex.status == 409)
        try:
            cloud_client.login("云端测试用户", "wrong-password")
            check("错误密码返回 401", False)
        except cloud_client.CloudClientError as ex:
            check("错误密码返回 401", ex.status == 401)
        try:
            cloud_client._request("GET", "/api/v1/account", auth=False)
            check("无 Token 不能读取云端账户", False)
        except cloud_client.CloudClientError as ex:
            check("无 Token 不能读取云端账户", ex.status == 401)

        mock = cloud_client.purchase("standard", "mock")
        check("云端模拟购买标准包入账 500",
              mock["status"] == "模拟已付款" and mock["balance"] == 600)
        repeated = cloud_client.sync_order(mock["order_id"])
        ledger = cloud_client.list_ledger()
        conn = service.connect()
        n = conn.execute("SELECT COUNT(*) FROM points_ledger WHERE source='mock_purchase'").fetchone()[0]
        conn.close()
        check("云端重复查单不重复入账且流水可查",
              repeated["balance"] == 600 and n == 1 and len(ledger) == 2
              and ledger[0]["source"] == "mock_purchase"
              and ledger[0]["balance_after"] == 600
              and ledger[1]["source"] == "grant")
        try:
            cloud_client.purchase("starter", "vibe")
            check("模拟模型环境禁止真实付款", False)
        except cloud_client.CloudClientError as ex:
            check("模拟模型环境禁止真实付款", ex.status == 409)

        # fake Vibe 完整服务端链路，无真实网关。
        FakeVibe.answers = [("WAIT", "等待买家付款", ""),
                            ("SUCCESS", "支付成功", "FAKE-CLOUD-ALIPAY-1")]
        FakeVibe.queries = 0
        original_mode = service.model_mode
        service.model_mode = lambda: "api"
        with patch("cloud_server.payment.VibePayChannel", FakeVibe):
            vibe = cloud_client.purchase("starter", "vibe")
            check("云端真实通道建单时不加积分",
                  vibe["status"] == "等待买家付款" and vibe["balance"] == 600)
            wait = cloud_client.sync_order(vibe["order_id"])
            success = cloud_client.sync_order(vibe["order_id"])
            again = cloud_client.sync_order(vibe["order_id"])
            check("云端 WAIT→SUCCESS 入账且重复同步防重",
                  wait["state"] == "WAIT" and success["balance"] == 700
                  and again["balance"] == 700 and FakeVibe.queries == 2)
            try:
                reopened = cloud_client.reopen_order(vibe["order_id"])
                check("已支付订单不能重复打开收银台", False, reopened)
            except cloud_client.CloudClientError as ex:
                check("已支付订单不能重复打开收银台", ex.status == 409)
        service.model_mode = original_mode

        # 桌面 llm/greetings 入口实际走云端。
        db.init_db()
        p1 = save_person("云端甲")
        save_person("云端乙")
        persons = db.list_persons()
        judged = llm.judge_all(persons)
        check("桌面关系判断走云端并扣 10 积分",
              judged["mode"] == "mock" and judged["cost"] == 10
              and judged["balance"] == 690)
        text, src = greetings.craft_one(p1, "生日", "fake-model")
        expense_ledger = cloud_client.list_ledger(direction="expense")
        check("桌面祝福语走云端扣 2 积分且支出流水可筛选",
              src == "cloud_mock" and bool(text) and cloud_client.cached_balance() == 688
              and [row["delta"] for row in expense_ledger] == [-2, -10])

        # request_id 幂等、冲突和失败退款直接检查服务层。
        uid = cloud_client.current_user()["id"]
        calls = []
        payload = {"demo": "same-request"}
        first = service.run_ai(uid, "idem-request-001", "greeting", payload,
                               lambda: calls.append(1) or {"text": "ok"})
        second = service.run_ai(uid, "idem-request-001", "greeting", payload,
                                lambda: calls.append(1) or {"text": "should-not-run"})
        check("相同 request_id 返回原结果且不重复扣费",
              first["balance"] == 686 and second["balance"] == 686 and len(calls) == 1)
        try:
            service.run_ai(uid, "idem-request-001", "greeting", {"demo": "changed"},
                           lambda: {"text": "bad"})
            check("相同 request_id 不同载荷返回 409", False)
        except cloud_server.CloudError as ex:
            check("相同 request_id 不同载荷返回 409", ex.status == 409)
        try:
            service.run_ai(uid, "failed-request-001", "greeting", payload,
                           lambda: (_ for _ in ()).throw(RuntimeError("fake model failure")))
            check("模型失败返回错误", False)
        except cloud_server.CloudError as ex:
            check("模型失败自动退回预扣积分",
                  ex.status == 502 and service.account(uid)["balance"] == 686)
        old_cost = cloud_server.AI_COSTS["greeting"]
        cloud_server.AI_COSTS["greeting"] = 10000
        try:
            service.run_ai(uid, "poor-request-001", "greeting", payload,
                           lambda: {"text": "must-not-run"})
            check("余额不足返回 402", False)
        except cloud_server.CloudError as ex:
            check("余额不足返回 402 且不调用模型",
                  ex.status == 402 and service.account(uid)["balance"] == 686)
        finally:
            cloud_server.AI_COSTS["greeting"] = old_cost

        # OpenAI 兼容协议实测：fake HTTP 模型只监听回环地址。
        model_httpd = cloud_server.CloudHTTPServer(("127.0.0.1", 0), FakeModelHandler)
        model_thread = threading.Thread(target=model_httpd.serve_forever, daemon=True)
        model_thread.start()
        os.environ["JINBU_MODEL_BASE_URL"] = "http://127.0.0.1:%d" % model_httpd.server_address[1]
        os.environ["JINBU_MODEL_API_KEY"] = "server-only-test-key"
        os.environ["JINBU_MODEL_NAME"] = "fake-openai-model"
        try:
            api_judged = llm.judge_all(persons)
            check("云端 OpenAI 兼容关系请求成功并扣 10 积分",
                  api_judged["mode"] == "api" and api_judged["balance"] == 676)
            api_text, api_src = greetings.craft_one(p1, "生日", "fake-openai-model")
            check("云端 OpenAI 兼容祝福请求成功并扣 2 积分",
                  api_src == "cloud_api" and api_text == "来自云端 API 的专属祝福"
                  and cloud_client.cached_balance() == 674)
            FakeModelHandler.fail = True
            try:
                greetings.craft_one(p1, "生日", "fake-openai-model")
                check("模型 HTTP 500 返回错误", False)
            except cloud_client.CloudClientError as ex:
                check("模型 HTTP 500 时服务端退回预扣积分",
                      ex.status == 502 and service.account(uid)["balance"] == 674)
            check("模型密钥只出现在服务端到假模型的请求中",
                  FakeModelHandler.authorizations
                  and all(x == "Bearer server-only-test-key"
                          for x in FakeModelHandler.authorizations))
        finally:
            FakeModelHandler.fail = False
            for key in ("JINBU_MODEL_BASE_URL", "JINBU_MODEL_API_KEY", "JINBU_MODEL_NAME"):
                os.environ.pop(key, None)
            model_httpd.shutdown()
            model_httpd.server_close()
            model_thread.join(timeout=5)

        # 模拟服务在预扣后宕机：重启恢复必须退回陈旧的 reserved。
        conn = service.connect()
        bal = service._balance(conn, uid)
        conn.execute("INSERT INTO points_ledger(user_id,delta,balance_after,reason,source,ref_trade_no,created_at) "
                     "VALUES(?,?,?,?,?,?,?)",
                     (uid, -2, bal - 2, "AI功能：greeting", "consume",
                      "AI:%d:stale-request-001" % uid, cloud_server._now()))
        conn.execute("INSERT INTO ai_requests(user_id,request_id,request_hash,feature,cost,status,created_at,updated_at) "
                     "VALUES(?,?,?,?,?,'reserved',?,?)",
                     (uid, "stale-request-001", "stale", "greeting", 2,
                      "2000-01-01 00:00:00", "2000-01-01 00:00:00"))
        conn.commit()
        conn.close()
        service.recover_stale_reservations()
        conn = service.connect()
        stale = conn.execute("SELECT status FROM ai_requests WHERE user_id=? AND request_id='stale-request-001'",
                             (uid,)).fetchone()
        conn.close()
        check("异常重启自动退回超时预扣积分",
              stale["status"] == "failed" and service.account(uid)["balance"] == bal)

        cfg = cloud_client.load_config()
        check("桌面配置和健康接口不包含模型 API Key",
              "api_key" not in cfg and "api_key" not in health)
        if "--ui" in sys.argv:
            verify_ui()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


try:
    run()
    status = "passed"
except BaseException:
    import traceback
    print(traceback.format_exc(), flush=True)
    status = "failed"
finally:
    REPORT.write_text(json.dumps({
        "version": "v0.69-cloud", "status": status,
        "network": "loopback-only", "real_model_call": False,
        "real_payment": False, "checks": checks}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps({"status": status, "checks": len(checks),
                      "report": str(REPORT)}, ensure_ascii=False), flush=True)
    sys.exit(0 if status == "passed" else 1)
