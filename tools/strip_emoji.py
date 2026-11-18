# -*- coding: utf-8 -*-
"""v0.13 一次性迁移：把 _btn/_label/_make_collapsible 调用块字符串里的
emoji 前缀删掉（图标由 icon= 参数另行补充，本脚本只负责清 emoji）。

括号配平定位调用块；只动 "..." / '...' / f"..." / f'...' 字面量。
"""
import re
import shutil

PATH = "ui.py"
shutil.copy2(PATH, PATH + ".pre_emoji_strip")

EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2764"
    "\u2B50\u3030\u303D\u00A9\u00AE]+[ ]?")
STR = re.compile(r'''(f?"(?:[^"\\]|\\.)*"|f?'(?:[^'\\]|\\.)*')''')
CALL = re.compile(r"self\._btn\(|self\._label\(|self\._make_collapsible\(")

with open(PATH, encoding="utf-8") as f:
    lines = f.readlines()

out = []
i = 0
n_stripped = 0
while i < len(lines):
    ln = lines[i]
    out.append(ln)
    if not CALL.search(ln):
        i += 1
        continue
    # 括号配平找调用块结束
    depth = ln.count("(") - ln.count(")")
    j = i
    while depth > 0 and j + 1 < len(lines):
        j += 1
        blk = lines[j]
        out.append(blk)
        depth += blk.count("(") - blk.count(")")
    # 处理块内每一行的字符串字面量
    for k in range(i, j + 1):
        orig = lines[k]

        def fix(m):
            global n_stripped
            s = m.group(0)
            new = EMOJI.sub("", s)
            if new != s:
                n_stripped += 1
            return new

        lines[k] = STR.sub(fix, orig)
    i = j + 1

with open(PATH, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("stripped %d string literals" % n_stripped)
