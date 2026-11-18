"""进步壹号 Agent —— 大模型关系推断

由 agent 连接的大模型判断（v0.5 交互原理核心之一）：
  1. 人物之间的关系 + 关系分数（0-100）
  2. 每个人物与"我"的关系分数（转化为亲密度/情感权重入库）

两种模式：
  - mock（默认）：确定性启发式，离线可用、结果可复现——先保证功能闭环
  - api：在 llm_config.json 配置 OpenAI 兼容接口（腾讯混元/火山方舟/
    DeepSeek 等均可），请求失败自动降级 mock 并提示

配置文件 llm_config.json（与 db 数据目录同级）：
  {"provider": "hunyuan",
   "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
   "api_key": "sk-...", "model": "hunyuan-lite"}

v0.11.1：内置免费预设——腾讯混元 Hunyuan-Lite 永久免费（每日 1 万
token，学生认证 5 万/天），首次开通另送 100 万 token 资源包（1 年）。
申请：腾讯云控制台 → 混元大模型 → API 密钥管理（CAM）。
"""

import json
import os
import re
import urllib.request
from collections import deque

import db

CONFIG_PATH = os.path.join(db.BASE, "llm_config.json")

# 关系类型 → 与我的关系分基线（mock 用）
REL_BASE = {"家人": 92, "挚友": 85, "重要合作伙伴": 78,
            "同事": 62, "普通朋友": 50, "弱关系": 35}

# ---------------- OpenAI 兼容服务预设（v0.11.1） ----------------
# 前端配置窗按预设一键填充；base_url/model 均可在界面上改。
PRESETS = {
    "hunyuan": {
        "label": "腾讯混元（Lite 永久免费）",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "models": ["hunyuan-lite", "hunyuan-turbos-latest",
                   "hunyuan-t1", "hunyuan-standard"],
        "default_model": "hunyuan-lite",
        "help": ("免费额度：Hunyuan-Lite 永久免费（每日 1 万 token，"
                 "学生认证 5 万/天）；首次开通送 100 万 token 资源包"
                 "（1 年有效）。\n申请：腾讯云控制台 → 混元大模型 → "
                 "API 密钥管理，创建 SecretId/SecretKey 后取 API Key。"),
    },
    "volces": {
        "label": "火山方舟（豆包）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-pro-32k", "doubao-lite-4k",
                   "doubao-1.5-lite-32k"],
        "default_model": "doubao-pro-32k",
        "help": "火山引擎方舟控制台创建 API Key；按量计费，新用户有赠送额度。",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
        "help": "DeepSeek 开放平台创建 API Key；充值制，价格较低。",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "models": [],
        "default_model": "",
        "help": "任何 OpenAI 兼容接口（/chat/completions + Bearer Key）。",
    },
}


