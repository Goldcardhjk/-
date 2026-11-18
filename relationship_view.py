"""关系事实的只读展示投影；不访问数据库、不生成关系或布局坐标。"""


def build_relationship_view(persons, links, circles=(), *,
                            is_circle_internal=None, show_all=False,
                            normalize_relation=None):
    """从人物与真实关系记录构建独立展示数据，不修改任何输入记录。

    persons 为带有 ``id`` 的人物字典迭代器；links 为带有 ``person_a``、
    ``person_b``、``relation`` 等原字段的关系字典迭代器。两者均可为生成器。
    circles 仅接受布局评估后已激活的圈子，不接受尚未激活的候选圈。
    默认模式下非空 circles 必须提供 ``is_circle_internal(link, circles)``，
    以便沿用调用方的泛化分类规则；``show_all=True`` 恢复所有有效真实记录，
    此时不执行圈隐藏分类。``normalize_relation(relation)`` 可选，
    仅用于标签语义去重，不改写原始关系类型。

    返回普通字典：
    - person_ids：按输入顺序去重的 ID 元组，包括没有关系记录的人物。
    - visible_links：端点均存在、非自环且未被圈隐藏的关系记录浅副本列表。
      同一无向人对的多条真实记录仍完整保留。
    - display_links：每无向人对一条的展示记录，保留该对首条可见记录的
      原字段，新增 label（聚合语义文本）及 records（该对全部可见记录副本）。
      路由直接消费此列表，首条记录仅提供稳定 ID 和样式，不决定关系语义。
    - labels_by_pair：无向人对到去重语义文本的字典，如 ``{(1, 2):
      '父子 · 引荐'}``；端点按 str(ID) 排序，与现有 UI 路由一致。
      仅合并可见记录的非空关系文本，按首次出现顺序排列。
    - metrics：人数、原始/有效/隐藏/可见记录数、可见人对数和孤立人数。
      有效记录要求双端点存在且不是自环；隐藏数以有效记录为基数。
      孤立人数以可见关系端点统计，已被圈替代的记录不计作可见连接。

    接入示例（依赖由调用方提供，本模块不导入它们）：
        view = build_relationship_view(
            persons, links, active_circles,
            is_circle_internal=llm.circle_internal_link,
            normalize_relation=db.normalize_link_relation)
        routes = self._route_links(view['display_links'], pos, cx, cy)
        for route in routes:
            label = route['lnk']['label']

    显示全量关系时，使用相同真实记录且 show_all=True 再构建一份投影。
    人物评分仅是人物属性，不会据此补出“我”与任何人物之间的关系。
    """
    person_ids = tuple(dict.fromkeys(person['id'] for person in persons))
    person_id_set = set(person_ids)
    circles = tuple(circles)
    if circles and not show_all and is_circle_internal is None:
        raise ValueError('存在已激活圈子时必须提供 is_circle_internal 分类回调')

    visible_links = []
    records_by_pair = {}
    labels = {}
    connected_ids = set()
    raw_count = valid_count = hidden_count = 0
    for link in links:
        raw_count += 1
        person_a, person_b = link['person_a'], link['person_b']
        if (person_a == person_b or person_a not in person_id_set
                or person_b not in person_id_set):
            continue
        valid_count += 1
        if circles and not show_all and is_circle_internal(link, circles):
            hidden_count += 1
            continue

        visible_links.append(dict(link))
        pair = tuple(sorted((person_a, person_b), key=str))
        records_by_pair.setdefault(pair, []).append(dict(link))
        pair_labels = labels.setdefault(pair, [])
        relation = link.get('relation')
        if normalize_relation is not None:
            relation = normalize_relation(relation)
        text = str(relation).strip() if relation is not None else ''
        if text and text not in pair_labels:
            pair_labels.append(text)
        connected_ids.update(pair)

    labels_by_pair = {
        pair: ' · '.join(pair_labels) for pair, pair_labels in labels.items()
    }
    display_links = []
    for pair, records in records_by_pair.items():
        display_link = dict(records[0])
        display_link['label'] = labels_by_pair[pair]
        display_link['records'] = records
        display_links.append(display_link)

    return {
        'person_ids': person_ids,
        'visible_links': visible_links,
        'display_links': display_links,
        'labels_by_pair': labels_by_pair,
        'metrics': {
            'person_count': len(person_ids),
            'raw_record_count': raw_count,
            'valid_record_count': valid_count,
            'hidden_record_count': hidden_count,
            'visible_record_count': len(visible_links),
            'visible_pair_count': len(labels),
            'isolated_person_count': len(person_id_set - connected_ids),
        },
    }
