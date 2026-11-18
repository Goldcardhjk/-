"""v0.57 多账号隔离验收（独立进程，不接触真实账号与生活样例库）。

需要外部指定数据目录：JINBU_DATA_DIR 指向 _tmp/v058/accounts_check。
每个账号一份 SQLite，验证切换后人物与记忆互不可见。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("JINBU_DATA_DIR", ""))
assert DATA and ROOT.drive.upper() == "D:" and "v058" in str(DATA), "必须指定 D 盘 _tmp/v058 下的独立数据目录"
sys.path.insert(0, str(ROOT))

import db

REPORT = DATA / "verification.json"
checks = []


def check(name, ok):
    checks.append({"name": name, "passed": bool(ok)})
    print(("[通过] " if ok else "[失败] ") + name, flush=True)
    if not ok:
        raise AssertionError(name)


def run():
    # 每次从干净状态开始，避免上一轮残留账号影响断言。
    accounts_file = DATA / "accounts.json"
    if accounts_file.exists():
        accounts_file.unlink()
    import shutil
    shutil.rmtree(DATA / "accounts", ignore_errors=True)
    check("数据目录落在独立测试区", Path(db.DB_PATH).parent == DATA and not db.FIXED_DB)
    alice = db.register_account("甲账号")
    bob = db.register_account("乙账号")
    check("两个账号各自建号", alice["slug"] != bob["slug"] and len(db.list_accounts()) == 2)
    a_db, b_db = Path(db.account_db_path(alice["slug"])), Path(db.account_db_path(bob["slug"]))
    db.switch_account(alice["slug"])
    conn = db.get_conn()
    cur = conn.execute("INSERT INTO persons (name, closeness, notes) VALUES ('甲的独有联系人', 0.9, '只属于甲')")
    pid = cur.lastrowid
    conn.execute("INSERT INTO interactions (person_id, type, date, note) VALUES (?, '见面', '2026-11-02', '甲的私密记录')", (pid,))
    conn.commit()
    alice_names = [p["name"] for p in db.list_persons()]
    alice_notes = [i["note"] for i in db.list_interactions()]
    check("甲账号写入人物与互动记录", "甲的独有联系人" in alice_names
          and "甲的私密记录" in alice_notes)
    db.switch_account(bob["slug"])
    bob_names = [p["name"] for p in db.list_persons()]
    bob_notes = [i["note"] for i in db.list_interactions()]
    check("切到乙账号后完全看不到甲的人物与记忆",
          "甲的独有联系人" not in bob_names and "甲的私密记录" not in bob_notes
          and Path(db.DB_PATH) == b_db)
    conn = db.get_conn()
    conn.execute("INSERT INTO persons (name, closeness) VALUES ('乙的独有联系人', 0.3)")
    conn.commit()
    check("乙账号自己的数据独立写入", "乙的独有联系人" in [p["name"] for p in db.list_persons()])
    db.switch_account(alice["slug"])
    check("切回甲账号后原有记忆完整",
          [p["name"] for p in db.list_persons()] == alice_names
          and [i["note"] for i in db.list_interactions()] == alice_notes
          and Path(db.DB_PATH) == a_db)
    check("两份库文件真实存在且互不相同", a_db.is_file() and b_db.is_file() and a_db != b_db)


report = {"version": "v058", "checks": checks, "data_dir": str(DATA),
          "accounts": str(DATA / "accounts.json")}
try:
    run()
    report["status"] = "passed"
except BaseException as exc:
    report["status"] = "failed"
    report["error"] = str(exc)
    raise
finally:
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": len(checks),
                      "report": str(REPORT)}, ensure_ascii=False), flush=True)
