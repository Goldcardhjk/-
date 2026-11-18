"""祝福语定制引擎

核心目标：每人专属、避免群发尴尬。
做法：把 姓名 / 关系 / 喜好 / 上次送礼 / 共享记忆 注入到节日模板，
让每条祝福都有「只对他/她说过」的细节。

v0.17：大模型定制撰写（craft_one）——把「社交记忆 + 关系评级」
（memo_md 记忆笔记 / 近期互动 / 喜好 / 禁忌 / 重要日期 / 关系分与层级）
整包注入 prompt 交给大模型逐位撰写；未配置 API 时降级本地增强模板。
"""

import json
from db import get_person, list_interactions
from scoring import compute_score

# 节日模板。{name}{rel}{pref}{memory}{last_gift} 会被替换
HOLIDAY_TEMPLATES = {
    "春节": "【{name}】{rel}，新年好呀！想起您平时喜欢{pref}，今年特意备了份小心意。愿新的一年身体健康、诸事顺意，咱们常联系🧧",
    "生日": "【{name}】生日快乐🎂！{rel}又长一岁啦～知道您钟爱{pref}，这份礼物希望戳中您的心。愿你新的一岁被温柔包围，所求皆如愿。",
    "中秋": "【{name}】中秋安康🌕！{rel}遥寄一份团圆心意，想起您爱{pref}，正好应景。愿月圆人安，事事圆满。",
    "端午": "【{name}】端午安康🚣！{rel}一份应节小礼送达，记得您喜欢{pref}。愿夏日清宁，百事皆顺。",
    "女神节": "【{name}】女神节快乐💐！{rel}知道您偏爱{pref}，挑了份合心意的。愿你永远被宠爱、闪闪发光。",
    "教师节": "【{name}】教师节快乐📚！{rel}感念一路指引，记得您爱{pref}，略备薄礼表心意。愿桃李芬芳，康乐顺遂。",
    "合作周年": "【{name}】咱们的合作纪念日到了🤝！{rel}一路同行，承蒙关照。想起您偏好{pref}，备了份谢礼。愿携手再创佳绩。",
    "新年": "【{name}】元旦快乐✨！{rel}辞旧迎新，感念有您。记得您中意{pref}，新岁小礼已安排。愿前路皆坦途，万事尽可期。",
}

# 共享记忆补充句（有则追加，强化「只对他说过」）
MEMORY_TAIL = {
    "家人": "您永远是我最牵挂的家人。",
    "挚友": "那些年的悄悄话，我都记着呢。",
    "重要合作伙伴": "每一次关键决策，都感谢您的信任。",
    "同事": "日常搭把手的默契，很珍惜。",
    "普通朋友": "偶尔问候，也挺暖的。",
    "弱关系": "保持联系，未来或许有更多交集。",
}


def _last_gift(person_id):
    inter = list_interactions(person_id)
    for i in inter:
        if i["type"] == "送出礼物" and i["note"]:
            return i["note"]
    return ""


def generate(person_id, holiday):
    p = get_person(person_id)
    if not p:
        return "（未找到该人物）"
    tpl = HOLIDAY_TEMPLATES.get(holiday)
    if not tpl:
        return f"（暂不支持节日：{holiday}）"

    prefs = json.loads(p.get("prefs") or "[]")
    pref_text = "、".join(prefs[:3]) if prefs else "美好之物"
    last = _last_gift(person_id)
    memory = MEMORY_TAIL.get(p.get("relation_type"), "")

    text = tpl.format(
        name=p["name"],
        rel=p.get("relation_type", "朋友"),
        pref=pref_text,
        memory=memory,
        last_gift=last,
    )
    if last:
        text += f"\n（上次送过：{last}）"
    if memory:
        text += f"\n{memory}"
    return text


if __name__ == "__main__":
    from db import init_db
    init_db()
    for hid in ["春节", "生日", "女神节"]:
        print(f"===== {hid} =====")
        print(generate(2, hid))
        print()


# ---------------------------------------------------------------- v0.17 大模型定制撰写
# 关系评级 → 语气亲疏指引（core=核心/重要/普通/浅层）
TONE_BY_TIER = {
    "核心": "至亲挚友级：语气亲昵热络，像私下发消息，可轻松可玩笑",
    "重要": "重要伙伴：敬重与热络平衡，提及共同经历并表达感谢",
    "普通": "普通朋友：礼貌得体，热情适度、不越界",
    "浅层": "浅层关系：简洁礼貌，保持友好距离，不过度热情",
}


