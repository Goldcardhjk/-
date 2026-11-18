"""绿泡泡连接器 · 模拟沙盒

「绿泡泡」是用户对某绿色聊天软件的昵称。真实拉取好友关系需要官方
开放平台 / 合规自动化方案，涉及账号安全与平台规则，MVP 阶段用
「模拟沙盒」完整演示交互链路：
    连接沙盒 -> 浏览好友卡片 -> 一键拉取到人脉库（生成卡片 + 关系连线）

设计要点：
- SANDBOX_CONTACTS 模拟一份「像真的」的好友录：16 人，资料齐全
  （关系/亲密度/喜好/禁忌/生日/地址/电话/重要日期/身份备注）
- 16 人按 家人 4 / 朋友 4 / 工作 5 / 弱关系 3 分组，正好铺满四列卡片墙，
  拉取后关系图 16 张卡 + 6 条人物间事实线（2 条亲属、3 条共事、1 条引荐），
  演示时一眼看清，不靠密集连线凑「关系复杂」的观感
- links 字段只记录**有明确事实依据**的人物连接（亲属/共事/上下级），
  同一个人不在两侧重复声明；「认识」这类泛化关系一律不写
- intro_by 只在确有其事时填写（谁把谁介绍进来），不为了画面丰富而编造
- 后续接真实数据时，只需把 list_contacts() 换成真实来源，其余不变。
"""

import json
import os

GROUPS = ["全部", "家人", "朋友", "工作", "弱关系"]

# 默认使用源码内置沙盒；只有显式设置 JSON 路径时才切换样例源。
_CONTACTS_OVERRIDE = os.environ.get("JINBU_SANDBOX_JSON", "")

