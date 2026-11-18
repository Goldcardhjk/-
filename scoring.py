"""社交价值评估引擎

说明：这里的「社交价值」不是给人物贴冷冰冰的价签，而是计算
「该投入多少精力去维护这段关系」的优先级分（0-100），
帮助你在有限精力下把礼送到最需要的人心里。

五个维度（权重合计 1.0）：
  亲密度 0.30   关系类型 + 用户设定亲密值
  情感权重 0.20 你对这段关系的主观看重程度
  互惠度 0.20   历史送礼/收礼往来是否平衡
  紧迫度 0.20   下一个重要日期（生日/纪念日）还有多少天
  网络中心度 0.10 是否由关键人引荐 / 处于关系网枢纽
"""

from datetime import datetime, date
import json
from db import list_interactions, list_persons

RELATION_WEIGHT = {
    "家人": 1.00,
    "挚友": 0.90,
    "重要合作伙伴": 0.85,
    "同事": 0.60,
    "普通朋友": 0.50,
    "弱关系": 0.30,
}


def _parse_dates(birthday, important_dates_json):
    """返回今年/明年所有重要日期 (date 对象)"""
    out = []
    if birthday:
        try:
            out.append(datetime.strptime(birthday, "%Y-%m-%d").date().replace(year=datetime.now().year))
        except Exception:
            pass
    try:
        for d in json.loads(important_dates_json or "[]"):
            ds = d.get("date")
            if ds:
                try:
                    out.append(datetime.strptime(ds, "%Y-%m-%d").date())
                except Exception:
                    pass
    except Exception:
        pass
    return out


def days_to_next_event(birthday, important_dates_json):
    today = date.today()
    dates = _parse_dates(birthday, important_dates_json)
    if not dates:
        return None
    # 取未来最近的；若都过去则取明年的（用年份+1重算生日）
    best = None
    for d in dates:
        # 转成今年或明年的有效日期
        cand = d
        if cand < today:
            try:
                cand = d.replace(year=d.year + 1)
            except Exception:
                cand = d
        gap = (cand - today).days
        if best is None or gap < best:
            best = gap
    return best


def score_context():
    """v0.21：批量预取 compute_score 的查询上下文（3 次连接搞定全量）

    旧 compute_score 对每人执行 list_interactions(pid) + 全表
    list_persons()（判断引荐枢纽）——N 人 = 2N 次连接 + N 次全表扫描，
    O(n²) 的 DB 负载是人多卡顿的头号元凶。刷新前调一次本函数，
    把 ctx 传给 compute_score(person, ctx) 即可。
    """
    persons = list_persons()
    inter = {}
    for it in list_interactions():
        inter.setdefault(it.get("person_id"), []).append(it)
    # 引荐枢纽：name -> 「引荐人是此人」的人数（即此人引荐了多少人）
    intro_count = {}
    for p in persons:
        ib = (p.get("intro_by") or "").strip()
        if ib:
            intro_count[ib] = intro_count.get(ib, 0) + 1
    return {"inter": inter, "intro_count": intro_count}


def compute_score(person, ctx=None):
    """返回 (总分, 各维度明细 dict, 分层标签)

    ctx：score_context() 的预取上下文（批量刷新时传入，避免逐人查库）；
    不传则按旧路径逐人查询（单点调用如资料卡预览）。
    """
    rel = RELATION_WEIGHT.get(person.get("relation_type"), 0.5)
    sentiment = float(person.get("sentiment_weight") or 0.5)

    # 互惠度
    if ctx is not None:
        inter = ctx["inter"].get(person["id"], [])
    else:
        inter = list_interactions(person["id"])
    given = sum(1 for i in inter if i["type"] == "送出礼物")
    received = sum(1 for i in inter if i["type"] == "收到礼物")
    reciprocity = min(1.0, (given + received) / 4.0)

    # 紧迫度
    d = days_to_next_event(person.get("birthday"), person.get("important_dates"))
    if d is None:
        urgency = 0.20
    elif d <= 7:
        urgency = 1.0
    elif d <= 30:
        urgency = 0.70
    elif d <= 90:
        urgency = 0.40
    else:
        urgency = 0.20

    # 网络中心度
    centrality = 0.5
    if person.get("intro_by"):
        centrality += 0.3
    # 若被多人引荐（自己是枢纽）再加分
    if ctx is not None:
        hub = ctx["intro_count"].get(person.get("name") or "", 0) >= 1
    else:
        others = [p for p in list_persons() if p["id"] != person["id"]]
        hub = sum(1 for p in others
                  if p.get("intro_by") == person.get("name")) >= 1
    if hub:
        centrality += 0.2
    centrality = min(1.0, centrality)

    score = 100 * (0.30 * rel + 0.20 * sentiment + 0.20 * reciprocity + 0.20 * urgency + 0.10 * centrality)
    score = round(score)

    # 关系保底：家人/挚友/重要合作伙伴是「天然重要」关系，
    # 即使当前无临近节日、往来少，也不应掉到普通/浅层以下
    floor = {"家人": 65, "挚友": 65, "重要合作伙伴": 60}.get(person.get("relation_type"))
    if floor and score < floor:
        score = floor

    detail = {
        "亲密度": round(rel, 2),
        "情感权重": round(sentiment, 2),
        "互惠度": round(reciprocity, 2),
        "紧迫度": round(urgency, 2),
        "网络中心度": round(centrality, 2),
    }
    return score, detail, tier(score)


def tier(score):
    if score >= 80:
        return "核心"
    if score >= 65:
        return "重要"
    if score >= 45:
        return "普通"
    return "浅层"


def rank_all():
    """返回按分数降序的人物列表，附带分数/分层/距下次节日天数"""
    ctx = score_context()          # v0.21：批量预取，逐人零查库
    rows = []
    for p in list_persons():
        s, detail, t = compute_score(p, ctx)
        d = days_to_next_event(p.get("birthday"), p.get("important_dates"))
        rows.append({
            "id": p["id"], "name": p["name"], "relation_type": p["relation_type"],
            "score": s, "tier": t, "detail": detail, "days": d,
        })
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows


if __name__ == "__main__":
    from db import init_db
    init_db()
    for r in rank_all():
        print(f"{r['score']:>3} [{r['tier']}] {r['name']} ({r['relation_type']}) 距下次节日 {r['days']} 天")
