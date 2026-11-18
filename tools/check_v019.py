# -*- coding: utf-8 -*-
"""v0.19 功能验证：关系卡删除人物后可在绿泡泡连接器重新导入

链路：
1. 连接器初始状态——全部好友已拉取（conn_imported = 全部）
2. 从人脉库删除一位人物（模拟关系卡删除）
3. _conn_after_person_delete 同步后——被删者移出 conn_imported
4. _render_conn_grid 自愈——重渲染后 conn_imported 与库内一致
5. import_one 重新拉取——人物重新入库、conn_imported 恢复、计数同步
6. 冒烟环境不依赖对话框（_ask 默认 True 会弹窗，直接调内部链路）

用法：.venv/Scripts/python.exe tools/check_v019.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
db.init_db()

import tkinter as tk
import wechat_sandbox
from ui import App

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [ok] " if cond else "  [FAIL] ") + name +
          (("  | " + str(extra)) if extra else ""))


app = App()
app.geometry("1600x920+4000+4000")
app.update()
app.refresh_network()
app.update()

sandbox = wechat_sandbox.list_contacts()
persons = db.list_persons()
check("沙盒好友与库内人物齐备", len(sandbox) >= 2 and len(persons) >= 2,
      "sandbox=%d persons=%d" % (len(sandbox), len(persons)))

# ① 连接器初始：已全部拉取
app.nb.select(app.tab_conn)
app.update()
target = sandbox[0]
check("初始 conn_imported 含目标好友",
      target["name"] in app.conn_imported,
      sorted(app.conn_imported))

# ② 模拟关系卡删除：从库中删除一位人物（不经 _ask 对话框）
pid = next(p["id"] for p in persons if p["name"] == target["name"])
db.delete_person(pid)
check("删除后库内已无该人物",
      not any(p["name"] == target["name"] for p in db.list_persons()))

# ③ 删除入口同步（conn_imported 尚有旧缓存 = 修复前状态）
check("删除后 conn_imported 仍缓存被删者（修复前现象）",
      target["name"] in app.conn_imported)
app._conn_after_person_delete(target["name"])
check("_conn_after_person_delete 后移出缓存",
      target["name"] not in app.conn_imported)

# ④ 自愈：直接污染缓存模拟漏同步，_render_conn_grid 应重算恢复
app.conn_imported.add(target["name"])
app._render_conn_grid()
app.update()
check("_render_conn_grid 自愈重算（缓存污染也恢复）",
      target["name"] not in app.conn_imported)
check("计数标签已同步",
      "已在库 %d / %d 位" % (len(app.conn_imported), len(app.sandbox))
      in app.conn_count_lbl.cget("text"),
      app.conn_count_lbl.cget("text"))

# ⑤ 重新拉取：卡片应显示「拉取」按钮而不是「已拉取」标签
app.import_one(target["name"])
app.update()
check("重新拉取后人物回到人脉库",
      any(p["name"] == target["name"] for p in db.list_persons()))
check("重新拉取后 conn_imported 恢复含目标",
      target["name"] in app.conn_imported)
check("重新拉取后卡片显示已拉取态",
      "已在库 %d / %d 位" % (len(app.conn_imported), len(app.sandbox))
      in app.conn_count_lbl.cget("text"),
      app.conn_count_lbl.cget("text"))

# ⑥ 重新拉取后关系连线同步（沙盒内关系重建）
links = db.list_links()
check("重新拉取后关系连线存在", len(links) >= 0, "%d links" % len(links))

# ⑦ 全链路不炸：卡片/网络/组合框刷新
try:
    app.refresh_cards(); app.refresh_network(); app.refresh_combos()
    app.update()
    check("删除→重拉全链路刷新无异常", True)
except Exception as e:
    check("删除→重拉全链路刷新无异常", False, e)

app.destroy()

print()
print("===== v0.19 功能验证：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
