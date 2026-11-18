"""智能送礼引擎

- 按人物喜好标签从礼品库推荐（匹配度）
- 多商家模拟比价，取最低价作为「到手价」
- 性价比 = 匹配度 / 价格（归一化），送礼送到心坎里又不花冤枉钱
- 生成送礼订单，付款走「模拟」通道（不触发真实交易）
"""

import json
from db import list_catalog, best_price, create_order, mock_pay, get_person


def recommend(person, top_n=5):
    """返回推荐列表 [{gift, best_price, merchant_prices, match, cost_perf}]"""
    prefs = set(json.loads(person.get("prefs") or "[]"))
    taboo = set(json.loads(person.get("taboo") or "[]"))
    rel = person.get("relation_type", "")

    results = []
    for g in list_catalog():
        tags = set(json.loads(g.get("tags") or "[]"))
        suitable = set(json.loads(g.get("suitable") or "[]"))

        # 命中喜好标签数
        hit = len(prefs & tags)
        # 关系适配
        rel_hit = 1 if (rel in suitable or "所有" in suitable) else 0
        # 禁忌：命中禁忌直接排除
        if taboo & tags:
            continue

        match = hit * 2 + rel_hit  # 匹配度（加权）
        if match == 0 and not prefs:
            match = 1  # 没有任何喜好记录时也给基础推荐
        if match == 0:
            continue

        prices = best_price(g["id"])
        lowest = min(p["price"] for p in prices) if prices else g["base_price"]

        # 性价比：匹配度越高、价格越低越好（价格取倒数并缩放）
        cost_perf = round(match / (lowest / 100.0), 2)

        results.append({
            "gift": g,
            "best_price": lowest,
            "merchant_prices": prices,
            "match": match,
            "cost_perf": cost_perf,
        })

    results.sort(key=lambda x: (x["match"], x["cost_perf"]), reverse=True)
    return results[:top_n]


def schedule_and_pay(person_id, gift_id, gift_name, scheduled_date, amount, greeting_text=""):
    """建单 + 模拟付款，返回订单 id 与状态"""
    oid = create_order(person_id, gift_id, gift_name, scheduled_date, amount, greeting_text)
    mock_pay(oid)  # MVP：模拟付款。真实付款需后续接入开放平台并加二次确认
    return oid, "模拟已付款"


if __name__ == "__main__":
    from db import init_db, get_person
    init_db()
    p = get_person(2)  # 小琳
    print(f"为 {p['name']}（喜好: {p['prefs']}）推荐：")
    for r in recommend(p):
        print(f"  ★ 匹配 {r['match']}  到手价 ¥{r['best_price']}  {r['gift']['name']}  [性价比 {r['cost_perf']}]")
        for mp in r["merchant_prices"]:
            print(f"      - {mp['merchant']}: ¥{mp['price']}")