def _person_context(p):
    """收集单个人物的「社交记忆 + 关系评级」上下文（LLM/模板共用）"""
    score, _, tier = compute_score(p)
    prefs = json.loads(p.get("prefs") or "[]")
    taboo = json.loads(p.get("taboo") or "[]")
    imp = json.loads(p.get("important_dates") or "[]")
    inters = list_interactions(p["id"])
    last_gift = ""
    for i in reversed(inters):
        if i["type"] == "送出礼物" and i.get("note"):
            last_gift = i["note"]
            break
    return {
        "name": p["name"],
        "relation": p.get("relation_type") or "朋友",
        "tier": tier,
        "score": score,
        "closeness": p.get("closeness"),
        "sentiment": p.get("sentiment_weight"),
        "prefs": prefs,
        "taboo": taboo,
        "imp": imp,
        "birthday": p.get("birthday") or "",
        "intro_by": p.get("intro_by") or "",
        "notes": (p.get("notes") or "").strip(),
        "memo": (p.get("memo_md") or "").strip(),
        "recent": inters[-5:],
        "last_gift": last_gift,
    }


def _api_craft(ctx, holiday, model=None):
    """调 OpenAI 兼容接口撰写；失败抛异常由调用方降级 mock"""
    import llm
    cfg = llm.load_config()
    base = cfg.get("base_url", "").rstrip("/")
    key = cfg.get("api_key", "")
    model = (model or cfg.get("model", "")).strip()
    if not (base and key and model):
        raise RuntimeError("大模型 API 未完整配置")

    lines = [
        f"- 姓名：{ctx['name']}",
        f"- 与我的关系：{ctx['relation']}（关系评级：{ctx['tier']} · "
        f"{ctx['score']} 分）",
        f"- 亲密度 {ctx['closeness']} / 情感权重 {ctx['sentiment']}",
    ]
    if ctx["prefs"]:
        lines.append("- 喜好：" + "、".join(ctx["prefs"]))
    if ctx["taboo"]:
        lines.append("- 禁忌（绝不能提）：" + "、".join(ctx["taboo"]))
    if ctx["birthday"]:
        lines.append(f"- 生日：{ctx['birthday']}")
    if ctx["imp"]:
        lines.append("- 重要日期：" + ", ".join(
            f"{d.get('label', '')}:{d.get('date', '')}" for d in ctx["imp"]))
    if ctx["intro_by"]:
        lines.append(f"- 引荐人：{ctx['intro_by']}")
    if ctx["notes"]:
        lines.append(f"- 备注：{ctx['notes']}")
    if ctx["memo"]:
        lines.append(f"- 社交记忆笔记：\n{ctx['memo']}")
    if ctx["recent"]:
        lines.append("- 近期互动：" + "；".join(
            f"{i['date']} {i['type']}" +
            (f"（{i['note']}）" if i.get("note") else "")
            for i in ctx["recent"]))
    if ctx["last_gift"]:
        lines.append(f"- 上次送礼：{ctx['last_gift']}")

    prompt = (
        "你是「进步壹号」的节日祝福语撰写助手，为用户的一位联系人"
        "量身写一条节日祝福。\n\n"
        "人物资料：\n" + "\n".join(lines) + "\n\n"
        "撰写要求：\n"
        f"1. 节日：{holiday}\n"
        f"2. 语气指引：{TONE_BY_TIER.get(ctx['tier'], TONE_BY_TIER['普通'])}\n"
        "3. 必须自然融入至少一处只属于他的细节（社交记忆/近期互动/"
        "喜好/重要日期），让这条祝福明显是专门写给他的，而不是群发套话\n"
        "4. 严格避开禁忌内容\n"
        "5. 80-140 字，口语化、真诚具体；至多 1 个 emoji；"
        "不要用「祝您节日快乐」式的空话开头\n"
        "6. 直接输出祝福语正文，不要任何解释、引号或前缀\n")

    import urllib.request
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return content.strip().strip('"').strip("「」").strip()


