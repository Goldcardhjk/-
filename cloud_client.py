# -*- coding: utf-8 -*-
"""桌面端云端客户端：只保存服务器地址和用户 Token，不保存模型 API Key。"""
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent)
CONFIG_PATH = os.environ.get("JINBU_CLOUD_CONFIG", str(ROOT / "cloud_client.json"))
SESSION_PATH = os.environ.get("JINBU_CLOUD_SESSION", str(ROOT / "data" / "cloud_session.json"))
DEFAULTS = {"enabled": False, "server_url": "http://127.0.0.1:8765"}
_LOCK = threading.Lock()


class CloudClientError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = int(status)


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    out = DEFAULTS.copy()
    out.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    out["server_url"] = out["server_url"].rstrip("/")
    return out


def save_config(cfg):
    old = load_config()
    old.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    old["server_url"] = old["server_url"].rstrip("/")
    with _LOCK:
        os.makedirs(os.path.dirname(os.path.abspath(CONFIG_PATH)), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    return old


def _load_session():
    try:
        with open(SESSION_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_session(data):
    with _LOCK:
        os.makedirs(os.path.dirname(os.path.abspath(SESSION_PATH)), exist_ok=True)
        tmp = SESSION_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SESSION_PATH)


def is_active():
    return bool(load_config().get("enabled") and _load_session().get("token"))


def cached_balance():
    return _load_session().get("balance")


def current_user():
    return _load_session().get("user") or {}


def set_enabled(enabled):
    if enabled and not _load_session().get("token"):
        raise CloudClientError("请先登录云端账号")
    return save_config({"enabled": bool(enabled)})


def _request(method, path, body=None, auth=True, timeout=40):
    url = load_config()["server_url"] + path
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if raw is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    if auth:
        token = _load_session().get("token")
        if not token:
            raise CloudClientError("请先登录云端账号", 401)
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=raw, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read(10 * 1024 * 1024 + 1)
            if len(content) > 10 * 1024 * 1024:
                raise CloudClientError("服务端响应超过 10MB")
            return json.loads(content.decode("utf-8"))
    except urllib.error.HTTPError as ex:
        try:
            data = json.loads(ex.read().decode("utf-8"))
            msg = data.get("error") or str(ex)
        except Exception:
            msg = "服务端 HTTP %s" % ex.code
        raise CloudClientError(msg, ex.code) from ex
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise CloudClientError("无法连接云端服务：%s" % ex) from ex
    except ValueError as ex:
        raise CloudClientError("服务端返回了无效 JSON") from ex


def health():
    return _request("GET", "/api/v1/health", auth=False, timeout=8)


def _store_login(data):
    _save_session({"token": data["token"], "user": data["user"],
                   "balance": data.get("balance", 0)})
    save_config({"enabled": True})
    return {"user": data["user"], "balance": data.get("balance", 0)}


def register(username, password):
    return _store_login(_request("POST", "/api/v1/auth/register", {
        "username": username, "password": password}, auth=False))


def login(username, password):
    return _store_login(_request("POST", "/api/v1/auth/login", {
        "username": username, "password": password}, auth=False))


def logout():
    save_config({"enabled": False})
    try:
        os.remove(SESSION_PATH)
    except FileNotFoundError:
        pass


def account():
    data = _request("GET", "/api/v1/account")
    session = _load_session()
    session["user"] = data["user"]
    session["balance"] = data["balance"]
    _save_session(session)
    return data


def list_orders():
    return _request("GET", "/api/v1/points/orders")["orders"]


def list_ledger(limit=100, direction="all"):
    direction = (direction or "all").strip().lower()
    if direction not in ("all", "income", "expense"):
        raise ValueError("流水方向仅支持 all / income / expense")
    limit = max(1, min(500, int(limit)))
    query = urllib.parse.urlencode({"limit": limit, "direction": direction})
    return _request("GET", "/api/v1/points/ledger?" + query)["ledger"]


def purchase(product_key, channel):
    data = _request("POST", "/api/v1/points/purchase", {
        "product_key": product_key, "channel": channel})
    session = _load_session()
    session["balance"] = data.get("balance", session.get("balance", 0))
    _save_session(session)
    return data


def sync_order(order_id):
    data = _request("POST", "/api/v1/points/sync", {"order_id": int(order_id)})
    session = _load_session()
    session["balance"] = data.get("balance", session.get("balance", 0))
    _save_session(session)
    return data


def reopen_order(order_id):
    return _request("POST", "/api/v1/points/reopen", {"order_id": int(order_id)})


def relation_judge(persons, extra_links=None):
    request_id = uuid.uuid4().hex
    data = _request("POST", "/api/v1/ai/relation-judge", {
        "request_id": request_id, "persons": persons,
        "extra_links": extra_links or []}, timeout=120)
    result = data["result"]
    result["balance"] = data["balance"]
    result["cost"] = data["cost"]
    result["request_id"] = request_id
    session = _load_session()
    session["balance"] = data["balance"]
    _save_session(session)
    return result


def greeting(context, holiday, model=None):
    request_id = uuid.uuid4().hex
    data = _request("POST", "/api/v1/ai/greeting", {
        "request_id": request_id, "context": context,
        "holiday": holiday, "model": model}, timeout=120)
    result = data["result"]
    result["balance"] = data["balance"]
    result["cost"] = data["cost"]
    session = _load_session()
    session["balance"] = data["balance"]
    _save_session(session)
    return result