def load_config():
    """读取大模型配置；服务端可用环境变量覆盖，避免把密钥写进桌面文件。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    for key, env_name in (("base_url", "JINBU_MODEL_BASE_URL"),
                          ("api_key", "JINBU_MODEL_API_KEY"),
                          ("model", "JINBU_MODEL_NAME")):
        if os.environ.get(env_name):
            cfg[key] = os.environ[env_name]
    return cfg


def save_config(cfg):
    """写 llm_config.json（自动剔除空值，保留用户已填字段）"""
    old = load_config()
    old.update({k: v for k, v in cfg.items() if v not in (None, "")})
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False, indent=2)
    return old


def model_choices():
    """UI 模型下拉候选：全部预设模型 + 当前配置里的模型（去重保序）"""
    out = []
    for p in PRESETS.values():
        for m in p["models"]:
            if m not in out:
                out.append(m)
    cur = load_config().get("model")
    if cur and cur not in out:
        out.insert(0, cur)
    return out


# ---------------- 模型社交成本分档（v0.18 祝福语分段选择用） ----------------
# 依据各服务商公开定价量级粗分：轻量/lite = 低，标准/pro = 中，
# 长思考/旗舰 = 高。仅作 UI 成本提示，不影响调用本身。
MODEL_COST = {
    "hunyuan-lite": 1, "hunyuan-turbos-latest": 2, "hunyuan-t1": 3,
    "hunyuan-standard": 2,
    "doubao-pro-32k": 2, "doubao-lite-4k": 1, "doubao-1.5-lite-32k": 1,
    "deepseek-chat": 1, "deepseek-reasoner": 2,
}
COST_LABEL = {1: "低成本", 2: "中成本", 3: "高成本"}


def model_cost(model):
    """模型社交成本档位 1-3（空/未知按中档 2 计；未知型号按名字启发式）"""
    if not model:
        return 0
    if model in MODEL_COST:
        return MODEL_COST[model]
    n = model.lower()
    if any(k in n for k in ("lite", "mini", "flash", "small")):
        return 1
    if any(k in n for k in ("t1", "reason", "r1", "max", "ultra")):
        return 3
    return 2


def model_cost_label(model):
    return COST_LABEL.get(model_cost(model), "—")


def test_connection(base_url=None, api_key=None, model=None, timeout=20):
    """发一条最小请求验证配置。返回 (ok, 消息)"""
    cfg = load_config()
    base = (base_url or cfg.get("base_url", "")).rstrip("/")
    key = api_key if api_key is not None else cfg.get("api_key", "")
    mdl = model or cfg.get("model", "")
    if not (base and key and mdl):
        return False, "base_url / api_key / model 未填完整"
    body = json.dumps({
        "model": mdl,
        "messages": [{"role": "user", "content": "回复：连接成功"}],
        "max_tokens": 16,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        txt = (data.get("choices") or [{}])[0].get(
            "message", {}).get("content", "")
        return True, "连接成功，模型回复：%s" % (txt.strip()[:40] or "（空）")
    except urllib.request.HTTPError as ex:
        try:
            detail = ex.read().decode("utf-8", "ignore")[:200]
        except Exception:
            detail = ""
        return False, "HTTP %s %s" % (ex.code, detail)
    except Exception as ex:
        return False, "请求失败：%s" % ex


def mode():
    """当前模式：api（已配置密钥）/ mock（本地模拟）"""
    cfg = load_config()
    return "api" if cfg.get("api_key") else "mock"


def mode_label():
    return "🤖 大模型：API 实连" if mode() == "api" else "🤖 大模型：本地模拟（未配置 API）"


# 备注末尾保存明确亲属资料，不按姓名、年龄或「同为家人」猜关系。
KINSHIP_MARK = "｜亲属元数据:"
FAMILY_RELATIONS = {"家人", "母子", "母女", "父子", "父女", "姑侄", "叔侄",
                    "伯侄", "舅甥", "姨甥", "祖孙", "兄弟", "姐妹", "姐弟", "兄妹", "夫妻"}


def family_metadata(person):
    text = person.get("notes") or person.get("note") or ""
    if KINSHIP_MARK not in text:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(text.rsplit(KINSHIP_MARK, 1)[1])
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


CIRCLE_MARK = "｜圈子元数据:"
CIRCLE_RELATIONS = {"家人", "同事", "同学", "合作伙伴"}


def relationship_circles(persons, links):
    """明确家族和圈子优先；亲属按真实边连通，不按身份猜两人关系。"""
    by_id = {p["id"]: p for p in persons}
    groups = {}
    for p in persons:
        family_name = family_metadata(p).get("family")
        if isinstance(family_name, str) and family_name.strip():
            groups.setdefault(("家人", family_name.strip()), set()).add(p["id"])
        text = p.get("notes") or ""
        if CIRCLE_MARK not in text:
            continue
        try:
            values, _ = json.JSONDecoder().raw_decode(text.rsplit(CIRCLE_MARK, 1)[1])
        except (ValueError, TypeError):
            continue
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            kind, name = db.normalize_link_relation(value.get("kind")), value.get("name")
            if kind not in CIRCLE_RELATIONS or not isinstance(name, str) or not name.strip():
                continue
            key = (kind, name.strip())
            groups.setdefault(key, set()).add(p["id"])
    circles = [{"kind": k, "name": n, "members": frozenset(m), "explicit": True}
               for (k, n), m in sorted(groups.items(), key=lambda g: (g[0][0] != "家人", g[0]))
               if len(m) >= 2]
    family = {}
    for link in links:
        a, b = link["person_a"], link["person_b"]
        if a == b or a not in by_id or b not in by_id:
            continue
        if link.get("relation") in FAMILY_RELATIONS:
            family.setdefault(a, set()).add(b)
            family.setdefault(b, set()).add(a)
    seen = set()
    for root in sorted(family):
        if root in seen:
            continue
        members, todo = set(), [root]
        while todo:
            p = todo.pop()
            if p in members:
                continue
            members.add(p)
            todo.extend(family[p] - members)
        seen.update(members)
        if not any(c["kind"] == "家人" and members <= c["members"] for c in circles):
            name = " / ".join(by_id[p]["name"] for p in sorted(members)[:2]) + "亲属圈"
            circles.append({"kind": "家人", "name": name, "members": frozenset(members)})
    for link in links:
        kind = db.normalize_link_relation(link.get("relation"))
        pair = frozenset((link["person_a"], link["person_b"]))
        if (kind not in CIRCLE_RELATIONS or len(pair) != 2 or not pair <= by_id.keys()
                or any(c["kind"] == kind and pair <= c["members"] for c in circles)):
            continue
        name = " / ".join(by_id[p]["name"] for p in sorted(pair))
        circles.append({"kind": kind, "name": name, "members": pair})
    for i, circle in enumerate(circles, 1):
        circle["label"] = f"{i:02d} {circle['kind']} · {circle['name']}"
    return circles


def circle_internal_link(link, circles):
    """只省略已有同圈泛化边；具体亲属、上下级及跨圈边原样保留。"""
    kind = db.normalize_link_relation(link.get("relation"))
    pair = {link["person_a"], link["person_b"]}
    return kind in CIRCLE_RELATIONS and any(
        c.get("replace_generic", True) and c["kind"] == kind
        and pair <= c["members"] for c in circles)


def person_circles(person):
    """读取人物所属圈子；兼容旧数据中写入 notes 的圈子元数据。"""
    text = person.get("notes") or person.get("note") or ""
    if CIRCLE_MARK not in text:
        return []
    try:
        values, _ = json.JSONDecoder().raw_decode(text.rsplit(CIRCLE_MARK, 1)[1])
    except (ValueError, TypeError):
        return []
    return [v for v in values if isinstance(v, dict) and v.get("kind") and v.get("name")]


def relationship_paths(persons, links, source_id, target_ids, max_hops=3):
    """在原始事实边上找最短无向路径；不使用画布隐藏/聚合后的边。"""
    target_ids = set(target_ids)
    if source_id in target_ids:
        return [[source_id]]
    adjacency = {}
    for link in links:
        a, b = link.get("person_a"), link.get("person_b")
        if a == b or a is None or b is None:
            continue
        adjacency.setdefault(a, []).append((b, link))
        adjacency.setdefault(b, []).append((a, link))
    queue = deque([(source_id, [source_id], [])])
    seen = {source_id}
    while queue:
        node, path, edges = queue.popleft()
        if len(edges) >= max_hops:
            continue
        for nxt, link in adjacency.get(node, []):
            if nxt in seen:
                continue
            next_path, next_edges = path + [nxt], edges + [link]
            if nxt in target_ids:
                return [{"person_ids": next_path, "links": next_edges}]
            seen.add(nxt)
            queue.append((nxt, next_path, next_edges))
    return []


def _evidence_text(person, hits, limit=24):
    """从档案原文截出命中词所在的那句，作为可核对依据。

    只回词表词（「医院, 护士」）既看不出是哪个科，也看不出是不是真在那家
    机构；截原文能一眼核对，也不额外编造任何事实。
    """
    blob = "  ".join(str(person.get(k) or "").strip()
                     for k in ("notes", "memo_md")).strip()
    if not blob:
        return ""
    for seg in re.split(r"[。；;\n]", blob):
        seg = seg.strip(" ，,、")
        if seg and any(h.lower() in seg.lower() for h in hits):
            return seg if len(seg) <= limit else seg[:limit - 1] + "…"
    return ""


def recommend_contacts(question, persons, links, interactions=()):
    """依据可追溯资料推荐联系人，不把亲密度或圈子成员资格当专业能力。

    返回 evidence、candidates 和 answer，供聊天层和 API prompt 共用。
    对医疗/挂号问题，只有资料明确出现医疗、医院、医生、心内等词才命中。
    """
    q = str(question or "")
    medical = bool(re.search(
        r"医疗|医院|医生|医师|专家号|挂号|号源|心脏|心内|心血管|心脑血管|心外|"
        r"看病|就医|诊所|门诊|急诊|住院|手术|体检|科室|病房|护士", q))
    groups = {
        # v0.59：补「心血管 / 心外 / 门诊 / 科室」等挂号常用说法。原先只认
        # 「医院 / 医生 / 心脏」，档案写「市一医院心血管科护士」时只命中
        # 「医院」，看不出是哪个科；若档案只写「心血管科医生」则整条命中不到。
        # 这里只收「职业 / 机构」类强信号：体检、住院、挂号、号源、手术是
        # 场景词——小姨备注「每年体检都提醒一遍」曾被当成医疗资源排进候选。
        "医疗": ("医院", "医生", "医师", "医疗", "心内", "心脏", "心血管",
                 "心脑血管", "心外", "诊所", "护士", "门诊", "急诊", "科室",
                 "病房", "药房", "卫生院", "卫生所"),
        "教育": ("老师", "教授", "学校", "课程", "升学", "教育"),
        "法律": ("律师", "法院", "法律", "合同", "诉讼"),
        "招聘": ("招聘", "内推", "HR", "人事", "工作"),
    }
    domain_words = next((words for key, words in groups.items() if key == "医疗" and medical), ())
    if not domain_words:
        for key, words in groups.items():
            if any(word in q for word in words):
                domain_words = words
                break
    by_id = {p.get("id"): p for p in persons}
    edge_by_person = {}
    for link in links:
        edge_by_person.setdefault(link.get("person_a"), []).append(link)
        edge_by_person.setdefault(link.get("person_b"), []).append(link)
    interaction_count = {}
    for row in interactions or ():
        interaction_count[row.get("person_id")] = interaction_count.get(row.get("person_id"), 0) + 1
    scored = []
    for person in persons:
        pid = person.get("id")
        circles = person_circles(person)
        profile_text = " ".join(str(person.get(k) or "") for k in (
            "name", "notes", "memo_md"))
        profile_text += " " + " ".join(str(c.get("name")) for c in circles)
        person_edges = edge_by_person.get(pid, [])
        relation_text = " ".join(str(e.get("relation") or "") + " " + str(e.get("note") or "") for e in person_edges)
        profile_hits = [word for word in domain_words if word.lower() in profile_text.lower()] if domain_words else []
        relation_hits = [word for word in domain_words if word.lower() in relation_text.lower()] if domain_words else []
        hits = sorted(set(profile_hits + relation_hits))
        if not hits:
            continue
        direct = bool(profile_hits)
        if medical and not direct:
            continue
        relation_strength = float(person.get("closeness") or 0.0)
        score = len(set(profile_hits)) * 30 + len(set(relation_hits)) * 12 + relation_strength * 10 + min(5, interaction_count.get(pid, 0))
        path = [pid]
        path_text = "直接联系人"
        intro_name = (person.get("intro_by") or "").strip()
        if intro_name:
            intro = next((p for p in persons if p.get("name") == intro_name), None)
            if intro:
                path = [intro.get("id"), pid]
                path_text = f"通过 {intro_name} 引荐"
        scored.append({"person": person, "score": score, "hits": hits,
                       "evidence": _evidence_text(person, hits),
                       "profile_hits": sorted(set(profile_hits)),
                       "relation_hits": sorted(set(relation_hits)),
                       "circles": circles, "path": path, "path_text": path_text,
                       "direct": direct})
    scored.sort(key=lambda x: (-x["score"], str(x["person"].get("name"))))
    if scored:
        candidates = scored[:5]
        for item in candidates:
            item["level"] = relation_level(item["person"], item.get("circles"))
            # 候选里保留「对某某：」前缀（接口/外部调用要知道答谢对象）；
            # 答复正文单独用无前缀短句，避免同一段里重复人名。
            item["thanks"] = thanks_for(item["level"], item["person"], heavy=medical)
        # v0.59：演示要看得到「找谁」这一句。原先「根据现有资料，优先找：」
        # 加每条候选都重复姓名，答复 9 行、右侧助手栏一屏放不下，观众只看到
        # 答谢方案。单人时姓名只说一次，依据直接用档案原文。
        names = [c["person"].get("name", "") for c in candidates[:3]]
        if len(names) == 1:
            answer = f"优先找：{names[0]}。"
            answer += "\n命中依据：" + (
                candidates[0]["evidence"] or "、".join(candidates[0]["profile_hits"])) + \
                f"（{candidates[0]['path_text']}）。"
        else:
            answer = "优先找：" + "、".join(names) + "。"
            answer += "\n命中依据：" + "；".join(
                f"{c['person'].get('name')}（{c['evidence'] or ', '.join(c['profile_hits'])}；"
                f"{c['path_text']}）" for c in candidates[:3]) + "。"
        first = candidates[0]
        answer += (f"\n关系等级：{first['level']}"
                   f"（关系分 {round(float(first['person'].get('closeness') or 0) * 100)}，"
                   f"{first['path_text']}）。")
        # v0.59：护士不是号源。被问「要一个号」时先把岗位说清，
        # 免得观众以为程序把护士当成了门诊医生。仅当档案写护士、没写医生时提示。
        ev0 = str(first.get("evidence") or "")
        if medical and "护士" in ev0 and "医生" not in ev0:
            answer += (f"\n岗位提示：{first['person'].get('name')}是护士，不是门诊医生；"
                       "请她帮忙问科室怎么约、找哪位医生，别当直接号源。")
        answer += "\n答谢建议：" + thanks_for(first["level"], heavy=medical)
        if len(candidates) > 1:
            answer += "\n备选：" + "、".join(
                f"{c['person'].get('name')}（{c['level']}；{c['thanks']}）" for c in candidates[1:3])
        answer += "\n先核实对方所在机构与具体流程。"
        return {"intent": "medical" if medical else "domain", "found": True,
                "candidates": candidates, "answer": answer}
    return {"intent": "medical" if medical else "domain", "found": False,
            "candidates": [],
            "answer": ("当前人脉资料中没有找到明确的医疗/医院/医生线索，不能据此指定某个人挂号。"
                       "建议先补充联系人职业、所在医院或专长，再让我按关系路径推荐；如果出现胸痛、呼吸困难、出冷汗等急症，应立即联系急救或就近急诊。"
                       if medical else "当前资料中没有找到足够明确的专业线索，建议补充联系人职业、机构或备注。")}


# ---------------------------------------------------------------- 本地指令解析
# AI 助手原先只有调模型一条路：没配 API 时只会聊天不会做动作。
# 这里加一层本地意图解析——能直接答（电话/生日/地址/备注/待办）或直接做
# （调分/加备注/补关系）的，不调模型，离线也能用。
def _nearest_event(person):
    """算出距今天最近的生日/纪念日，返回 (天数, 标签)。逻辑与右侧栏「近期提醒」一致。"""
    from datetime import date as _d, datetime as _dt
    today = _d.today()
    best = None
    if person.get("birthday"):
        try:
            bd = _dt.strptime(person["birthday"], "%Y-%m-%d").date().replace(year=today.year)
            if bd < today:
                bd = bd.replace(year=bd.year + 1)
            best = ((bd - today).days, "生日")
        except Exception:
            pass
    try:
        for im in json.loads(person.get("important_dates") or "[]"):
            ds = (im.get("date") or "").strip()
            if not ds:
                continue
            try:
                imd = _dt.strptime(ds, "%Y-%m-%d").date()
            except Exception:
                continue
            if imd < today:
                continue
            dd = (imd - today).days
            if best is None or dd < best[0]:
                best = (dd, im.get("label") or "纪念日")
    except Exception:
        pass
    return best


# 本地建立连接时可识别的关系词后缀（v0.61）。「X和Y是大学同学」这类话
# 只有结尾落在关系名词上才建边，避免把「X和Y是谁」这类问句误判成指令。
REL_NOUNS = (
    "同学", "同事", "朋友", "家人", "亲戚", "亲属", "挚友", "闺蜜", "兄弟",
    "姐妹", "发小", "战友", "室友", "邻居", "老乡", "伙伴", "搭档", "客户",
    "网友", "师生", "夫妻", "恋人", "对象", "合作伙伴", "合作", "父子",
    "母子", "父女", "母女", "叔侄", "姨甥", "舅甥", "姑侄", "表亲", "堂亲",
    "引荐", "认识",
)


def _distinct_names_in(q, persons):
    """按出现顺序列出问句里提到的已建档人物（长名优先，去重叠）。"""
    found = []
    for p in sorted(persons, key=lambda x: -len(x.get("name") or "")):
        n = p.get("name") or ""
        if n and n in q and not any(n in (k.get("name") or "") for k in found):
            found.append(p)
    found.sort(key=lambda p: q.find(p["name"]))
    return found


def parse_local_intent(q, persons, links, interactions=()):
    """本地指令解析：命中返回 (reply, actions)，不命中返回 None。"""
    q = str(q or "").strip()
    if not q:
        return None
    names = {p["name"]: p for p in persons}
    hit = None
    for name in sorted(names, key=len, reverse=True):
        if name in q:
            hit = names[name]
            break

    # ---------- 两人相关（v0.61）：关系查询 / 建立连接 ----------
    found = _distinct_names_in(q, persons)
    if len(found) >= 2:
        a, b = found[0], found[1]
        direct = [l for l in links
                  if {l.get("person_a"), l.get("person_b")} == {a["id"], b["id"]}]
        # ① 问关系：「X和Y是什么关系」「X和Y认识吗」
        if any(k in q for k in ("什么关系", "认识吗", "啥关系")):
            if direct:
                segs = "；".join(
                    "%s（%d分）" % (db.normalize_link_relation(l.get("relation") or "认识"),
                                    int(l.get("score") or 0))
                    + (("，备注：" + l["note"]) if l.get("note") else "")
                    for l in direct)
                return ("%s 和 %s 有直接关系：%s。" % (a["name"], b["name"], segs), [])
            paths = relationship_paths(persons, links, a["id"], [b["id"]])
            if paths:
                id_name = {p["id"]: p["name"] for p in persons}
                chain = paths[0]
                hops = []
                for l in chain["links"]:
                    hops.append(db.normalize_link_relation(l.get("relation") or "认识"))
                route_names = " → ".join(id_name.get(i, str(i)) for i in chain["person_ids"])
                return ("%s 和 %s 没有直接记录的关系；间接路径（%d 步）：%s。"
                        % (a["name"], b["name"], len(hops), route_names), [])
            return ("现有资料里 %s 和 %s 还没有任何关系记录；可以直接告诉我"
                    "「%s和%s是…」我来建立连接。" % (a["name"], b["name"],
                                                     a["name"], b["name"]), [])
        # ② 建连接：「X和Y是大学同学」（关系词白名单防误判）
        m = re.search(r"是\s*(?:我的)?([^，。；！？!?,、\s]{1,10})", q)
        if m and not any(k in q for k in ("什么", "谁", "吗", "怎么", "哪")):
            rel = re.sub(r"(?:的|了|呢|吧|啊|呀)+$", "", m.group(1))
            if rel and any(rel.endswith(noun) for noun in REL_NOUNS):
                if direct:
                    segs = "；".join(
                        db.normalize_link_relation(l.get("relation") or "认识")
                        for l in direct)
                    return ("%s 和 %s 已经有连接（%s），不重复建立。"
                            % (a["name"], b["name"], segs), [])
                return ("好的，已建立 %s ↔ %s 的「%s」连接。"
                        % (a["name"], b["name"], rel),
                        [{"op": "add_link", "a": a["name"], "b": b["name"],
                          "relation": rel, "score": 50}])

    # 调关系分：认「加5 / +5 / 减3 / -3 / 调为80」
    m = re.search(r"关系分\s*(加|减|[+\-])?\s*(\d+)", q)
    if hit and m:
        sign = -1 if m.group(1) in ("减", "-") else 1
        delta = sign * float(m.group(2))
        return ("好的，我把 %s 的关系分调整 %s。" % (hit["name"], m.group(2) if sign > 0 else "-" + m.group(2)),
                [{"op": "update_score", "name": hit["name"], "delta": delta}])
    # 加备注
    m = re.search(r"(?:给\S+)?(?:加|写)?备注[:：]\s*(.+)$", q)
    if hit and m and not any(k in q for k in ("电话", "生日", "地址")):
        text = m.group(1).strip()
        return ("记下了：%s — %s。" % (hit["name"], text),
                [{"op": "add_memo", "name": hit["name"], "text": text}])
    # 查资料
    if hit and any(k in q for k in ("电话", "手机", "联系方式")) and "找" not in q:
        return ("%s 的电话：%s。" % (hit["name"], hit.get("phone") or "（未填）"), [])
    if hit and ("生日" in q or "几号" in q):
        ev = _nearest_event(hit)
        base = "%s 的生日：%s。" % (hit["name"], hit.get("birthday") or "（未填）")
        if ev:
            base += " 距下次还有 %d 天。" % ev[0]
        return (base, [])
    if hit and any(k in q for k in ("地址", "住址", "收货")):
        return ("%s 的收货地址：%s。" % (hit["name"], hit.get("address") or "（未填）"), [])
    if hit and ("资料" in q or "备注是什么" in q or "是谁" in q):
        info = (hit.get("notes") or "").split("｜")[0].strip()
        return ("%s：%s 关系分 %d，%s" % (
            hit["name"], hit.get("relation_type") or "",
            round(float(hit.get("closeness") or 0) * 100), info or "暂无备注"), [])
    # 查待办/提醒
    if any(k in q for k in ("待办", "提醒", "最近要", "该联系谁", "有什么要办")):
        rows = []
        for p in persons:
            ev = _nearest_event(p)
            if ev:
                rows.append((ev[0], p["name"], ev[1]))
        rows.sort()
        if not rows:
            return ("最近没有快到期的生日或纪念日。", [])
        top = "；".join("%s %s（%d 天后）" % (n, lab, d) for d, n, lab in rows[:4])
        return ("近期提醒：" + top + "。", [])
    return None


# ---------------------------------------------------------------- 关系等级与答谢
# 发布方案要求「谁在市医院、关系等级怎么样、被帮助之后的答谢方案」一次答全。
# 等级只由已记录的关系事实（圈子/亲密度）决定，不按问题类型编造。
THANKS_PLANS = {
    "家人": "家人之间先把事办成，事后回家吃顿饭、陪半天，比买东西更实在。",
    "挚友": "约一顿饭当面道谢，加一张手写卡片；不必贵重，重在专门为他留出时间。",
    "重要": "备一份他常提起的对口小礼，附一封写清来意的感谢信，三天内送到。",
    "普通": "当天发一条具体到事的道谢消息（说清帮了什么、结果如何），隔周带杯咖啡或点心。",
    "弱关系": "先发一条简短致谢并说明结果，别拖；以后有来有往再逐步加深。",
}

# 挂号、诉讼、内推这类「欠人情的大事」，同一关系等级也要加码答谢，
# 不能拿一杯咖啡打发；仍只给可执行动作，不承诺价格和渠道。
HEAVY_THANKS_PLANS = {
    "家人": "家人先把事办成，事后回家吃顿饭、陪半天，再主动分担一件家里的事。",
    "挚友": "约饭当面道谢加手写卡，并记下这次人情，改天主动帮他一次。",
    "重要": "备一份对口谢礼加手写感谢信，三天内送到；事后主动回帮一次。",
    "普通": "这次是重人情：当天把结果和谢意说清楚，三天内备一份实用谢礼（水果、茶叶或家乡特产）送上门，别只发消息。",
    "弱关系": "当天致谢并说明结果，三天内寄一份不贵但用心的谢礼；以后对方有事主动搭把手。",
}


def relation_level(person, circles=None):
    """把已记录的关系事实映射成一句关系等级，供推荐与答谢共用。"""
    kinds = {c.get("kind") for c in (circles if circles is not None
                                     else person_circles(person))}
    closeness = float(person.get("closeness") or 0)
    # 家属若只带亲属元数据、没有圈子元数据，过去会按分数落到「挚友」，
    # 找人答复里把表姐说成挚友——关系类型本身已是家人，直接认。
    if "家人" in kinds or person.get("relation_type") == "家人":
        return "家人"
    if closeness >= 0.80:
        return "挚友"
    if closeness >= 0.65:
        return "重要"
    if closeness >= 0.45:
        return "普通"
    return "弱关系"


def thanks_for(level, person=None, heavy=False):
    """按关系等级给出可执行的答谢方案；重人情自动加码，不承诺价格。"""
    plans = HEAVY_THANKS_PLANS if heavy else THANKS_PLANS
    plan = plans.get(level) or plans["普通"]
    name = (person or {}).get("name")
    return f"对{name}：{plan}" if name else plan


# ---------------------------------------------------------------- 会议纪要 / 社交细节
NOTE_RELATION_WORDS = (
    ("同事", "同事"), ("同学", "同学"), ("家人", "家人"), ("亲戚", "家人"),
    ("朋友", "朋友"), ("合作伙伴", "合作伙伴"), ("合伙人", "合作伙伴"),
    ("客户", "合作伙伴"), ("引荐", "引荐"), ("介绍", "引荐"),
)
POSITIVE_WORDS = ("感谢", "帮了大忙", "帮了", "支持", "照顾", "合作愉快", "很照顾", "靠谱")
NEGATIVE_WORDS = ("冷淡", "没回", "拒绝", "冲突", "不愉快", "拖了", "失信")


def parse_notes(text, persons, links=()):
    """把会议纪要/聊天记录里的事实拆成可执行动作，本地启发式、离线可用。

    只做两件有据可依的事：把提到的人写进备注、把明确说到的两人关系补成边。
    没有提到的人、没有说到的关系一律不猜；不覆盖已有关系。
    """
    by_name = {p["name"]: p for p in persons if p.get("name")}
    existing = {frozenset((l.get("person_a"), l.get("person_b"))) for l in links}
    actions, notes, skipped = [], {}, []
    for raw in re.split(r"[。；;！!？?\n\r]+", str(text or "")):
        sentence = raw.strip(" \t·-—、,，")
        if len(sentence) < 4:
            continue
        mentioned = [name for name in by_name if name in sentence]
        if not mentioned:
            continue
        relation = next((kind for word, kind in NOTE_RELATION_WORDS if word in sentence), None)
        pair = None
        if len(mentioned) >= 2 and relation:
            a, b = mentioned[0], mentioned[1]
            pair = (a, b)
            if frozenset((by_name[a]["id"], by_name[b]["id"])) not in existing:
                actions.append({"op": "add_link", "a": a, "b": b,
                                "relation": relation, "score": 60})
                existing.add(frozenset((by_name[a]["id"], by_name[b]["id"])))
            else:
                skipped.append(f"{a}↔{b} 已有关系记录，保留原值")
        person = mentioned[0]
        notes.setdefault(person, []).append(sentence[:80])
        delta = 3 if any(w in sentence for w in POSITIVE_WORDS) else (
            -3 if any(w in sentence for w in NEGATIVE_WORDS) else 0)
        if delta:
            actions.append({"op": "update_score", "name": person, "delta": delta})
    for person, sentences in notes.items():
        actions.append({"op": "add_memo", "name": person,
                        "text": "；".join(sentences)[:160]})
    mentioned_all = {name for name in by_name
                     for sentence in re.split(r"[。；;\n]+", str(text or ""))
                     if name in sentence}
    unknown = [w for w in ("新同事", "新朋友") if w in str(text or "")]
    summary = []
    if actions:
        links_added = sum(1 for a in actions if a["op"] == "add_link")
        memos = sum(1 for a in actions if a["op"] == "add_memo")
        scores = sum(1 for a in actions if a["op"] == "update_score")
        summary.append(f"识别到 {len(mentioned_all)} 位已建档人物："
                       + "、".join(sorted(mentioned_all)))
        if links_added:
            summary.append(f"可补 {links_added} 条人物间关系")
        if memos:
            summary.append(f"可写入 {memos} 条交往记忆")
        if scores:
            summary.append(f"可微调 {scores} 人的关系分")
        if skipped:
            summary.append("保留原记录：" + "；".join(skipped[:3]))
        if unknown:
            summary.append("提到但尚未建档的说法：" + "、".join(unknown) + "（需先建人物卡）")
    else:
        summary.append("没有识别到已建档人物；先确认纪要里的人是否已在人物卡里。")
    return {"actions": actions, "summary": summary,
            "persons": sorted(mentioned_all)}


def explicit_family_links(persons):
    """只接受同族、明确相邻代、明确指定对方的亲属连接；端点长辈在前。"""
    by_name = {p["name"]: p for p in persons}
    out, seen = [], set()
    for a, pa in by_name.items():
        ma = family_metadata(pa)
        ga = ma.get("generation")
        if type(ga) is not int or not ma.get("family"):
            continue
        younger = ma.get("younger", [])
        if not isinstance(younger, list):
            continue
        for item in younger:
            if not isinstance(item, dict):
                continue
            b = item.get("name")
            mb = family_metadata(by_name.get(b, {}))
            if (b == a or type(mb.get("generation")) is not int
                    or mb.get("family") != ma["family"]
                    or mb["generation"] != ga + 1 or (a, b) in seen):
                continue
            rel = item.get("relation") or "家人"
            if rel not in {"家人", "母子", "母女", "父子", "父女", "姑侄",
                           "叔侄", "伯侄", "舅甥", "姨甥"}:
                continue
            seen.add((a, b))
            note = json.dumps({"kinship": {"elder": a, "younger": b,
                                          "delta": 1}}, ensure_ascii=False)
            out.append({"a": a, "b": b, "relation": rel,
                        "score": 78, "note": note})
    return out


def family_constraints(persons, links=()):
    """返回明确的 (长辈ID, 小辈ID) 半径约束；缺失/矛盾资料不推断。"""
    by_name = {p["name"]: p for p in persons}
    meta = {p["id"]: family_metadata(p) for p in persons}
    out = set()
    for a, ma in meta.items():
        ga = ma.get("generation")
        if type(ga) is not int or not ma.get("family"):
            continue
        for b, mb in meta.items():
            if (mb.get("family") == ma["family"]
                    and type(mb.get("generation")) is int
                    and mb["generation"] > ga):
                out.add((a, b))
    for link in links:
        try:
            kin = json.loads(link.get("note") or "{}").get("kinship", {})
            a = by_name[kin["elder"]]["id"]
            b = by_name[kin["younger"]]["id"]
            if (kin.get("delta") == 1 and a != b
                    and {a, b} == {link["person_a"], link["person_b"]}
                    and (b, a) not in out):
                # 已有约束能从小辈走回长辈时拒绝，避免矛盾资料形成环。
                pending, visited = [b], set()
                while pending:
                    node = pending.pop()
                    if node in visited:
                        continue
                    visited.add(node)
                    pending.extend(y for x, y in out if x == node)
                if a not in visited:
                    out.add((a, b))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return sorted(out)


# ---------------------------------------------------------------- mock 推断
def _mock_my_scores(persons):
    """启发式判断每个人与我的关系分 → 亲密度/情感权重"""
    out = {}
    for p in persons:
        rel = p.get("relation_type") or "普通朋友"
        base = REL_BASE.get(rel, 50)
        closeness = float(p.get("closeness") or 0.5)
        sentiment = float(p.get("sentiment_weight") or 0.5)
        score = base + closeness * 8 + sentiment * 4
        if p.get("birthday"):
            score += 3
        if p.get("intro_by"):
            score += 2
        score = int(max(5, min(99, round(score))))
        new_close = round(max(0.2, min(1.0, 0.35 + (score - 35) / 100.0)), 2)
        new_sent = round(max(0.2, min(1.0, 0.3 + (score - 30) / 90.0)), 2)
        reason = f"{rel}；现有亲密度 {closeness:.2f}、情感权重 {sentiment:.2f}"
        if p.get("birthday"):
            reason += "；有生日记录"
        if p.get("intro_by"):
            reason += f"；由 {p['intro_by']} 引荐"
        out[p["name"]] = {"closeness": new_close, "sentiment": new_sent,
                          "score": score, "reason": reason}
    return out


def _mock_links(persons, extra_links=None):
    """启发式推断人物之间的关系 + 关系分数

    v0.59：同一对人物只保留一条线（按事实强度：亲属 > 已知关系 > 引荐）。
    过去 key 里带关系名，同一对会同时出「合作伙伴 + 引荐」两条，画面上
    两条线几乎重合，看起来像凭空多编了一条关系。
    """
    out = []
    seen = set()

    def _add(a, b, rel, score, note):
        rel = db.normalize_link_relation(rel)
        key = tuple(sorted((a, b)))
        if key in seen or a == b:
            return
        seen.add(key)
        out.append({"a": a, "b": b, "relation": rel, "score": int(score),
                    "note": note})

    by_name = {p["name"]: p for p in persons}
    for link in explicit_family_links(persons):
        _add(link["a"], link["b"], link["relation"], link["score"], link["note"])
    # 1) 外部补充的普通连接；亲属连接必须有明确代际资料。
    for a, b, rel in (extra_links or []):
        if a in by_name and b in by_name and rel not in FAMILY_RELATIONS:
            pa, pb = by_name[a], by_name[b]
            score = int(((float(pa.get("closeness") or 0.5)
                          + float(pb.get("closeness") or 0.5)) / 2) * 70
                        + REL_BASE.get(rel if rel in REL_BASE else "普通朋友", 50) * 0.3)
            _add(a, b, rel, max(20, min(95, score)), "沙盒好友关系")
    # 2) 引荐链：A 的引荐人是 B → 引荐连接
    for p in persons:
        intro = (p.get("intro_by") or "").strip()
        if intro and intro in by_name:
            _add(intro, p["name"], "引荐", 70, f"{p['name']} 由 {intro} 引荐")
    return out


# ---------------------------------------------------------------- API 推断
def _api_judge(persons, extra_links=None):
    """调用 OpenAI 兼容接口做关系判断；失败抛异常由调用方降级"""
    cfg = load_config()
    base = cfg.get("base_url", "").rstrip("/")
    key = cfg.get("api_key", "")
    model = cfg.get("model", "")
    if not (base and key and model):
        raise RuntimeError("大模型 API 未完整配置（base_url/api_key/model）")

    lines = []
    for p in persons:
        lines.append(
            f'- {p["name"]}｜关系类型:{p.get("relation_type")}｜亲密度:{p.get("closeness")}'
            f'｜情感权重:{p.get("sentiment_weight")}｜引荐人:{p.get("intro_by") or "无"}'
            f'｜生日:{p.get("birthday") or "无"}')
    if extra_links:
        lines.append("已知好友关系：" + "；".join(f"{a}-{b}({db.normalize_link_relation(r)})" for a, b, r in extra_links))
    prompt = (
        "你是人际关系分析助手。下面是用户通讯录中的联系人资料，请判断：\n"
        "1) 仅输出有已知好友关系或明确引荐依据的人物关系与分数(0-100)。"
        "同属工作、朋友、同事、合作伙伴分类不代表两人认识，不得按类别两两补边。\n"
        "泛化合作关系统一写为合作伙伴；历史合作同义。具体项目、交易关系保留原名称。\n"
        "2) 每位联系人与用户本人的关系分数(0-100)，并折算成亲密度(0-1)、情感权重(0-1)\n"
        "只输出 JSON，不要其他文字，格式：\n"
        '{"links":[{"a":"姓名","b":"姓名","relation":"认识","score":50,"note":"依据"}],'
        '"my_scores":{"姓名":{"closeness":0.5,"sentiment":0.5,"score":50,"reason":"依据"}}}\n\n'
        + "\n".join(lines))

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("大模型返回内容不含 JSON")
    return json.loads(content[start:end + 1])


# ---------------------------------------------------------------- 统一入口
def _normalize_api_judge(r, persons, extra_links=None):
    """约束模型输出，不允许编造人物或亲属关系。"""
    my = r.get("my_scores") or {}
    lks = [l for l in (r.get("links") or [])
           if isinstance(l, dict) and l.get("relation") not in FAMILY_RELATIONS]
    known = {frozenset((l["a"], l["b"])) for l in _mock_links(persons, extra_links)}
    lks = [l for l in lks if frozenset((l.get("a"), l.get("b"))) in known]
    lks.extend(explicit_family_links(persons))
    names = {p["name"] for p in persons}
    lks = [l for l in lks if l.get("a") in names
           and l.get("b") in names and l["a"] != l["b"]]
    my = {k: v for k, v in my.items() if k in names}
    for l in lks:
        l["relation"] = db.normalize_link_relation(l.get("relation"))
        try:
            l["score"] = int(max(0, min(100, float(l.get("score", 50)))))
        except Exception:
            l["score"] = 50
    out = dict(r)
    out.update({"mode": "api", "my_scores": my, "links": lks})
    return out


def judge_all(persons, extra_links=None):
    """云端启用时由服务端计费；否则保留本地 API/免费模拟模式。"""
    if not persons:
        return {"mode": mode(), "my_scores": {}, "links": []}
    import cloud_client
    if cloud_client.is_active():
        result = cloud_client.relation_judge(persons, extra_links)
        if result.get("mode") == "api":
            return _normalize_api_judge(result, persons, extra_links)
        return result
    ticket = None
    if mode() == "api":
        import points
        ticket = points.begin_api_charge("relation_judge")
        try:
            r = _api_judge(persons, extra_links)
            return _normalize_api_judge(r, persons, extra_links)
        except Exception:
            if ticket:
                points.refund_api_charge(ticket)
            pass                          # 降级 mock（已退回预扣积分）
    return {"mode": "mock",
            "my_scores": _mock_my_scores(persons),
            "links": _mock_links(persons, extra_links)}


if __name__ == "__main__":
    db.init_db()
    persons = db.list_persons()
    r = judge_all(persons)
    print("模式:", r["mode"])
    print("与我的关系分:", {k: v["score"] for k, v in r["my_scores"].items()})
    print("人物间关系:", len(r["links"]), "条")
    for l in r["links"][:8]:
        print("  ", l)