def _mock_craft(ctx, holiday):
    """本地增强模板：引用社交记忆/近期互动细节，避免纯群发感"""
    tpl = HOLIDAY_TEMPLATES.get(holiday)
    if not tpl:
        return f"（暂不支持节日：{holiday}）"
    pref_text = "、".join(ctx["prefs"][:3]) if ctx["prefs"] else "美好之物"
    text = tpl.format(
        name=ctx["name"],
        rel=ctx["relation"],
        pref=pref_text,
        memory=MEMORY_TAIL.get(ctx["relation"], ""),
        last_gift=ctx["last_gift"] or "",
    )
    # 挑一条最贴身的细节缀在末尾（记忆笔记 > 近期互动 > 重要日期）
    detail = ""
    if ctx["memo"]:
        detail = ctx["memo"].strip().splitlines()[0][:36]
    elif ctx["recent"] and ctx["recent"][-1].get("note"):
        detail = ctx["recent"][-1]["note"][:36]
    elif ctx["imp"]:
        d = ctx["imp"][0]
        detail = f"{d.get('label', '特别的日子')}快到了"
    if detail:
        text += f"\n（{detail}）"
    if ctx["last_gift"] and ctx["last_gift"] != detail:
        text += f"\n（上次送过：{ctx['last_gift']}）"
    tail = MEMORY_TAIL.get(ctx["relation"], "")
    if tail:
        text += f"\n{tail}"
    return text


def craft_one(person_id, holiday, model=None):
    """为单个人物定制撰写节日祝福语。

    返回 (text, source)：source ∈ {"api", "mock"}
    - api：社交记忆 + 关系评级注入 prompt，交大模型撰写
    - mock：未配置 API 或请求失败时的本地增强模板

    v0.18：model=None 时按人物关系层级取分段祝福模型
    （model_for_tier，未配置分段则跟随全局模型）——不同关系人群
    走不同模型，即不同的社交成本。
    """
    p = get_person(person_id)
    if not p:
        return "（未找到该人物）", "mock"
    ctx = _person_context(p)
    if model is None:
        model = model_for_tier(ctx["tier"])
    import cloud_client
    if cloud_client.is_active():
        result = cloud_client.greeting(ctx, holiday, model)
        return result["text"], "cloud_" + result.get("source", "api")
    if llm_mode() == "api":
        import points
        ticket = points.begin_api_charge("greeting")
        try:
            return _api_craft(ctx, holiday, model), "api"
        except Exception:
            points.refund_api_charge(ticket)
            pass                          # 降级 mock（已退回预扣积分）
    return _mock_craft(ctx, holiday), "mock"


def llm_mode():
    """当前大模型模式（api/mock），供 UI 展示"""
    import llm
    return llm.mode()


# ---------------------------------------------------------------- v0.18 分段祝福模型
# 按关系分分段（口径与 TONE_BY_TIER / 网络图图例一致）：
#   核心 ≥80 / 重要 65-79 / 普通 45-64 / 浅层 <45
# 每个分段可指定不同的祝福「模型」——给核心圈配更强（也更贵）的模型
# 认真写，给浅层配轻量模型控制成本，即「不同关系人群不同的社交成本」。
# 配置存 llm_config.json 的 greet_tier_models 字段。
TIER_ORDER = ["核心", "重要", "普通", "浅层"]
TIER_SCORE_RANGE = {"核心": "≥80", "重要": "65-79",
                    "普通": "45-64", "浅层": "<45"}
FOLLOW_GLOBAL = "（跟随全局）"     # 分段下拉里的哨兵值 = 用全局模型


def load_tier_models():
    """读取分段模型配置 {层级: 模型名}（未配置层为空串=跟随全局）"""
    import llm
    tm = llm.load_config().get("greet_tier_models") or {}
    return {t: (tm.get(t) or "") for t in TIER_ORDER}


def save_tier_models(mapping):
    """写分段模型配置（只保留四层与非空值）"""
    import llm
    clean = {t: m for t, m in (mapping or {}).items()
             if t in TIER_ORDER and m}
    llm.save_config({"greet_tier_models": clean})
    return clean


def model_for_tier(tier):
    """该层级的祝福模型：分段配置优先，否则全局模型"""
    m = load_tier_models().get(tier) or ""
    if m:
        return m
    import llm
    return llm.load_config().get("model", "") or ""
