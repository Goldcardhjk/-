# -*- coding: utf-8 -*-
"""进步壹号 Agent —— MCP Server（Model Context Protocol · stdio）

把桌面智能体的能力暴露为标准 MCP 工具，任何 MCP 客户端（Claude Desktop、
DSH、百炼/魔搭生态里的智能体…）都能在用户授权下驱动本应用：
查询关系档案、本地证据找人、写记忆、送礼推荐、建送礼订单。

设计原则（对齐官方 AI 付的授权边界要求）：
  1. 读多写少：查询类工具不上锁；写操作两类（记忆、订单）且都可追溯；
  2. **支付永远留给用户**：MCP 只能「建单」，不能代付——Vibe Pay 订单
     建完停在「待付款」，收银台在桌面应用支付窗里，由用户扫码/登录完成；
  3. 数据不出本机：直连本地 SQLite（与桌面应用同一数据目录约定
     JINBU_DATA_DIR），不联网、不上传；
  4. 零第三方依赖：标准库实现 JSON-RPC 2.0 over stdio
     （MCP 线协议 = 按行分隔的 JSON-RPC），`python mcp_server.py` 即起；
  5. 计费（v0.68）：查询/搜索类工具 1 积分/次，写操作（记忆、订单）免费，
     每个结果都带 points_balance；余额不足时明确拒绝并提示充值，
     不执行工具、不产生负余额。

用法（供 MCP Client 配置）：
    command: python
    args:    [ "<安装目录>/mcp_server.py" ]
    env:     { "JINBU_DATA_DIR": "<安装目录>/data" }   # 可选
    # Windows 示例：D:/进步壹号/进步壹号Agent/mcp_server.py
    # macOS 示例：  /Applications/进步壹号.app/Contents/Resources/mcp_server.py

手工冒烟：
    Windows:  echo {"jsonrpc":"2.0","id":1,"method":"initialize","params":{}} ^
                  | python mcp_server.py
    macOS:    echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
                  | python3 mcp_server.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import db                      # noqa: E402
import llm                     # noqa: E402
import gifts                   # noqa: E402

SERVER_INFO = {"name": "jinbu-yihao-agent", "version": "0.68.0"}
PROTOCOL_VERSION = "2024-11-05"

# 查询/搜索类工具按次计费（v0.68 积分域）；写操作免费，支付永远由用户完成。
QUERY_TOOLS = {"list_scenes", "search_persons", "find_contact",
               "get_person", "recommend_gifts"}
QUERY_COST = 1


# ---------------------------------------------------------------- 工具实现
def _person_brief(p):
    score = int(round(float(p.get("closeness") or 0) * 100))
    return {
        "name": p.get("name"), "relation_type": p.get("relation_type"),
        "score": score, "scenes": p.get("scenes") or [],
        "intro_by": p.get("intro_by") or "",
        "note": (p.get("notes") or "").split("｜")[0][:80],
    }


def tool_list_scenes(params):
    scenes = db.list_scenes() if hasattr(db, "list_scenes") else []
    return {"scenes": [
        {"scene": s,
         "people": db.scene_people_count(s)
         if hasattr(db, "scene_people_count") else None}
        for s in scenes]}


def tool_search_persons(params):
    scene = (params or {}).get("scene")
    persons = db.list_persons()
    if scene:
        persons = [p for p in persons if scene in (p.get("scenes") or [])]
    key = (params or {}).get("query") or ""
    if key:
        persons = [p for p in persons
                   if key in (p.get("name") or "")
                   or key in (p.get("relation_type") or "")
                   or key in (p.get("notes") or "")]
    return {"count": len(persons),
            "persons": [_person_brief(p) for p in persons[:50]]}


def tool_find_contact(params):
    """本地证据找人：身边谁最该为这个问题出手（可追问依据）。"""
    q = (params or {}).get("question") or ""
    rec = llm.recommend_contacts(q, db.list_persons(), db.list_links(),
                                 db.list_interactions())
    return {"question": q, "intent": rec.get("intent"),
            "found": rec.get("found"),
            "candidates": [
                {"name": c["person"].get("name"),
                 "evidence": c.get("hits", []), "circles": c.get("circles", []),
                 "score": int(round(float(c["person"].get("closeness") or 0) * 100))}
                for c in rec.get("candidates", [])[:5]],
            "answer": rec.get("answer", "")}


def tool_get_person(params):
    name = (params or {}).get("name") or ""
    p = next((x for x in db.list_persons() if x["name"] == name), None)
    if not p:
        return {"error": "未找到人物：%s" % name}
    d = _person_brief(p)
    d.update({"birthday": p.get("birthday"),
              "important_dates": p.get("important_dates"),
              "prefs": p.get("prefs"), "taboo": p.get("taboo"),
              "memo": (p.get("memo_md") or "")[:600],
              "relations": [
                  {"other": (r.get("name_b") if r.get("person_a") == p["id"]
                            else r.get("name_a")),
                   "relation": db.normalize_link_relation(r.get("relation", "")),
                   "score": r.get("score")}
                  for r in db.list_links()
                  if p["id"] in (r.get("person_a"), r.get("person_b"))][:12]})
    return d


def tool_add_memo(params):
    name = (params or {}).get("name") or ""
    text = (params or {}).get("text") or ""
    p = next((x for x in db.list_persons() if x["name"] == name), None)
    if not p:
        return {"error": "未找到人物：%s" % name}
    if not text.strip():
        return {"error": "备注内容为空"}
    old = p.get("memo_md") or ""
    db.set_memo(p["id"], (old + "\n- " + text.strip()).strip())
    return {"ok": True, "person": name, "recorded": text.strip()}


def tool_recommend_gifts(params):
    name = (params or {}).get("name") or ""
    p = next((x for x in db.list_persons() if x["name"] == name), None)
    if not p:
        return {"error": "未找到人物：%s" % name}
    reco = gifts.recommend(p)
    return {"person": name,
            "gifts": [
                {"name": r["gift"]["name"], "category": r["gift"]["category"],
                 "best_price": r["best_price"], "match": r["match"],
                 "merchants": [{"merchant": mp["merchant"], "price": mp["price"]}
                               for mp in r["merchant_prices"]]}
                for r in reco[:5]]}


def tool_create_gift_order(params):
    """建送礼订单（**不会代付**）。

    channel="mock"：本地模拟通道，立即标记「模拟已付款」——不产生真实交易；
    channel="vibe"：Vibe Pay 真实通道，订单停在「待付款」，收银台在
    桌面应用里点「一键付款」由用户本人完成（授权边界：支付必须由用户执行）。
    """
    params = params or {}
    name = params.get("person") or ""
    gift_name = params.get("gift") or ""
    sdate = params.get("scheduled_date") or "未排期"
    channel = params.get("channel") or "mock"
    p = next((x for x in db.list_persons() if x["name"] == name), None)
    if not p:
        return {"error": "未找到人物：%s" % name}
    g = next((x for x in gifts.recommend(p)
              if x["gift"]["name"] == gift_name), None)
    if g is None:
        return {"error": "礼物不在推荐清单内（先调 recommend_gifts）：%s"
                % gift_name}
    oid = db.create_order(p["id"], g["gift"]["id"], g["gift"]["name"],
                          sdate, g["best_price"], "", channel=channel)
    if channel == "mock":
        db.mock_pay(oid)
        status = "模拟已付款"
    else:
        db.update_order_status(oid, "待付款")
        status = "待付款（请到桌面应用完成支付）"
    return {"order_id": oid, "person": name, "gift": g["gift"]["name"],
            "amount": g["best_price"], "channel": channel, "status": status}


TOOLS = [
    {"name": "list_scenes",
     "description": "列出关系网络的使用场景卡（办公/学校/家庭/自定义）与各场景人数。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "search_persons",
     "description": "按场景/关键词搜索人物卡（姓名、关系类型、备注全文匹配）。",
     "inputSchema": {"type": "object", "properties": {
         "scene": {"type": "string", "description": "可选：场景名，如 办公"},
         "query": {"type": "string", "description": "可选：关键词"}}}},
    {"name": "find_contact",
     "description": ("本地证据找人：用自然语言问「这件事该找谁」，"
                     "返回候选人、依据与关系评分。例：我要挂市一医院心血管科的号。"),
     "inputSchema": {"type": "object", "required": ["question"],
                     "properties": {"question": {"type": "string"}}}},
    {"name": "get_person",
     "description": "查看某人档案：关系/分数/场景/喜好/禁忌/纪念日/记忆笔记/事实关系。",
     "inputSchema": {"type": "object", "required": ["name"],
                     "properties": {"name": {"type": "string"}}}},
    {"name": "add_memo",
     "description": "给某人物追加一条记忆笔记（社交细节）。写入前先 get_person 核对身份。",
     "inputSchema": {"type": "object", "required": ["name", "text"],
                     "properties": {"name": {"type": "string"},
                                    "text": {"type": "string"}}}},
    {"name": "recommend_gifts",
     "description": "为某人智能送礼推荐：按喜好匹配+禁忌排除+多商家比价。",
     "inputSchema": {"type": "object", "required": ["name"],
                     "properties": {"name": {"type": "string"}}}},
    {"name": "create_gift_order",
     "description": ("为某人建送礼订单。mock=模拟通道立即标记；vibe=Vibe Pay 真实"
                     "通道但只建单，支付由用户在桌面应用支付窗本人完成（不代付）。"),
     "inputSchema": {"type": "object",
                     "required": ["person", "gift"],
                     "properties": {
                         "person": {"type": "string"},
                         "gift": {"type": "string"},
                         "scheduled_date": {"type": "string"},
                         "channel": {"type": "string", "enum": ["mock", "vibe"]}}}},
]


# 查询类工具在描述里明示成本，智能体可在调用前自行判断是否划算。
for _tool in TOOLS:
    if _tool["name"] in QUERY_TOOLS:
        _tool["description"] += "（每次调用消耗 %d 积分）" % QUERY_COST


# ---------------------------------------------------------------- 积分计费（v0.68）
def charge_and_run(name, fn, args):
    """查询类先原子扣分再执行；写操作免费，但结果统一带 points_balance。"""
    if name in QUERY_TOOLS:
        try:
            db.points_consume(None, QUERY_COST, "MCP 查询：%s" % name)
        except ValueError as ex:
            return {"error": str(ex),
                    "hint": "请在桌面应用「更多 → 积分充值」充值后重试",
                    "points_balance": db.points_balance()}
    out = fn(args)
    if isinstance(out, dict):
        out = dict(out)
        out.setdefault("points_balance", db.points_balance())
    return out


# ---------------------------------------------------------------- JSON-RPC 分发
def handle(req):
    method = req.get("method", "")
    rid = req.get("id")
    params = req.get("params") or {}
    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": params.get("protocolVersion")
                               or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        fn = {t["name"]: f for t, f in zip(
            TOOLS, (tool_list_scenes, tool_search_persons, tool_find_contact,
                    tool_get_person, tool_add_memo, tool_recommend_gifts,
                    tool_create_gift_order))}.get(name)
        if fn is None:
            return {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32602, "message": "未知工具：%s" % name}}
        try:
            out = charge_and_run(name, fn, params.get("arguments") or {})
            text = json.dumps(out, ensure_ascii=False)
            isError = isinstance(out, dict) and "error" in out
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}],
                "isError": bool(isError)}}
        except Exception as ex:                      # noqa: BLE001
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps({"error": str(ex)},
                                                ensure_ascii=False)}],
                "isError": True}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": "未实现的方法：%s" % method}}
    return None


def main():
    db.init_db()
    for line in sys.stdin:                          # MCP stdio = 按行 JSON-RPC
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        try:
            resp = handle(req)
        except Exception:                            # noqa: BLE001
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "error": {
                "code": -32603, "message": "internal error"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
