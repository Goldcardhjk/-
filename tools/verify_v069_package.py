# -*- coding: utf-8 -*-
"""直接验收 v0.69 试用目录中的双 exe；全部数据写入隔离 _tmp 目录。"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "进步壹号_Payathon提交" / "04_可试用作品_v069"
BASE = ROOT / "_tmp" / "v069" / "package_check"
RUN = BASE / ("run_" + time.strftime("%Y%m%d_%H%M%S"))
RUN.mkdir(parents=True, exist_ok=True)
REPORT = BASE / "verification.json"
PORT = 8876
URL = "http://127.0.0.1:%d" % PORT
server = None
desktop = None
desktop_hwnd = None
result = {"status": "running", "real_model": False, "real_payment": False}


def api(method, path, token=None, payload=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(URL + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def visible_windows():
    rows = []
    user32 = ctypes.windll.user32
    enum_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            size = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(size + 1)
            user32.GetWindowTextW(hwnd, buf, size + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if buf.value:
                rows.append((hwnd, pid.value, buf.value))
        return True

    user32.EnumWindows(enum_type(callback), 0)
    return rows


def responding(hwnd):
    user32 = ctypes.windll.user32
    fn = user32.SendMessageTimeoutW
    fn.restype = wintypes.LPARAM
    fn.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                   wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    out = ctypes.c_size_t()
    return bool(fn(hwnd, 0, 0, 0, 0x0002, 2000, ctypes.byref(out)))


try:
    env = os.environ.copy()
    for key in ("JINBU_MODEL_BASE_URL", "JINBU_MODEL_API_KEY", "JINBU_MODEL_NAME"):
        env.pop(key, None)
    env.update({
        "JINBU_SERVER_HOST": "127.0.0.1", "JINBU_SERVER_PORT": str(PORT),
        "JINBU_SERVER_DB": str(RUN / "cloud.db"), "JINBU_SERVER_ALLOW_MOCK": "1",
        "JINBU_DB_PATH": str(RUN / "desktop.db"),
        "JINBU_CLOUD_CONFIG": str(RUN / "cloud_client.json"),
        "JINBU_CLOUD_SESSION": str(RUN / "cloud_session.json"),
    })
    server_log = open(RUN / "server.log", "wb")
    server = subprocess.Popen(
        [str(PACKAGE / "JinbuYihaoCloudServer_v069.exe")], cwd=str(PACKAGE),
        env=env, stdout=server_log, stderr=subprocess.STDOUT)
    health = None
    for _ in range(60):
        try:
            health = api("GET", "/api/v1/health")
            break
        except Exception:
            if server.poll() is not None:
                raise RuntimeError("云端 exe 提前退出，详见 server.log")
            time.sleep(0.25)
    if not health or not health.get("ok") or health.get("model_mode") != "mock":
        raise AssertionError("云端 exe 健康检查失败")

    account = api("POST", "/api/v1/auth/register", payload={
        "username": "package_ledger_demo", "password": "PackageDemo2026!"})
    token = account["token"]
    if account.get("balance") != 100:
        raise AssertionError("注册赠分不是 100")
    purchase = api("POST", "/api/v1/points/purchase", token, {
        "product_key": "standard", "channel": "mock"})
    if purchase.get("balance") != 600:
        raise AssertionError("模拟购买后余额不是 600")
    relation = api("POST", "/api/v1/ai/relation-judge", token, {
        "request_id": uuid.uuid4().hex,
        "persons": [{"name": "云端甲", "relation": "朋友", "likes": "", "taboos": "",
                     "importance": "重要", "dates": []}], "extra_links": []})
    if relation.get("balance") != 590 or relation.get("cost") != 10:
        raise AssertionError("关系判断计费不符")
    greeting = api("POST", "/api/v1/ai/greeting", token, {
        "request_id": uuid.uuid4().hex,
        "context": {"name": "云端甲", "relation": "朋友", "prefs": ["阅读"],
                    "last_gift": "", "memo": "", "recent": [], "imp": []},
        "holiday": "生日", "model": "mock"})
    if greeting.get("balance") != 588 or greeting.get("cost") != 2:
        raise AssertionError("祝福生成计费不符")
    ledger = api("GET", "/api/v1/points/ledger?limit=100&direction=all", token)
    expenses = api("GET", "/api/v1/points/ledger?limit=100&direction=expense", token)
    rows = ledger.get("ledger", [])
    expense_rows = expenses.get("ledger", [])
    if (ledger.get("balance") != 588 or len(rows) != 4
            or [row["delta"] for row in rows] != [-2, -10, 500, 100]
            or [row["delta"] for row in expense_rows] != [-2, -10]
            or not all(row.get("created_at") and row.get("reason") and row.get("source")
                       for row in rows)):
        raise AssertionError("打包版流水内容、倒序或收支筛选不符")
    try:
        api("GET", "/api/v1/points/ledger", None)
        raise AssertionError("无 Token 的流水请求不应成功")
    except urllib.error.HTTPError as ex:
        if ex.code != 401:
            raise

    before = {row[0] for row in visible_windows()}
    desktop = subprocess.Popen(
        [str(PACKAGE / "JinbuYihaoAgent_v069.exe")], cwd=str(PACKAGE), env=env)
    expected = "进步壹号 Agent v0.69 · 关系网络 · 云端积分 · MCP 智能体"
    for _ in range(80):
        candidates = [row for row in visible_windows()
                      if row[0] not in before and row[2] == expected]
        if candidates:
            desktop_hwnd, desktop_pid, desktop_title = candidates[0]
            break
        if desktop.poll() is not None:
            raise RuntimeError("桌面 exe 提前退出")
        time.sleep(0.25)
    if not desktop_hwnd:
        raise AssertionError("桌面 exe 窗口未出现")
    desktop_responding = responding(desktop_hwnd)
    if not desktop_responding:
        raise AssertionError("桌面 exe 窗口无响应")

    result.update({
        "status": "passed", "server_pid": server.pid,
        "desktop_pid": desktop.pid, "desktop_child_pid": desktop_pid,
        "desktop_title": desktop_title, "desktop_responding": desktop_responding,
        "register_balance": 100, "purchase_balance": 600,
        "relation_cost": 10, "relation_balance": 590,
        "greeting_cost": 2, "greeting_balance": 588,
        "ledger_count": len(rows), "ledger_deltas": [row["delta"] for row in rows],
        "expense_deltas": [row["delta"] for row in expense_rows],
        "unauthorized_ledger_status": 401,
        "work_dir": str(RUN),
    })
except Exception as ex:
    result.update({"status": "failed", "error": str(ex), "work_dir": str(RUN)})
    raise
finally:
    if desktop_hwnd:
        try:
            ctypes.windll.user32.PostMessageW(desktop_hwnd, 0x0010, 0, 0)
        except Exception:
            pass
    if desktop is not None:
        try:
            desktop.wait(timeout=5)
        except Exception:
            subprocess.run(["taskkill", "/PID", str(desktop.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if server is not None:
        # PyInstaller 为父/子双进程；必须清理本脚本创建的整棵进程树。
        subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            server.wait(timeout=5)
        except Exception:
            pass
    try:
        server_log.close()
    except Exception:
        pass
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