SANDBOX_CONTACTS = [
    # ---------------- 家人 4 人：父系（奶奶—叔叔）、母系（小姨—表姐阿芬）两支 ----------------
    {"name": "奶奶", "remark": "父系·叔叔的母亲", "group": "家人",
     "relation_type": "家人", "closeness": 1.0, "sentiment_weight": 0.95,
     "intro_by": "", "prefs": ["养生", "保暖"], "taboo": ["含糖高"],
     "birthday": "1940-05-02", "address": "广州市番禺区华南新城 家中",
     "phone": "13700001001",
     "important_dates": [{"label": "生日家宴", "date": "2026-05-02"}],
     "note": "叔叔的母亲。与小姨一支不属于同一族，不做亲属连边。",
     "kinship": {"family": "演示家庭·父系", "generation": 0,
                 "younger": [{"name": "叔叔", "relation": "母子"}]},
     "links": []},
    {"name": "叔叔", "remark": "父系·奶奶的儿子", "group": "家人",
     "relation_type": "家人", "closeness": 0.65, "sentiment_weight": 0.60,
     "intro_by": "", "prefs": ["茶", "养生"], "taboo": ["辣"],
     "birthday": "1962-11-26", "address": "广州市番禺区华南新城 家中",
     "phone": "13700001002",
     "important_dates": [{"label": "中秋家宴", "date": "2026-09-25"}],
     "note": "奶奶的儿子，住得近，家里大事由他张罗。",
     "kinship": {"family": "演示家庭·父系", "generation": 1},
     "links": []},
    {"name": "小姨", "remark": "母系·阿芬的母亲", "group": "家人",
     "relation_type": "家人", "closeness": 0.90, "sentiment_weight": 0.85,
     "intro_by": "", "prefs": ["保暖", "养生"], "taboo": ["含糖高"],
     "birthday": "1965-10-08", "address": "广州市越秀区东山口 家中",
     "phone": "13700001003",
     "important_dates": [{"label": "生日", "date": "2026-10-08"}],
     "note": "表姐阿芬的母亲，每年体检都提醒一遍。",
     "kinship": {"family": "演示家庭·母系", "generation": 1,
                 "younger": [{"name": "表姐阿芬", "relation": "母女"}]},
     "links": []},
    {"name": "表姐阿芬", "remark": "母系·小姨的女儿", "group": "家人",
     "relation_type": "家人", "closeness": 0.95, "sentiment_weight": 0.90,
     "intro_by": "", "prefs": ["护肤", "养生"], "taboo": [],
     "birthday": "1993-04-12", "address": "广州市天河区珠江新城 阿芬收",
     "phone": "13700001004",
     "important_dates": [],
     "note": "小姨的女儿，同城；市一医院心血管科护士，家里人看病都先问她。",
     "kinship": {"family": "演示家庭·母系", "generation": 2},
     "links": []},

    # ---------------- 朋友 4 人 ----------------
    {"name": "小琳", "remark": "大学室友", "group": "朋友",
     "relation_type": "挚友", "closeness": 0.92, "sentiment_weight": 0.90,
     "intro_by": "", "prefs": ["香薰", "手账", "草莓", "盲盒"], "taboo": ["香菜"],
     "birthday": "1995-03-18", "address": "上海市徐汇区漕溪北路100号 小琳收",
     "phone": "13900002222",
     "important_dates": [{"label": "闺蜜日", "date": "2026-12-01"}],
     "note": "大学室友，无话不谈，礼物要走心小而美。同一宿舍与阿豪相熟。",
     "links": [("阿豪", "挚友")]},
    {"name": "阿豪", "remark": "宿舍上下铺", "group": "朋友",
     "relation_type": "挚友", "closeness": 0.88, "sentiment_weight": 0.80,
     "intro_by": "", "prefs": ["数码", "潮玩", "咖啡"], "taboo": [],
     "birthday": "1994-11-03", "address": "深圳市南山区科技园 阿豪收",
     "phone": "13800007777", "important_dates": [],
     "note": "大学宿舍上下铺，数码产品都会先问他。",
     "links": []},
    {"name": "大鹏", "remark": "周末球友", "group": "朋友",
     "relation_type": "普通朋友", "closeness": 0.55, "sentiment_weight": 0.50,
     "intro_by": "", "prefs": ["篮球", "球鞋", "奶茶"], "taboo": [],
     "birthday": "1995-07-20", "address": "广州市天河区东圃 大鹏收",
     "phone": "13700008888", "important_dates": [],
     "note": "周末球局认识，一周一见；和其他人只在场边点头，不硬连关系。",
     "links": []},
    {"name": "婷婷", "remark": "高中同桌", "group": "朋友",
     "relation_type": "普通朋友", "closeness": 0.45, "sentiment_weight": 0.45,
     "intro_by": "", "prefs": ["手账", "鲜花", "草莓"], "taboo": [],
     "birthday": "1996-09-01", "address": "东莞市南城区 婷婷收",
     "phone": "13700011111", "important_dates": [],
     "note": "高中同桌，久未联系但一直惦记。",
     "circles": [{"kind": "同学", "name": "高中同学"}],
     "links": []},

    # ---------------- 工作 5 人 ----------------
    {"name": "王教授", "remark": "学术引路人", "group": "工作",
     "relation_type": "重要合作伙伴", "closeness": 0.85, "sentiment_weight": 0.80,
     "intro_by": "", "prefs": ["茶", "书法", "历史书", "核桃"], "taboo": ["酒", "烟"],
     "birthday": "1972-09-10", "address": "北京市海淀区中关村南大街5号 王老师收",
     "phone": "13800001111",
     "important_dates": [{"label": "合作周年", "date": "2026-11-20"}],
     "note": "带我入门学术圈的贵人，每年教师节/生日都要顾到。",
     "circles": [{"kind": "合作伙伴", "name": "夏令营课程团队"}],
     "links": [("李博士", "合作伙伴")]},
    {"name": "李博士", "remark": "夏令营课程合作", "group": "工作",
     "relation_type": "重要合作伙伴", "closeness": 0.70, "sentiment_weight": 0.60,
     "intro_by": "王教授", "prefs": ["书", "茶", "文创"], "taboo": [],
     "birthday": "1985-06-30", "address": "广州市大学城外环东路 李博士收",
     "phone": "13700022222", "important_dates": [],
     "note": "王教授介绍的课程合作方，课程体系一起打磨。",
     "circles": [{"kind": "合作伙伴", "name": "夏令营课程团队"}],
     "links": []},
    {"name": "赵总", "remark": "甲方·宏业科技", "group": "工作",
     "relation_type": "重要合作伙伴", "closeness": 0.75, "sentiment_weight": 0.70,
     "intro_by": "", "prefs": ["普洱茶", "高尔夫", "红酒"], "taboo": ["烟"],
     "birthday": "1978-12-12", "address": "深圳市南山区科技园 赵总收",
     "phone": "13800003333",
     "important_dates": [{"label": "合作周年", "date": "2027-03-15"}],
     "note": "甲方，逢年过节必互动；下面的对接人由他指定。",
     "circles": [{"kind": "同事", "name": "宏业科技"}],
     "links": [("孙秘书", "同事")]},
    {"name": "孙秘书", "remark": "赵总助理", "group": "工作",
     "relation_type": "同事", "closeness": 0.55, "sentiment_weight": 0.50,
     "intro_by": "赵总", "prefs": ["香薰", "文创", "鲜花"], "taboo": [],
     "birthday": "1992-03-08", "address": "深圳市南山区科技园 孙秘书收",
     "phone": "13800004444", "important_dates": [],
     "note": "赵总的助理，对接日程都经她，节日祝福别漏。",
     "circles": [{"kind": "同事", "name": "宏业科技"}],
     "links": []},
    {"name": "蔡经理", "remark": "宏业科技·客户经理", "group": "工作",
     "relation_type": "重要合作伙伴", "closeness": 0.65, "sentiment_weight": 0.60,
     "intro_by": "赵总", "prefs": ["咖啡", "数码", "茶"], "taboo": [],
     "birthday": "1989-07-04", "address": "深圳市南山区科技园 蔡经理收",
     "phone": "13800005577", "important_dates": [],
     "note": "赵总下面的客户经理，跑流程都找他。",
     "circles": [{"kind": "同事", "name": "宏业科技"}],
     "links": []},

    # ---------------- 弱关系 3 人 ----------------
    {"name": "房东陈叔", "remark": "房东", "group": "弱关系",
     "relation_type": "弱关系", "closeness": 0.30, "sentiment_weight": 0.30,
     "intro_by": "", "prefs": ["茶", "坚果"], "taboo": [],
     "birthday": "1960-01-15", "address": "广州市番禺区 陈叔收",
     "phone": "13700044444", "important_dates": [],
     "note": "每年续约互动一次，保持客气。",
     "links": []},
    {"name": "阿凯", "remark": "健身教练", "group": "弱关系",
     "relation_type": "普通朋友", "closeness": 0.40, "sentiment_weight": 0.40,
     "intro_by": "", "prefs": ["运动", "蛋白粉", "数码"], "taboo": [],
     "birthday": "1996-04-02", "address": "广州市天河区 阿凯收",
     "phone": "13700055555", "important_dates": [],
     "note": "每周三上课，节假日会发祝福。",
     "links": []},
    {"name": "Lisa", "remark": "瑜伽老师", "group": "弱关系",
     "relation_type": "普通朋友", "closeness": 0.40, "sentiment_weight": 0.40,
     "intro_by": "", "prefs": ["香薰", "养生", "鲜花"], "taboo": [],
     "birthday": "1991-11-11", "address": "广州市海珠区 Lisa收",
     "phone": "13700066666", "important_dates": [],
     "note": "周末瑜伽课老师，节日卡必到。",
     "links": []},
]

