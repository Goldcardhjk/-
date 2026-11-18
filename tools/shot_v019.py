# -*- coding: utf-8 -*-
"""v0.19 截图验证：删除人物后连接器显示「拉取」按钮（可重新导入）

三连拍：
  _v019_conn_before.png    连接器基线（全部已拉取）
  _v019_conn_after_del.png 删除一位人物后（该卡片应显示绿色「拉取」按钮）
  _v019_conn_reimport.png  重新拉取后（恢复「已拉取」）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import db
db.init_db()

from tools.shot import grab
from ui import App

app = App()
app.geometry("1600x920+4000+4000")
app.update()
app.refresh_network()
app.update()

_deleted = {"name": None}


def shot(path):
    try:
        grab(app.winfo_id(), path)
        print("SHOT:", path)
    except Exception as e:
        print("SHOT-ERR:", e)


def step1():
    """基线：连接器页"""
    app.nb.select(app.tab_conn)
    app.update()
    app.after(900, lambda: (shot("_v019_conn_before.png"), step2()))


def step2():
    """删除第一位人物 → 同步连接器"""
    p = db.list_persons()[0]
    _deleted["name"] = p["name"]
    db.delete_person(p["id"])
    app._conn_after_person_delete(p["name"])
    app.update()
    app.after(900, lambda: (shot("_v019_conn_after_del.png"), step3()))


def step3():
    """重新拉取还原"""
    app.import_one(_deleted["name"])
    app.update()
    app.after(900, lambda: (shot("_v019_conn_reimport.png"), app.destroy()))


app.after(600, step1)
app.mainloop()
print("done:", _deleted["name"])
