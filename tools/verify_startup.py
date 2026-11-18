# -*- coding: utf-8 -*-
"""v0.69 启动优化专项验收；只使用 _tmp 下的新隔离数据库。"""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "_tmp" / "v069" / "startup_check"
RUN = BASE / ("run_" + time.strftime("%Y%m%d_%H%M%S"))
RUN.mkdir(parents=True, exist_ok=False)
os.environ["JINBU_DATA_DIR"] = str(RUN / "data")
os.environ["JINBU_DB_PATH"] = str(RUN / "data" / "agent.db")
os.environ.pop("JINBU_SKIP_SEED", None)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import ui  # noqa: E402
import tkinter as tk  # noqa: E402

REPORT = BASE / "verification.json"
checks = []


def check(name, condition, detail=""):
    checks.append({"name": name, "passed": bool(condition), "detail": str(detail)})
    print(("[通过] " if condition else "[失败] ") + name +
          ((" | " + str(detail)) if detail else ""), flush=True)
    if not condition:
        raise AssertionError(name)


app = None
result = {"status": "running", "work_dir": str(RUN)}
try:
    t0 = time.perf_counter()
    phases = []
    original_progress = ui.App._startup_progress

    def record_progress(self, text, percent):
        phases.append({"text": text, "percent": percent})
        return original_progress(self, text, percent)

    ui.App._startup_progress = record_progress
    connector_builds = []
    original_connector = ui.App.build_connector

    def counted_connector(self):
        connector_builds.append(time.perf_counter())
        return original_connector(self)

    ui.App.build_connector = counted_connector

    # 保持真实 Tk 尺寸计算，但不在主桌面弹出测试窗口。
    original_init = tk.Tk.__init__

    def hidden_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.withdraw()

    tk.Tk.__init__ = hidden_init
    t1 = time.perf_counter()
    app = ui.App()
    t2 = time.perf_counter()
    app.update_idletasks()
    t3 = time.perf_counter()
    pending = dict(app._lazy_pages)

    check("加载页包含数据库与首页两个真实阶段",
          any("本地数据" in p["text"] for p in phases)
          and any("关系首页" in p["text"] for p in phases), phases)
    check("首页完成即撤掉加载页", app._startup is None)
    check("关系首页已构建", "web_canvas" in vars(app) and len(app.node_pos) >= 1)
    check("其余四页均未提前构建", len(pending) == 4, list(pending))
    check("连接器启动阶段未执行", not connector_builds and "conn_grid" not in vars(app))

    app.refresh_cards()
    check("首页刷新不会反向构建卡片页", "cards_frame" not in vars(app))

    # 旧脚本直接访问控件时仍兼容，但不改变当前选中页。
    current = app.nb.select()
    gr_text = app.gr_text
    check("直接访问祝福语控件会按需构建", gr_text is not None
          and str(app.tab_greet) not in app._lazy_pages and app.nb.select() is current)

    person = db.list_persons()[0]
    app.go_gift(person["id"])
    check("首页跳转送礼会自动构建并选中人物",
          app.nb.select() is app.tab_gift and app.g_person.get() == person["name"])

    app.nb.select(app.tab_cards)
    check("首次选择卡片页自动构建", "cards_frame" in vars(app)
          and str(app.tab_cards) not in app._lazy_pages)

    app.nb.select(app.tab_conn)
    check("首次选择连接器才构建沙盒与联系人网格",
          len(connector_builds) == 1 and len(app.sandbox) == 16
          and str(app.tab_conn) not in app._lazy_pages)
    app.nb.select(app.tab_net)
    app.nb.select(app.tab_conn)
    check("重复切换连接器不重复构建", len(connector_builds) == 1)
    check("全部页签最终均可正常使用", not app._lazy_pages)

    total = t3 - t0
    result.update({
        "import_s": round(t1 - t0, 3),
        "app_including_db_and_home_s": round(t2 - t1, 3),
        "first_layout_s": round(t3 - t2, 3),
        "python_total_to_home_s": round(total, 3),
        "baseline_python_total_s": 6.046,
        "reduction_percent": round((6.046 - total) / 6.046 * 100, 1),
    })
    # 单次 Wall time 可能受杀毒、桌面窗口站等系统抖动影响；功能验收不
    # 把单次离群值误判为代码回退。性能结论使用多次独立进程的中位数。
    print("[计时] 本次首页冷启动 %.3f 秒（性能结论取多次中位数）" % total,
          flush=True)
    result["status"] = "passed"
except Exception as ex:
    result.update({"status": "failed", "error": str(ex)})
    raise
finally:
    result["checks"] = checks
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    (RUN / "verification.json").write_text(payload, encoding="utf-8")
    REPORT.write_text(payload, encoding="utf-8")
    if app is not None:
        try:
            app.destroy()
        except Exception:
            pass
    try:
        for conn in list(getattr(db._LOCAL, "conns", {}).values()):
            object.__getattribute__(conn, "_conn").close()
        db._LOCAL.conns = {}
    except Exception:
        pass
    print(json.dumps(result, ensure_ascii=False), flush=True)