if _CONTACTS_OVERRIDE:
    with open(_CONTACTS_OVERRIDE, "r", encoding="utf-8") as _f:
        _loaded_contacts = json.load(_f)
    if not isinstance(_loaded_contacts, list) or not all(
            isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"].strip()
            for c in _loaded_contacts):
        raise ValueError("JINBU_SANDBOX_JSON 必须是包含非空姓名的联系人列表")
    if len({c["name"] for c in _loaded_contacts}) != len(_loaded_contacts):
        raise ValueError("JINBU_SANDBOX_JSON 中的联系人姓名不能重复")
    SANDBOX_CONTACTS = _loaded_contacts

# 关系 -> 主题色（与网络页一致）
REL_COLOR = {
    "家人": "#F85149", "挚友": "#F0883E", "重要合作伙伴": "#A371F7",
    "同事": "#3FB950", "普通朋友": "#22D3EE", "弱关系": "#8A97A8",
}


def list_contacts(group=None):
    """沙盒好友列表；group=None 返回全部"""
    if not group or group == "全部":
        return [dict(c) for c in SANDBOX_CONTACTS]
    return [dict(c) for c in SANDBOX_CONTACTS if c.get("group") == group]


def group_counts():
    """各分组人数（含「全部」）"""
    counts = {"全部": len(SANDBOX_CONTACTS)}
    for c in SANDBOX_CONTACTS:
        counts[c.get("group", "弱关系")] = counts.get(c.get("group", "弱关系"), 0) + 1
    return counts


def contact_links(contact):
    """该好友在沙盒内的连接：[(对方姓名, 关系), ...]"""
    return list(contact.get("links", []))


def connect():
    """模拟连接绿泡泡沙盒，返回连接状态"""
    return {"status": "connected", "server": "绿泡泡模拟沙盒 v0.5",
            "message": f"已连接，发现 {len(SANDBOX_CONTACTS)} 位可拉取好友"}


def contact_to_person(contact):
    """把沙盒好友转换为人脉库 person 数据（供 db.save_person 使用）"""
    remark = contact.get("remark", "")
    note = contact.get("note", "")
    combined = note + (f"｜身份：{remark}" if remark else "")
    if contact.get("circles"):
        combined += "｜圈子元数据:" + json.dumps(contact["circles"], ensure_ascii=False)
    if contact.get("kinship"):
        combined += "｜亲属元数据:" + json.dumps(contact["kinship"], ensure_ascii=False)
    return {
        "name": contact["name"],
        "relation_type": contact["relation_type"],
        "closeness": contact.get("closeness", 0.5),
        "sentiment_weight": contact.get("sentiment_weight", 0.5),
        "intro_by": contact.get("intro_by", ""),
        "address": contact.get("address", ""),
        "phone": contact.get("phone", ""),
        "birthday": contact.get("birthday", ""),
        "important_dates": json.dumps(contact.get("important_dates", []),
                                      ensure_ascii=False),
        "prefs": json.dumps(contact.get("prefs", []), ensure_ascii=False),
        "taboo": json.dumps(contact.get("taboo", []), ensure_ascii=False),
        "notes": combined,
        "source": "wechat",
    }


if __name__ == "__main__":
    print(connect())
    for c in list_contacts():
        lk = "、".join(f"{t}({r})" for t, r in contact_links(c)) or "-"
        print(f"  [{c['group']}] {c['name']:<6} {c['relation_type']:<8} "
              f"引荐:{c['intro_by'] or '-':<5} 连接: {lk}")
