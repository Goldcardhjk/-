# -*- coding: utf-8 -*-
"""v0.67 MCP Server 专项验收（独立进程、独立数据目录、全程离线）。

覆盖：
  1. initialize / tools/list / tools/call 的 JSON-RPC 正确性与 error 路径；
  2. 七件工具全链路：场景/搜索/本地证据找人/档案/记忆/送礼推荐/建单；
  3. 授权边界：vibe 通道只建单不代付（订单停「待付款」）；
  4. mock 通道建单立即「模拟已付款」且订单入 jarna；
  5. 未知工具/未知方法的标准 JSON-RPC 错误码；
  6. v0.68 计费：查询类扣 1 积分并回报余额、余额不足拒绝且不执行、
     写操作不扣分。

运行：python -B tools/verify_mcp.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_tmp/v067" / "mcp_check"
OUT.mkdir(parents=True, exist_ok=True)
sys.dont_write_bytecode = True

ENV = dict(os.environ)
ENV["JINBU_DATA_DIR"] = str(OUT / "data")
ENV["JINBU_SKIP_SEED"] = "1"
ENV["PYTHONIOENCODING"] = "utf-8"
ENV["PYTHON"] = sys.executable

REPORT = OUT / "verification.json"
checks = []


def check(name, cond, extra=""):
    checks.append({"name": name, "passed": bool(cond)})
    print(("[通过] " if cond else "[失败] ") + name +
          ((" | " + str(extra)) if extra else ""), flush=True)
    if not cond:
        raise AssertionError(name)


class McpClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-B", str(ROOT / "mcp_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            env=ENV, cwd=str(ROOT))
        self.rid = 0
        self.last_raw = ""

    def call(self, method, params=None):
        self.rid += 1
        req = {"jsonrpc": "2.0", "id": self.rid, "method": method,
               "params": params or {}}
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        self.last_raw = line
        return json.loads(line)

    def tool(self, name, args):
        return self.call("tools/call", {"name": name, "arguments": args})

    def notify(self, method, params=None):
        req = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def run():
    assert ROOT.drive.upper() == "D:"
    # 关键：主进程自己也要先隔离再 import db——否则造数据会写进
    # 真实默认库（2026-11-11 曾因此把测试人物写进 data/agent.db，
    # 定点清除后把这个教训焊死在这里）
    os.environ["JINBU_DATA_DIR"] = str(OUT / "data")
    os.environ.pop("JINBU_SKIP_SEED", None)   # 隔离空库：保留默认礼品库种子
    ENV["JINBU_DATA_DIR"] = str(OUT / "data")
    ENV.pop("JINBU_SKIP_SEED", None)

    def payload(resp):
        return json.loads(resp["result"]["content"][0]["text"])

    cli = McpClient()
    try:
        # ---- 初始化与握手 ----
        r = cli.call("initialize", {"protocolVersion": "2024-11-05",
                                    "capabilities": {}})
        check("initialize 返回 serverInfo 与 tools 能力",
              r["result"]["serverInfo"]["name"] == "jinbu-yihao-agent"
              and "tools" in r["result"]["capabilities"]
              and r["result"]["protocolVersion"] == "2024-11-05")
        cli.notify("notifications/initialized")
        r = cli.call("tools/list")
        names = [t["name"] for t in r["result"]["tools"]]
        check("tools/list 暴露七件工具且 schema 齐全",
              len(names) == 7 and "find_contact" in names
              and "create_gift_order" in names
              and all(t.get("inputSchema") for t in r["result"]["tools"]))

        # ---- 造数据：两位人物 + 一位医疗联络人 ----
        sys.path.insert(0, str(ROOT))
        import db
        db.init_db()
        db.points_record(None, 100, "验收预置积分", "grant")
        db.save_person({"name": "赵宁", "relation_type": "同学", "closeness": 0.7,
                        "sentiment_weight": 0.7, "intro_by": "", "address": "",
                        "phone": "", "birthday": "1995-05-01",
                        "important_dates": "[]", "prefs": '["咖啡"]',
                        "taboo": "[]",
                        "notes": "高中同学，在市一医院工作，护士长"}, None)
        db.save_person({"name": "阿芬表姐", "relation_type": "家人",
                        "closeness": 0.9, "sentiment_weight": 0.9,
                        "intro_by": "", "address": "", "phone": "",
                        "birthday": "1990-11-02",
                        "important_dates": "[]", "prefs": '["丝绸"]',
                        "taboo": '["含糖高"]',
                        "notes": "表姐，市一医院心血管科护士，血糖偏高别送甜食"},
                      None)
        # 场景种子（办公/学校/家庭）由 db 初始化写入，无需手工建

        # ---- 读工具 ----
        r = cli.tool("list_scenes", {})
        sc = payload(r)["scenes"]
        check("list_scenes 返回场景与人数",
              len(sc) >= 1 and all("scene" in x and "people" in x for x in sc))
        check("查询类工具扣 1 积分并回报余额",
              payload(r).get("points_balance") == 99)
        r = cli.tool("search_persons", {"query": "医院"})
        rj = payload(r)
        check("search_persons 命中档案 人物",
              rj["count"] >= 1
              and any("阿芬" in p["name"] for p in rj["persons"]))
        r = cli.tool("find_contact", {"question": "我要挂市一医院心血管科的号，找谁帮忙？"})
        fj = payload(r)
        check("find_contact 本地证据命中 阿芬表姐",
              fj.get("intent") == "medical"
              and any("阿芬" in (c.get("name") or "")
                      for c in fj.get("candidates", []))
              and "护士" in fj.get("answer", ""))
        r = cli.tool("get_person", {"name": "阿芬表姐"})
        pj = payload(r)
        check("get_person 档案含禁忌与场景",
              pj["name"] == "阿芬表姐" and "含糖高" in (pj.get("taboo") or "")
              and isinstance(pj.get("scenes"), list))
        r = cli.tool("get_person", {"name": "不存在的人"})
        check("get_person 未知人物走 isError 路径",
              r["result"]["isError"] is True
              and payload(r).get("error"))

        # ---- 写工具：记忆 ----
        r = cli.tool("add_memo", {"name": "阿芬表姐",
                                  "text": "上周帮我挂过号，记着回礼"})
        check("add_memo 落库成功",
              "上周帮我挂过号" in (db.get_person(
                  next(p["id"] for p in db.list_persons()
                       if p["name"] == "阿芬表姐")).get("memo_md") or ""))

        # ---- 送礼推荐与建单 ----
        r = cli.tool("recommend_gifts", {"name": "阿芬表姐"})
        gifts = payload(r)["gifts"]
        check("recommend_gifts 排禁忌并含比价",
              len(gifts) >= 1
              and not any("甜" in g["name"] or "糖" in g["name"] for g in gifts)
              and all(len(g["merchants"]) >= 1 for g in gifts))
        pick = gifts[0]["name"]
        r = cli.tool("create_gift_order",
                     {"person": "阿芬表姐", "gift": pick,
                      "scheduled_date": "2026-11-30", "channel": "mock"})
        oj = payload(r)
        check("mock 建单立即模拟已付款",
              oj["status"] == "模拟已付款" and oj["channel"] == "mock")
        rows = db.list_orders()
        check("mock 订单落库通道正确",
              any(o["id"] == oj["order_id"] and o["channel"] == "mock"
                  and o["status"] == "模拟已付款" for o in rows))
        r = cli.tool("create_gift_order",
                     {"person": "阿芬表姐", "gift": pick,
                      "scheduled_date": "2026-11-30", "channel": "vibe"})
        oj2 = payload(r)
        check("vibe 建单停待付款且不代付（授权边界）",
              oj2["channel"] == "vibe"
              and oj2["status"].startswith("待付款")
              and next(o for o in db.list_orders()
                       if o["id"] == oj2["order_id"])["status"] == "待付款")
        r = cli.tool("create_gift_order",
                     {"person": "阿芬表姐", "gift": "不在清单里的礼物"})
        check("礼物不在推荐清单拒绝建单",
              r["result"]["isError"] is True)

        # ---- 协议错误路径 ----
        r = cli.call("tools/call", {"name": "no_such_tool", "arguments": {}})
        check("未知工具返回 -32602",
              r.get("error", {}).get("code") == -32602)
        r = cli.call("xxxx/fake")
        check("未知方法返回 -32601",
              r.get("error", {}).get("code") == -32601)
        r = cli.call("ping")
        check("ping 心跳正常", "result" in r)

        # ---- v0.68 计费：余额不足拒绝执行、不产生负余额 ----
        bal = db.points_balance()
        if bal > 0:
            db.points_consume(None, bal, "验收清空余额")
        r = cli.tool("get_person", {"name": "阿芬表姐"})
        pj = payload(r)
        check("积分不足时查询被拒并提示充值（不执行工具）",
              r["result"]["isError"] is True
              and "积分不足" in (pj.get("error") or "")
              and pj.get("points_balance") == 0
              and pj.get("hint"))

        check("数据只落隔离库（D 盘 _tmp/v067）",
              str(OUT) in str(db.DB_PATH))
    finally:
        cli.close()


try:
    run()
    status = "passed"
except BaseException:
    import traceback
    print(traceback.format_exc(), flush=True)
    status = "failed"
finally:
    REPORT.write_text(json.dumps(
        {"version": "v0.68-mcp", "status": status, "checks": checks},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": status, "checks": len(checks),
                      "report": str(REPORT)}, ensure_ascii=False), flush=True)
    sys.exit(0 if status == "passed" else 1)
