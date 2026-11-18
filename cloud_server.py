# -*- coding: utf-8 -*-
"""进步壹号云端账号、积分与模型网关（Python 标准库）。

默认仅监听 127.0.0.1。公网部署必须放在 HTTPS 反向代理之后，并通过环境变量
配置模型密钥；API Key 永不下发桌面端。
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import greetings
import llm
import payment
import points

ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent)
PRODUCTS = points.PRODUCTS
AI_COSTS = points.AI_COSTS


class CloudError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = int(status)
        self.message = str(message)


def _hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210000)
    return salt.hex(), digest.hex()


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class CloudService:
    def __init__(self, db_path=None):
        self.db_path = os.path.abspath(db_path or os.environ.get(
            "JINBU_SERVER_DB", str(ROOT / "data" / "cloud.db")))
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()
        self.recover_stale_reservations()

    def recover_stale_reservations(self, max_age_minutes=15):
        """进程在预扣后崩溃时，重启自动退回超时未完成的请求。"""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT user_id,request_id,feature,cost FROM ai_requests "
                "WHERE status='reserved' AND updated_at < "
                "datetime('now','localtime', ?)",
                ("-%d minutes" % int(max_age_minutes),)).fetchall()
            for row in rows:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    current = conn.execute(
                        "SELECT status FROM ai_requests WHERE user_id=? AND request_id=?",
                        (row["user_id"], row["request_id"])).fetchone()
                    if not current or current["status"] != "reserved":
                        conn.rollback()
                        continue
                    bal = self._balance(conn, row["user_id"])
                    conn.execute(
                        "INSERT INTO points_ledger(user_id,delta,balance_after,reason,source,ref_trade_no,created_at) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (row["user_id"], row["cost"], bal + row["cost"],
                         "服务中断退回：" + row["feature"], "refund",
                         "APIREFUND:%d:%s" % (row["user_id"], row["request_id"]), _now()))
                    conn.execute(
                        "UPDATE ai_requests SET status='failed',error=?,updated_at=? "
                        "WHERE user_id=? AND request_id=?",
                        ("服务中断，预扣积分已自动退回", _now(),
                         row["user_id"], row["request_id"]))
                    conn.commit()
                except sqlite3.IntegrityError:
                    conn.rollback()       # 已退款过，保持幂等
                except Exception:
                    conn.rollback()
                    raise
        finally:
            conn.close()

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=20000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self):
        conn = self.connect()
        conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            username_norm TEXT NOT NULL UNIQUE,
            password_salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            expires_at REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS points_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            delta INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL,
            ref_trade_no TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_ledger_ref
            ON points_ledger(ref_trade_no) WHERE ref_trade_no <> '';
        CREATE TABLE IF NOT EXISTS points_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            product_key TEXT NOT NULL,
            product_name TEXT NOT NULL,
            points INTEGER NOT NULL,
            amount REAL NOT NULL,
            out_trade_no TEXT NOT NULL UNIQUE,
            channel TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '待付款',
            pay_ref TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            paid_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_order_pay_ref
            ON points_orders(pay_ref) WHERE pay_ref <> '';
        CREATE TABLE IF NOT EXISTS ai_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            request_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            feature TEXT NOT NULL,
            cost INTEGER NOT NULL,
            status TEXT NOT NULL,
            response_json TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, request_id)
        );
        """)
        conn.commit()
        conn.close()

    @staticmethod
    def _balance(conn, user_id):
        return int(conn.execute(
            "SELECT COALESCE(SUM(delta),0) FROM points_ledger WHERE user_id=?",
            (user_id,)).fetchone()[0] or 0)

    @staticmethod
    def _public_user(conn, user_id):
        row = conn.execute("SELECT id,username,created_at FROM users WHERE id=?",
                           (user_id,)).fetchone()
        if not row:
            raise CloudError(401, "账号不存在或已失效")
        return {"id": row["id"], "username": row["username"],
                "created_at": row["created_at"]}

    def _new_session(self, conn, user_id):
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) "
                     "VALUES(?,?,?,?)",
                     (_token_hash(token), user_id, time.time() + 30 * 86400, _now()))
        return token

    def register(self, username, password):
        username = (username or "").strip()
        if not 2 <= len(username) <= 32:
            raise CloudError(400, "用户名长度需为 2–32 个字符")
        if not 8 <= len(password or "") <= 128:
            raise CloudError(400, "密码长度需为 8–128 个字符")
        salt, digest = _hash_password(password)
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO users(username,username_norm,password_salt,password_hash,created_at) "
                "VALUES(?,?,?,?,?)",
                (username, username.casefold(), salt, digest, _now()))
            uid = int(cur.lastrowid)
            conn.execute("INSERT INTO points_ledger(user_id,delta,balance_after,reason,source,ref_trade_no,created_at) "
                         "VALUES(?,100,100,'新账号体验积分','grant',?,?)",
                         (uid, "FREE100:%d" % uid, _now()))
            token = self._new_session(conn, uid)
            conn.commit()
            return {"token": token, "user": self._public_user(conn, uid),
                    "balance": 100}
        except sqlite3.IntegrityError as ex:
            conn.rollback()
            raise CloudError(409, "用户名已注册") from ex
        finally:
            conn.close()

    def login(self, username, password):
        conn = self.connect()
        row = conn.execute("SELECT * FROM users WHERE username_norm=?",
                           ((username or "").strip().casefold(),)).fetchone()
        if not row:
            conn.close()
            raise CloudError(401, "用户名或密码错误")
        _, expected = _hash_password(password or "", bytes.fromhex(row["password_salt"]))
        if not hmac.compare_digest(expected, row["password_hash"]):
            conn.close()
            raise CloudError(401, "用户名或密码错误")
        token = self._new_session(conn, row["id"])
        conn.commit()
        out = {"token": token, "user": self._public_user(conn, row["id"]),
               "balance": self._balance(conn, row["id"])}
        conn.close()
        return out

    def authenticate(self, token):
        if not token:
            raise CloudError(401, "请先登录云端账号")
        conn = self.connect()
        row = conn.execute("SELECT user_id,expires_at FROM sessions WHERE token_hash=?",
                           (_token_hash(token),)).fetchone()
        if not row or float(row["expires_at"]) < time.time():
            conn.close()
            raise CloudError(401, "登录已过期，请重新登录")
        user = self._public_user(conn, row["user_id"])
        conn.close()
        return user

    def account(self, user_id):
        conn = self.connect()
        out = {"user": self._public_user(conn, user_id),
               "balance": self._balance(conn, user_id),
               "model_mode": self.model_mode()}
        conn.close()
        return out

    @staticmethod
    def model_mode():
        cfg = llm.load_config()
        if cfg.get("base_url") and cfg.get("api_key") and cfg.get("model"):
            return "api"
        return "mock" if os.environ.get("JINBU_SERVER_ALLOW_MOCK", "1") == "1" else "off"

    def list_orders(self, user_id):
        conn = self.connect()
        rows = conn.execute("SELECT * FROM points_orders WHERE user_id=? "
                            "ORDER BY id DESC LIMIT 50", (user_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def list_ledger(self, user_id, limit=100, direction="all"):
        """只返回当前 Token 对应用户的积分流水，支持收支筛选。"""
        direction = (direction or "all").strip().lower()
        if direction not in ("all", "income", "expense"):
            raise CloudError(400, "流水方向仅支持 all / income / expense")
        try:
            limit = max(1, min(500, int(limit)))
        except (TypeError, ValueError) as ex:
            raise CloudError(400, "流水条数必须为整数") from ex
        where = "user_id=?"
        params = [int(user_id)]
        if direction == "income":
            where += " AND delta>0"
        elif direction == "expense":
            where += " AND delta<0"
        params.append(limit)
        conn = self.connect()
        rows = conn.execute(
            "SELECT id,delta,balance_after,reason,source,ref_trade_no,created_at "
            "FROM points_ledger WHERE " + where + " ORDER BY id DESC LIMIT ?",
            params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _order(self, conn, oid, user_id):
        row = conn.execute("SELECT * FROM points_orders WHERE id=? AND user_id=?",
                           (int(oid), int(user_id))).fetchone()
        if not row:
            raise CloudError(404, "积分订单不存在")
        return row

    def _mark_paid(self, conn, order, trade_no, status):
        if order["status"] in ("已付款", "模拟已付款"):
            return self._balance(conn, order["user_id"]), False
        if not trade_no:
            raise CloudError(502, "网关未返回交易号，请人工核对")
        used = conn.execute("SELECT id FROM points_orders WHERE pay_ref=? AND id<>?",
                            (trade_no, order["id"])).fetchone()
        if used:
            raise CloudError(409, "支付交易号已绑定其他订单")
        bal = self._balance(conn, order["user_id"]) + int(order["points"])
        conn.execute("INSERT INTO points_ledger(user_id,delta,balance_after,reason,source,ref_trade_no,created_at) "
                     "VALUES(?,?,?,?,?,?,?)",
                     (order["user_id"], order["points"], bal,
                      "购买" + order["product_name"],
                      "mock_purchase" if order["channel"] == "mock" else "purchase",
                      trade_no, _now()))
        conn.execute("UPDATE points_orders SET status=?,pay_ref=?,paid_at=? WHERE id=?",
                     (status, trade_no, _now(), order["id"]))
        return bal, True

    def purchase(self, user_id, product_key, channel):
        product = PRODUCTS.get(product_key)
        if not product:
            raise CloudError(400, "未知积分包")
        if channel not in ("mock", "vibe"):
            raise CloudError(400, "未知支付通道")
        if channel == "vibe" and self.model_mode() != "api":
            raise CloudError(409, "当前为模拟模型环境，禁止真实付款；请先配置服务端模型 API")
        out_no = "JBYHC%s%s" % (datetime.now().strftime("%Y%m%d%H%M%S"),
                                 secrets.token_hex(4).upper())
        conn = self.connect()
        cur = conn.execute("INSERT INTO points_orders(user_id,product_key,product_name,points,amount,out_trade_no,channel,status,created_at) "
                           "VALUES(?,?,?,?,?,?,?,'待付款',?)",
                           (user_id, product_key, product["name"], product["points"],
                            product["price"], out_no, channel, _now()))
        oid = int(cur.lastrowid)
        conn.commit()
        try:
            if channel == "mock":
                order = self._order(conn, oid, user_id)
                bal, _ = self._mark_paid(conn, order, "MOCK-" + out_no, "模拟已付款")
                conn.commit()
                return {"order_id": oid, "out_trade_no": out_no, "channel": channel,
                        "status": "模拟已付款", "balance": bal, "pay_url": "",
                        "product": product}
            chan = payment.VibePayChannel()
            ready, why = chan.ready()
            if not ready:
                raise CloudError(503, "服务端 Vibe Pay 未配置：%s" % why)
            intent = chan.start_payment(out_no, product["price"],
                                        "进步壹号%s %d积分" % (product["name"], product["points"]))
            conn.execute("UPDATE points_orders SET status='等待买家付款' WHERE id=?", (oid,))
            conn.commit()
            return {"order_id": oid, "out_trade_no": out_no, "channel": channel,
                    "status": "等待买家付款", "balance": self._balance(conn, user_id),
                    "pay_url": intent["pay_url"], "product": product}
        except Exception:
            conn.rollback()
            conn.execute("UPDATE points_orders SET status='下单失败' WHERE id=? AND status='待付款'", (oid,))
            conn.commit()
            raise
        finally:
            conn.close()

    def sync_order(self, user_id, oid):
        conn = self.connect()
        order = self._order(conn, oid, user_id)
        if order["status"] in ("已付款", "模拟已付款"):
            bal = self._balance(conn, user_id)
            conn.close()
            return {"state": "SUCCESS", "message": "已到账，无需重复入账", "balance": bal}
        if order["channel"] == "mock":
            conn.close()
            raise CloudError(409, "模拟订单未到账，请重新下单")
        try:
            state, msg, trade_no = payment.VibePayChannel().query(order["out_trade_no"])
            if state == "SUCCESS":
                bal, credited = self._mark_paid(conn, order, trade_no, "已付款")
                conn.commit()
                return {"state": "SUCCESS", "balance": bal,
                        "message": "支付成功，已入账 %d 积分" % order["points"] if credited else "订单此前已入账"}
            status = "支付失败" if state == "FAIL" else "等待买家付款"
            conn.execute("UPDATE points_orders SET status=? WHERE id=?", (status, oid))
            conn.commit()
            return {"state": state, "message": msg, "balance": self._balance(conn, user_id)}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reopen_order(self, user_id, oid):
        conn = self.connect()
        order = self._order(conn, oid, user_id)
        conn.close()
        if order["channel"] != "vibe" or order["status"] in ("已付款", "模拟已付款"):
            raise CloudError(409, "该订单无需重新打开收银台")
        intent = payment.VibePayChannel().start_payment(
            order["out_trade_no"], order["amount"],
            "进步壹号%s %d积分" % (order["product_name"], order["points"]))
        return {"order_id": int(oid), "out_trade_no": order["out_trade_no"],
                "channel": "vibe", "status": order["status"],
                "pay_url": intent["pay_url"], "product": {
                    "name": order["product_name"], "points": order["points"],
                    "price": order["amount"]}}

    def run_ai(self, user_id, request_id, feature, payload, producer):
        if feature not in AI_COSTS:
            raise CloudError(400, "不支持的 AI 功能")
        if not 8 <= len(request_id or "") <= 100:
            raise CloudError(400, "request_id 长度需为 8–100 个字符")
        request_hash = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        cost = AI_COSTS[feature]
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM ai_requests WHERE user_id=? AND request_id=?",
                               (user_id, request_id)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise CloudError(409, "request_id 已用于不同请求")
                if old["status"] == "succeeded":
                    conn.commit()
                    return json.loads(old["response_json"])
                if old["status"] == "reserved":
                    raise CloudError(409, "相同请求正在处理中")
                raise CloudError(502, old["error"] or "上次调用失败，请使用新 request_id 重试")
            bal = self._balance(conn, user_id)
            if bal < cost:
                raise CloudError(402, "积分不足（余额 %d，需要 %d），请先充值" % (bal, cost))
            ref = "AI:%d:%s" % (user_id, request_id)
            conn.execute("INSERT INTO points_ledger(user_id,delta,balance_after,reason,source,ref_trade_no,created_at) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (user_id, -cost, bal - cost, "AI功能：" + feature,
                          "consume", ref, _now()))
            conn.execute("INSERT INTO ai_requests(user_id,request_id,request_hash,feature,cost,status,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,'reserved',?,?)",
                         (user_id, request_id, request_hash, feature, cost, _now(), _now()))
            conn.commit()
        except sqlite3.IntegrityError as ex:
            conn.rollback()
            raise CloudError(409, "重复请求已处理") from ex
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        try:
            result = producer()
        except Exception as ex:
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT status FROM ai_requests WHERE user_id=? AND request_id=?",
                                   (user_id, request_id)).fetchone()
                if row and row["status"] == "reserved":
                    bal = self._balance(conn, user_id)
                    conn.execute("INSERT INTO points_ledger(user_id,delta,balance_after,reason,source,ref_trade_no,created_at) "
                                 "VALUES(?,?,?,?,?,?,?)",
                                 (user_id, cost, bal + cost, "API失败退回：" + feature,
                                  "refund", "APIREFUND:%d:%s" % (user_id, request_id), _now()))
                    conn.execute("UPDATE ai_requests SET status='failed',error=?,updated_at=? "
                                 "WHERE user_id=? AND request_id=?",
                                 (str(ex)[:500], _now(), user_id, request_id))
                    conn.commit()
            finally:
                conn.close()
            raise CloudError(502, "模型调用失败，预扣积分已退回：%s" % ex) from ex
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            bal = self._balance(conn, user_id)
            response = {"result": result, "cost": cost, "balance": bal,
                        "request_id": request_id}
            conn.execute("UPDATE ai_requests SET status='succeeded',response_json=?,updated_at=? "
                         "WHERE user_id=? AND request_id=? AND status='reserved'",
                         (json.dumps(response, ensure_ascii=False), _now(), user_id, request_id))
            conn.commit()
            return response
        finally:
            conn.close()

    def relation_judge(self, user_id, request_id, payload):
        persons = payload.get("persons") or []
        extra = payload.get("extra_links") or []
        if not isinstance(persons, list) or not persons:
            raise CloudError(400, "persons 不能为空")
        mode = self.model_mode()
        if mode == "off":
            raise CloudError(503, "服务端模型 API 未配置")

        def produce():
            if mode == "mock":
                return {"mode": "mock", "my_scores": llm._mock_my_scores(persons),
                        "links": llm._mock_links(persons, extra)}
            raw = llm._api_judge(persons, extra)
            return {"mode": "api", "my_scores": raw.get("my_scores") or {},
                    "links": raw.get("links") or []}
        return self.run_ai(user_id, request_id, "relation_judge", payload, produce)

    def greeting(self, user_id, request_id, payload):
        ctx = payload.get("context") or {}
        holiday = payload.get("holiday") or ""
        model = payload.get("model") or None
        if not ctx.get("name") or not holiday:
            raise CloudError(400, "人物上下文和节日不能为空")
        mode = self.model_mode()
        if mode == "off":
            raise CloudError(503, "服务端模型 API 未配置")

        def produce():
            text = (greetings._mock_craft(ctx, holiday) if mode == "mock"
                    else greetings._api_craft(ctx, holiday, model))
            return {"text": text, "source": mode}
        return self.run_ai(user_id, request_id, "greeting", payload, produce)


class CloudHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "JinbuCloud/1.0"

    def _send(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as ex:
            raise CloudError(400, "无效 Content-Length") from ex
        if size <= 0 or size > 2 * 1024 * 1024:
            raise CloudError(413, "请求体为空或超过 2MB")
        try:
            data = json.loads(self.rfile.read(size).decode("utf-8"))
        except (ValueError, UnicodeError) as ex:
            raise CloudError(400, "请求体必须是 UTF-8 JSON") from ex
        if not isinstance(data, dict):
            raise CloudError(400, "JSON 顶层必须是对象")
        return data

    def _user(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise CloudError(401, "缺少 Bearer Token")
        return self.server.service.authenticate(auth[7:].strip())["id"]

    def _dispatch(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        service = self.server.service
        if self.command == "GET" and path == "/api/v1/health":
            return 200, {"ok": True, "model_mode": service.model_mode(),
                         "products": PRODUCTS, "costs": AI_COSTS}
        if self.command == "POST" and path == "/api/v1/auth/register":
            body = self._body()
            return 200, service.register(body.get("username"), body.get("password"))
        if self.command == "POST" and path == "/api/v1/auth/login":
            body = self._body()
            return 200, service.login(body.get("username"), body.get("password"))
        uid = self._user()
        if self.command == "GET" and path == "/api/v1/account":
            return 200, service.account(uid)
        if self.command == "GET" and path == "/api/v1/points/orders":
            return 200, {"orders": service.list_orders(uid)}
        if self.command == "GET" and path == "/api/v1/points/ledger":
            query = urllib.parse.parse_qs(parsed.query)
            rows = service.list_ledger(
                uid, query.get("limit", ["100"])[0],
                query.get("direction", ["all"])[0])
            return 200, {"ledger": rows, "balance": service.account(uid)["balance"]}
        if self.command == "POST" and path == "/api/v1/points/purchase":
            body = self._body()
            return 200, service.purchase(uid, body.get("product_key"), body.get("channel"))
        if self.command == "POST" and path == "/api/v1/points/sync":
            return 200, service.sync_order(uid, self._body().get("order_id"))
        if self.command == "POST" and path == "/api/v1/points/reopen":
            return 200, service.reopen_order(uid, self._body().get("order_id"))
        if self.command == "POST" and path == "/api/v1/ai/relation-judge":
            body = self._body()
            return 200, service.relation_judge(uid, body.get("request_id"), body)
        if self.command == "POST" and path == "/api/v1/ai/greeting":
            body = self._body()
            return 200, service.greeting(uid, body.get("request_id"), body)
        raise CloudError(404, "接口不存在")

    def _handle(self):
        try:
            status, data = self._dispatch()
            self._send(status, data)
        except CloudError as ex:
            self._send(ex.status, {"error": ex.message})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            import traceback
            traceback.print_exc()
            self._send(500, {"error": "服务端内部错误"})

    do_GET = _handle
    do_POST = _handle


def main():
    parser = argparse.ArgumentParser(description="进步壹号云端账号与模型计费服务")
    parser.add_argument("--host", default=os.environ.get("JINBU_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("JINBU_SERVER_PORT", "8765")))
    parser.add_argument("--db", default=os.environ.get("JINBU_SERVER_DB"))
    args = parser.parse_args()
    service = CloudService(args.db)
    server = CloudHTTPServer((args.host, args.port), Handler)
    server.service = service
    print("进步壹号云端服务：http://%s:%d（模型模式：%s）" %
          (args.host, args.port, service.model_mode()), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
