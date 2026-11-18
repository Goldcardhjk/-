# -*- coding: utf-8 -*-
"""v0.13 一次性迁移：给 _btn 调用按按钮文字补 icon= 参数。

两遍式：第一遍定位所有调用块并计算插入内容，第二遍统一写回，
避免边改边扫导致行号失效。空文字按钮按行号在 MAP_EMPTY 处理。
"""
import re
import shutil

PATH = "ui.py"
shutil.copy2(PATH, PATH + ".pre_iconify")

MAP = {
    "测试连接": "icon_search",
    "保存": "icon_save",
    "保存笔记": "icon_save",
    "建立连接": "icon_link",
    "更换形象": "icon_avatar",
    "编辑": "icon_edit",
    "关系程度": "icon_slider",
    "连接人物": "icon_add",
    "送礼": "icon_gift",
    "祝福": "icon_star",
    "生成专属祝福": "icon_star",
    "模拟一键付款": "icon_gift",
    "复制": "icon_copy",
    "连接沙盒": "icon_link",
    "一键拉取全部": "icon_import",
    "大模型判断关系": "ai_avatar",
    "去看关系网络": "icon_net",
    "刷新": "icon_refresh",
    "从绿泡泡拉取": "icon_conn",
    "断开": None,
}

with open(PATH, encoding="utf-8") as f:
    src = f.read()

lines = src.splitlines(keepends=True)
STR = re.compile(r'''(f?"(?:[^"\\]|\\.)*"|f?'(?:[^'\\]|\\.)*')''')

# ---- 第一遍：定位 _btn 调用块 ----
edits = []          # (start_line, end_line, icon)
i = 0
BTN = re.compile(r"self\._btn\(")
while i < len(lines):
    if not BTN.search(lines[i]):
        i += 1
        continue
    # 从 self._btn( 处开始括号配平，确保插入点落在 _btn 自己的右括号前
    # （而不是行尾 .pack(...) 的右括号）
    depth = 0
    j = i
    k = lines[j].index("self._btn(")
    end_k = None
    while j < len(lines):
        line = lines[j]
        while k < len(line):
            ch = line[k]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end_k = k
                    break
            k += 1
        if end_k is not None:
            break
        j += 1
        k = 0
    if end_k is None:
        i += 1
        continue
    block = "".join(lines[i:j + 1])
    if "icon=" not in block:
        m = STR.search(block)
        if m:
            text = m.group(0).strip("\"'")
            if text.startswith("f"):
                text = text[2:-1]
            icon = MAP.get(text, "__none__")
            if icon and icon != "__none__":
                edits.append((i, j, end_k, icon))
    i = j + 1

# ---- 第二遍：从后往前插，避免行号漂移 ----
n = 0
for i, j, end_k, icon in reversed(edits):
    # end_k 是 _btn( 调用在行 i+j 段内的闭合括号偏移；重算更稳妥：
    # 直接定位合并块里 self._btn( 后配平的右括号
    block = "".join(lines[i:j + 1])
    s = block.index("self._btn(")
    depth = 0
    pos = None
    k = s
    while k < len(block):
        if block[k] == "(":
            depth += 1
        elif block[k] == ")":
            depth -= 1
            if depth == 0:
                pos = k
                break
        k += 1
    if pos is None:
        continue
    new_block = block[:pos] + ', icon="%s"' % icon + block[pos:]
    lines[i:j + 1] = [new_block]
    n += 1

with open(PATH, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("added %d icons" % n)
