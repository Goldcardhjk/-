"""进步壹号 Agent —— 本地数据库层 (SQLite, 存于 D 盘)

表结构:
  persons        人物档案 (关系/社交价值维度/喜好/禁忌/收货地址/重要日期)
  interactions   互动记录 (用于互惠度与亲密度计算)
  gift_catalog   礼品库 (模拟)
  gift_prices    多商家模拟比价
  gift_orders    送礼订单 (待付款 -> 模拟已付款)
"""

import os
import sqlite3
import json
import hashlib
import sys
import threading
from datetime import datetime, date as calendar_date

# v0.21：线程本地 sqlite 连接（get_conn 复用）——连接不可跨线程，
# 每线程首次 get_conn 时自建，此后该线程所有短事务查询零 connect 开销
_LOCAL = threading.local()

# 数据目录定位：开发时跟随源码目录；打包成 exe 后存到 exe 所在目录，
# 避免写进 C 盘临时解压目录（每次运行数据不保留）
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
# 数据目录可由 JINBU_DATA_DIR 指定（样例/自检用），否则用程序目录下的 data。
# 测试样例还可显式指定独立 SQLite 文件；未设置时用 data/agent.db。
BASE_DATA = os.path.abspath(os.environ.get("JINBU_DATA_DIR", os.path.join(BASE, "data")))
DB_PATH = os.path.abspath(os.environ.get("JINBU_DB_PATH", os.path.join(BASE_DATA, "agent.db")))
DATA_DIR = os.path.dirname(DB_PATH)

# ---------------- 多账号隔离（v0.57） ----------------
# 每个账号一份 SQLite：默认账号沿用 data/agent.db（老数据不搬），
# 新账号放在 data/accounts/<slug>/agent.db。任务记忆天然互不可见。
ACCOUNTS_PATH = os.path.join(BASE_DATA, "accounts.json")
FIXED_DB = bool(os.environ.get("JINBU_DB_PATH"))     # 样例/测试库不启用账号切换


def _write_accounts(rows):
    try:
        os.makedirs(os.path.dirname(ACCOUNTS_PATH) or ".", exist_ok=True)
        with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _account_rows():
    try:
        with open(ACCOUNTS_PATH, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("slug") and r.get("name")]


def list_accounts():
    """本机已有账号；样例/测试库模式返回空，避免误切真实数据。"""
    return [] if FIXED_DB else _account_rows()


def account_db_path(slug):
    if slug == "default":
        return os.path.join(BASE_DATA, "agent.db")
    return os.path.join(BASE_DATA, "accounts", slug, "agent.db")


def bootstrap_accounts():
    """首次运行：把已有单人数据登记成「默认账号」，不搬库也不丢数据。"""
    if FIXED_DB or _account_rows():
        return _account_rows()
    if not os.path.exists(account_db_path("default")):
        return []
    user = get_user() or {}
    name = (user.get("name") or "我的账号").strip() or "我的账号"
    rows = [{"slug": "default", "name": name,
             "avatar": user.get("avatar") or "", "created": ""}]
    _write_accounts(rows)
    return rows


def register_account(name, avatar=None):
    """按昵称新建（或复用）账号，返回账号记录；真正的切换用 switch_account。"""
    name = (name or "").strip() or "我的账号"
    slug = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    rows = _account_rows()
    row = next((r for r in rows if r["slug"] == slug), None)
    if row is None:
        row = {"slug": slug, "name": name, "avatar": avatar or "",
               "created": datetime.now().strftime("%Y-%m-%d %H:%M")}
        rows.append(row)
        _write_accounts(rows)
    elif avatar and row.get("avatar") != avatar:
        row["avatar"] = avatar
        _write_accounts(rows)
    os.makedirs(os.path.dirname(account_db_path(slug)), exist_ok=True)
    return row


def switch_account(slug):
    """切到该账号的库：先断开旧库连接，再按新路径重建表。"""
    global DB_PATH, DATA_DIR
    if FIXED_DB:
        return DB_PATH
    for conn in list(getattr(_LOCAL, "conns", {}).values()):
        try:
            object.__getattribute__(conn, "_conn").close()
        except Exception:
            pass
    _LOCAL.conns = {}
    DB_PATH = os.path.abspath(account_db_path(slug))
    DATA_DIR = os.path.dirname(DB_PATH)
    os.environ["JINBU_ACCOUNT"] = slug
    fresh = not os.path.exists(DB_PATH)
    # 新账号从空白开始，不灌演示人物；老账号/默认账号保持原有数据。
    if fresh:
        os.environ["JINBU_SKIP_SEED"] = "1"
    try:
        init_db()
    finally:
        if fresh:
            os.environ.pop("JINBU_SKIP_SEED", None)
    return DB_PATH


def current_account():
    return os.environ.get("JINBU_ACCOUNT") or "default"


# ---------------- 数据备份 / 恢复（v0.61） ----------------
def backup_database(dest_path):
    """把当前账号的 SQLite 库整体复制到 dest_path（先提交落盘）。"""
    import shutil
    conn = get_conn()
    try:
        conn.commit()
    except Exception:
        pass
    parent = os.path.dirname(os.path.abspath(dest_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    shutil.copyfile(DB_PATH, dest_path)
    return os.path.abspath(dest_path)


def restore_backup(src_path):
    """用备份文件整体覆盖当前账号的库：先断开全部缓存连接再复制。

    连接代理按文件签名自检外部替换，但读路径不触发自检；这里与
    switch_account 一样显式清空连接缓存，保证恢复后读写都落到新文件。
    """
    import shutil
    if not os.path.isfile(src_path):
        raise FileNotFoundError(src_path)
    for conn in list(getattr(_LOCAL, "conns", {}).values()):
        try:
            object.__getattribute__(conn, "_conn").close()
        except Exception:
            pass
    _LOCAL.conns = {}
    shutil.copyfile(src_path, DB_PATH)
    init_db()
    return DB_PATH

RELATION_TYPES = ["家人", "挚友", "重要合作伙伴", "同事", "普通朋友", "弱关系"]
RELATION_LINK_TYPES = ["引荐", "家人", "挚友", "合作伙伴", "同事", "认识"]

# v0.63：使用场景卡。场景是「我现在看谁」的视图过滤，可多选；
# 与「账号」（谁在用，物理隔离）是两个层级——场景卡做在账号内部。
# v0.65：场景改为账号内可自定义（scenes 表）。SCENES 只用作首次初始化时的
# 预设名，不再是运行时数据源；运行时读场景一律走 list_scenes()。
# 预设场景同样可改名、可删除，与普通场景没有权限差别。
SCENES = ["办公", "学校", "家庭"]
BUILTIN_SCENE = "全部"            # 虚拟视图：不进表、不可改删

# 关系类型 → 默认场景（新增人物时自动挂上，资料卡里可改）
REL_TO_SCENE = {
    "家人": "家庭",
    "挚友": "学校",
    "同学": "学校",
    "同学/校友": "学校",
    "重要合作伙伴": "办公",
    "同事": "办公",
    "普通朋友": "学校",
    "弱关系": "办公",
}


def list_scenes():
    """当前账号的场景名列表（按自定义顺序）。不含内置「全部」。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM scenes ORDER BY sort_order, name").fetchall()
        return [r["name"] for r in rows]
    except Exception:
        return list(SCENES)
    finally:
        conn.close()


def _raw_scenes(raw):
    """只读原始 JSON 数组，不做有效性过滤（改名/删除迁移时用）。"""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [s for s in v if isinstance(s, str)] if isinstance(v, list) else []
    except Exception:
        return []


def default_scenes(relation_type, valid=None):
    """按关系类型给默认场景标签。默认场景被用户删掉时回落到第一个现存场景。"""
    valid = valid if valid is not None else list_scenes()
    s = REL_TO_SCENE.get(relation_type)
    if s:
        return [s] if s in valid else (valid[:1] or [])
    return list(valid) or []      # 未知类型：挂到全部现存场景


def _validate_scene_name(name, conn, exclude=None):
    name = (name or "").strip()
    if not name:
        return None, "场景名不能为空"
    if name == BUILTIN_SCENE:
        return None, "「全部」是内置视图，不能用这个名字"
    if len(name) > 12:
        return None, "场景名请控制在 12 个字以内"
    row = conn.execute("SELECT 1 FROM scenes WHERE name=?", (name,)).fetchone()
    if row and name != exclude:
        return None, "已有同名场景"
    return name, None


def add_scene(name):
    """新增自定义场景。返回 (是否成功, 场景名或错误原因)"""
    conn = get_conn()
    name, err = _validate_scene_name(name, conn)
    if err:
        conn.close()
        return False, err
    nxt = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM scenes").fetchone()[0]
    conn.execute("INSERT INTO scenes (name, sort_order, is_preset) VALUES (?, ?, 0)",
                 (name, nxt))
    conn.commit()
    conn.close()
    return True, name


def rename_scene(old, new):
    """改名：同步迁移所有人物标签与「上次场景」，预设改后仍可继续编辑。"""
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM scenes WHERE name=?", (old,)).fetchone():
        conn.close()
        return False, "场景不存在"
    new, err = _validate_scene_name(new, conn, exclude=old)
    if err:
        conn.close()
        return False, err
    if new == old:
        conn.close()
        return True, new
    # 改名前先记下旧场景全集：旧库里很多人没存 scenes 字段，
    # 靠关系类型默认映射显示，迁移时必须按「改名前的有效场景」补齐。
    before = [r["name"] for r in conn.execute(
        "SELECT name FROM scenes ORDER BY sort_order, name").fetchall()]
    conn.execute("UPDATE scenes SET name=? WHERE name=?", (new, old))
    moved = 0
    for r in conn.execute("SELECT id, scenes, relation_type FROM persons").fetchall():
        cur = _raw_scenes(r["scenes"]) or default_scenes(r["relation_type"], before)
        if old in cur:
            upd = [new if s == old else s for s in cur]
            conn.execute("UPDATE persons SET scenes=? WHERE id=?",
                         (json.dumps(upd, ensure_ascii=False), r["id"]))
            moved += 1
    conn.execute("UPDATE user_profile SET scene=? WHERE scene=?", (new, old))
    conn.commit()
    conn.close()
    return True, "%s（已迁移 %d 人）" % (new, moved)


def delete_scene(name):
    """删除场景：从所有人物标签移除；标签空了则回落到默认/首个现存场景。
    当前正在看的场景被删时自动回到「全部」。返回受影响人数。
    """
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM scenes WHERE name=?", (name,)).fetchone():
        conn.close()
        return 0
    # 同上：删除前先取旧场景全集，补齐靠默认映射显示的人。
    before = [r["name"] for r in conn.execute(
        "SELECT name FROM scenes ORDER BY sort_order, name").fetchall()]
    conn.execute("DELETE FROM scenes WHERE name=?", (name,))
    valid = [r["name"] for r in conn.execute(
        "SELECT name FROM scenes ORDER BY sort_order, name").fetchall()]
    changed = 0
    for r in conn.execute(
            "SELECT id, scenes, relation_type FROM persons").fetchall():
        cur = _raw_scenes(r["scenes"]) or default_scenes(r["relation_type"], before)
        if name not in cur:
            continue
        left = [s for s in cur if s != name and s in valid]
        if not left:
            left = ([s for s in default_scenes(r["relation_type"], valid)
                     if s in valid][:1] or valid[:1])
        conn.execute("UPDATE persons SET scenes=? WHERE id=?",
                     (json.dumps(left, ensure_ascii=False), r["id"]))
        changed += 1
    conn.execute("UPDATE user_profile SET scene=? WHERE scene=?",
                 (BUILTIN_SCENE, name))
    conn.commit()
    conn.close()
    return changed


def move_scene(name, delta):
    """上移 delta=-1 / 下移 delta=+1，重排场景卡顺序。"""
    conn = get_conn()
    names = [r["name"] for r in conn.execute(
        "SELECT name FROM scenes ORDER BY sort_order, name").fetchall()]
    if name not in names:
        conn.close()
        return False
    i, j = names.index(name), names.index(name) + delta
    if not (0 <= j < len(names)):
        conn.close()
        return False
    names[i], names[j] = names[j], names[i]
    for k, n in enumerate(names):
        conn.execute("UPDATE scenes SET sort_order=? WHERE name=?", (k, n))
    conn.commit()
    conn.close()
    return True


def scene_people_count(name):
    """挂了该场景的人数（删除确认框用）"""
    n = 0
    for r in list_persons():
        if name in (r.get("scenes") or []):
            n += 1
    return n


def normalize_link_relation(relation):
    """兼容历史泛化合作类型；读取展示时不改写原记录。"""
    return "合作伙伴" if relation == "合作" else relation


def get_conn():
    """v0.21：线程本地连接复用——短事务查询（get_conn→execute→close）
    不再重复 connect/mkdir。close() 经代理只回滚未提交事务、不断开，
    主线程刷新链路（一次刷新上百次查询）连接开销归零。
    每线程独立连接（sqlite3 连接不可跨线程），后台线程首次用时自建。
    """
    conns = getattr(_LOCAL, "conns", None)
    if conns is None:
        conns = {}
        _LOCAL.conns = conns
    # 按库路径缓存：切换账号后旧连接不会再被复用。
    conn = conns.get(DB_PATH)
    if conn is None:
        if not os.path.isdir(DATA_DIR):
            os.makedirs(DATA_DIR, exist_ok=True)
        raw = sqlite3.connect(DB_PATH)
        raw.row_factory = sqlite3.Row
        conn = _ConnProxy(raw)
        conns[DB_PATH] = conn
    return conn


class _ConnProxy:
    """sqlite3 连接的复用代理：属性/方法透传；close() 只回滚不断开。

    保留调用方「execute→commit→close」旧书写习惯的同时让连接常驻
    线程本地。写路径照旧显式 commit（现有代码均已 commit），未提交
    事务在 close 时安全丢弃，语义与旧实现一致。

    v0.21 文件替换自愈：连接常驻期间若 DB 文件被外部替换（备份恢复 /
    导入 / bench 清理的 copy2 覆盖），持有的旧 inode 会变成「幽灵数据」
    （读写落到已替换的旧文件，用户看到的库却是新文件）。每次写路径
    （execute/commit 等）前比对 DB 文件签名（mtime+size），变了就
    关旧连、重连到新文件。
    """

    _WRITE_OPS = ("execute", "executemany", "executescript", "commit")

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_sig", _db_sig())

    def _ensure(self):
        conn = object.__getattribute__(self, "_conn")
        # 事务期间绝不因其他线程/进程提交造成的文件签名变化而重连，
        # 否则会把 BEGIN IMMEDIATE 后的事务中途丢弃。
        if conn.in_transaction:
            return conn
        try:
            sig = _db_sig()
        except OSError:
            return conn
        if sig != object.__getattribute__(self, "_sig"):
            try:
                conn.close()
            except Exception:
                pass
            raw = sqlite3.connect(DB_PATH)
            raw.row_factory = sqlite3.Row
            conn = raw
            object.__setattr__(self, "_conn", conn)
            object.__setattr__(self, "_sig", _db_sig())
        return conn

    def __getattr__(self, name):
        conn = object.__getattribute__(self, "_conn")
        attr = getattr(conn, name)
        if name in _ConnProxy._WRITE_OPS:
            def wrapper(*a, **kw):
                # cursor 的写入会先改变文件签名；提交时不能把自己的写入
                # 当作外部替换而关闭连接，否则新库迁移的 cursor 随即失效。
                c = (object.__getattribute__(self, "_conn")
                     if name == "commit" else self._ensure())
                result = getattr(c, name)(*a, **kw)
                object.__setattr__(self, "_sig", _db_sig())
                return result
            return wrapper
        return attr

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_conn"), name, value)

    def close(self):
        try:
            if object.__getattribute__(self, "_conn").in_transaction:
                object.__getattribute__(self, "_conn").rollback()
        except Exception:
            pass


def _db_sig():
    """DB 文件签名（mtime_ns + size）——外部替换检测用"""
    st = os.stat(DB_PATH)
    return (st.st_mtime_ns, st.st_size)


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            relation_type TEXT DEFAULT '普通朋友',
            closeness REAL DEFAULT 0.5,
            sentiment_weight REAL DEFAULT 0.5,
            intro_by TEXT,
            address TEXT,
            phone TEXT,
            birthday TEXT,
            important_dates TEXT DEFAULT '[]',
            prefs TEXT DEFAULT '[]',
            taboo TEXT DEFAULT '[]',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            type TEXT,
            date TEXT,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS gift_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            category TEXT,
            base_price REAL,
            tags TEXT DEFAULT '[]',
            suitable TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS gift_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gift_id INTEGER,
            merchant TEXT,
            price REAL
        );
        CREATE TABLE IF NOT EXISTS gift_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            gift_id INTEGER,
            gift_name TEXT,
            scheduled_date TEXT,
            amount REAL,
            status TEXT DEFAULT '待付款',
            paid_at TEXT,
            greeting_text TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_a INTEGER,
            person_b INTEGER,
            relation TEXT DEFAULT '认识',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    # 轻量迁移：新增字段（source=来源, avatar=形象图片路径, pos=节点位置, memo_md=记忆笔记）
    for ddl in ("ALTER TABLE persons ADD COLUMN source TEXT DEFAULT 'manual'",
                "ALTER TABLE persons ADD COLUMN avatar TEXT",
                "ALTER TABLE persons ADD COLUMN pos_x REAL",
                "ALTER TABLE persons ADD COLUMN pos_y REAL",
                "ALTER TABLE persons ADD COLUMN memo_md TEXT DEFAULT ''",
                # v0.5：人物间连接的关系分数（0-100，驱动连线颜色/长短/粗细）
                "ALTER TABLE links ADD COLUMN score INTEGER DEFAULT 50",
                # v0.62 支付域：订单生成通道（mock/vibe）+ 支付宝交易号（回查/对账）
                "ALTER TABLE gift_orders ADD COLUMN channel TEXT DEFAULT 'mock'",
                "ALTER TABLE gift_orders ADD COLUMN pay_ref TEXT DEFAULT ''",
                # v0.63 使用场景卡：人物场景标签（JSON 数组，可多选）
                "ALTER TABLE persons ADD COLUMN scenes TEXT DEFAULT ''"):
        try:
            c.execute(ddl); conn.commit()
        except Exception:
            pass
    # v0.5：本地账号（"我"是谁）
    c.execute("""CREATE TABLE IF NOT EXISTS user_profile (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    name TEXT NOT NULL,
                    avatar TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )""")
    # v0.65：账号内自定义场景 + 一次性 seed 标记。
    # 每个账号一个独立库，场景定义天然按账号隔离；预设场景也可改可删。
    c.execute("""CREATE TABLE IF NOT EXISTS scenes (
                    name TEXT PRIMARY KEY,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    is_preset INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )""")
    c.execute("""CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )""")
    # v0.68 积分域：用户买积分（钱→商户账户）→ 积分入用户账户 →
    # 消耗积分调用模型分析。账本只追加，余额=SUM(delta)。
    c.execute("""CREATE TABLE IF NOT EXISTS points_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_key TEXT NOT NULL,
                    delta INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    reason TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    ref_trade_no TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now'))
                )""")
    # 积分购买订单与送礼订单分离：商品、积分、商户单号、支付状态各自可追溯。
    c.execute("""CREATE TABLE IF NOT EXISTS points_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_key TEXT NOT NULL DEFAULT 'local',
                    product_key TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    out_trade_no TEXT NOT NULL UNIQUE,
                    channel TEXT NOT NULL DEFAULT 'vibe',
                    status TEXT NOT NULL DEFAULT '待付款',
                    pay_ref TEXT NOT NULL DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    paid_at TEXT
                )""")
    # 非空交易号全库唯一；重复查单/双击/并发入账只能产生一条流水。
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_points_ledger_trade
                 ON points_ledger(ref_trade_no) WHERE ref_trade_no <> ''""")
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_points_orders_pay_ref
                 ON points_orders(pay_ref) WHERE pay_ref <> ''""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_points_orders_user
                 ON points_orders(user_key, id)""")
    # 只在首次初始化写预设；用户删光全部场景后重启不会把它们复活。
    if not c.execute("SELECT 1 FROM app_meta WHERE key='scenes_seeded'").fetchone():
        for i, _sc in enumerate(SCENES):
            c.execute("INSERT OR IGNORE INTO scenes (name, sort_order, is_preset) "
                      "VALUES (?, ?, 1)", (_sc, i))
        c.execute("INSERT OR REPLACE INTO app_meta (key, value) "
                  "VALUES ('scenes_seeded', '1')")
    # v0.63 场景卡：记住该账号上次看的是哪个场景（全部/办公/学校/家庭）
    for ddl in ("ALTER TABLE user_profile ADD COLUMN scene TEXT DEFAULT ''",):
        try:
            c.execute(ddl); conn.commit()
        except Exception:
            pass
    conn.commit()
    # 独立生活化样例可显式跳过默认演示人物与互动；正常运行保持原有种子数据。
    if not os.environ.get("JINBU_SKIP_SEED"):
        seed_if_empty(conn)
    _mirror_intro_links(conn)
    conn.close()


# ---------------------- 本地账号（"我"） ----------------------
def get_user():
    """返回当前登录账号 dict(name/avatar/scene) 或 None（未登录）"""
    conn = get_conn()
    row = conn.execute("SELECT name, avatar, scene FROM user_profile WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else None


def save_scene(scene):
    """v0.63：记住当前账号正在看的使用场景（重启后自动恢复）"""
    scene = scene if scene in (list_scenes() + [BUILTIN_SCENE, ""]) else BUILTIN_SCENE
    conn = get_conn()
    conn.execute("UPDATE user_profile SET scene=? WHERE id=1", (scene,))
    conn.commit()
    conn.close()


def save_user(name, avatar=None):
    """保存/更新本地账号（幂等，单行）"""
    conn = get_conn()
    conn.execute("""INSERT INTO user_profile (id, name, avatar) VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name, avatar=excluded.avatar""",
                 (name, avatar))
    conn.commit()
    conn.close()
    return {"name": name, "avatar": avatar}


def clear_user():
    """退出登录：清除本地账号"""
    conn = get_conn()
    conn.execute("DELETE FROM user_profile WHERE id=1")
    conn.commit()
    conn.close()


def _mirror_intro_links(conn):
    """把旧版 intro_by 引荐关系镜像成 links 连接（幂等，不重复插入；
    若两人之间已有任何连接则跳过，避免同对画多重线）"""
    c = conn.cursor()
    name2id = {r["name"]: r["id"]
               for r in c.execute("SELECT id, name FROM persons").fetchall()}
    rows = c.execute(
        "SELECT id, intro_by FROM persons WHERE intro_by IS NOT NULL AND intro_by != ''"
    ).fetchall()
    for r in rows:
        a, b = name2id.get(r["intro_by"]), r["id"]
        if not a or not b or a == b:
            continue
        if c.execute("SELECT 1 FROM links WHERE (person_a=? AND person_b=?) "
                     "OR (person_a=? AND person_b=?)", (a, b, b, a)).fetchone():
            continue
        c.execute("INSERT INTO links (person_a, person_b, relation) VALUES (?,?,?)",
                  (a, b, "引荐"))
    conn.commit()


# ---------------------- 种子数据 ----------------------
def seed_if_empty(conn):
    c = conn.cursor()
    if c.execute("SELECT COUNT(*) FROM persons").fetchone()[0] > 0:
        return

    # 示例人物
    sample_persons = [
        ("王教授", "重要合作伙伴", 0.85, 0.8, "李总", "北京市海淀区中关村南大街5号 王老师收 13800001111",
         "13800001111", "1972-09-10",
         json.dumps([{"label": "合作周年", "date": "2026-11-20"}]),
         json.dumps(["茶", "书法", "历史书", "核桃"]),
         json.dumps(["酒", "烟"]),
         "带我入门学术圈的贵人，每年教师节/生日都要顾到。"),
        ("小琳", "挚友", 0.9, 0.9, "", "上海市徐汇区漕溪北路100号 小琳收 13900002222",
         "13900002222", "1995-03-18",
         json.dumps([{"label": "闺蜜日", "date": "2026-12-01"}]),
         json.dumps(["香薰", "手账", "草莓", "盲盒"]),
         json.dumps(["香菜"]),
         "大学室友，无话不谈，礼物要走心小而美。"),
        ("张阿姨", "家人", 1.0, 0.95, "", "广州市天河区体育西路 张阿姨收 13700003333",
         "13700003333", "1958-05-02",
         json.dumps([]),
         json.dumps(["养生", "丝绸", "坚果", "保暖"]),
         json.dumps(["含糖高"]),
         "长辈，过节必须到有实物到家。"),
        ("陈学长", "普通朋友", 0.5, 0.5, "王教授", "深圳市南山区科技园 陈学长收 13600004444",
         "13600004444", "1990-07-25",
         json.dumps([]),
         json.dumps(["数码", "咖啡", "机械键盘"]),
         json.dumps([]),
         "同门师兄，偶尔请教，维持弱联系即可。"),
    ]
    for p in sample_persons:
        c.execute(
            """INSERT INTO persons
               (name, relation_type, closeness, sentiment_weight, intro_by, address, phone,
                birthday, important_dates, prefs, taboo, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            p,
        )

    # 互动/送礼记录（用于互惠度演示）
    c.execute("INSERT INTO interactions (person_id, type, date, note) VALUES (1,'送出礼物','2025-09-10','教师节茶叶')")
    c.execute("INSERT INTO interactions (person_id, type, date, note) VALUES (1,'收到礼物','2025-10-01','赠书')")
    c.execute("INSERT INTO interactions (person_id, type, date, note) VALUES (2,'送出礼物','2025-03-18','手账礼盒')")
    c.execute("INSERT INTO interactions (person_id, type, date, note) VALUES (3,'送出礼物','2025-05-02','丝绸围巾')")

    # 礼品库 + 模拟比价
    catalog = [
        ("武夷山岩茶礼盒", "茶/饮品", 268, ["茶", "养生", "长辈"], ["长辈", "合作伙伴", "男性"]),
        ("手冲咖啡套装", "数码/生活", 199, ["咖啡", "数码"], ["朋友", "男性", "同事"]),
        ("真丝睡衣", "服饰/家居", 329, ["丝绸", "保暖", "养生"], ["长辈", "女性", "家人"]),
        ("香薰蜡烛礼盒", "香氛", 159, ["香薰", "放松"], ["女性", "朋友", "挚友"]),
        ("机械键盘", "数码", 459, ["数码", "机械键盘"], ["男性", "同事", "朋友"]),
        ("坚果养生礼盒", "食品", 128, ["坚果", "养生"], ["长辈", "家人", "所有"]),
        ("手账本套装", "文具", 89, ["手账", "文具"], ["女性", "朋友", "挚友"]),
        ("草莓鲜花花束", "鲜花/食品", 99, ["草莓", "鲜花"], ["女性", "朋友", "挚友"]),
        ("保温杯", "生活", 119, ["保暖", "养生"], ["长辈", "同事", "家人"]),
        ("盲盒公仔", "潮玩", 69, ["盲盒", "潮玩"], ["女性", "朋友"]),
        ("书法毛笔套装", "文具/文化", 188, ["书法", "历史书"], ["长辈", "合作伙伴", "男性"]),
        ("护手霜礼盒", "护肤", 139, ["护肤", "保暖"], ["女性", "同事", "家人"]),
    ]
    merchants = ["京东自营", "淘宝心选", "拼多多百亿补贴", "天猫超市"]
    for name, cat, base, tags, suitable in catalog:
        cur = c.execute(
            "INSERT INTO gift_catalog (name, category, base_price, tags, suitable) VALUES (?,?,?,?,?)",
            (name, cat, base, json.dumps(tags), json.dumps(suitable)),
        )
        gid = cur.lastrowid
        # 每个商家在 base 上下浮动，制造比价空间
        import random
        random.seed(gid)
        for m in merchants:
            delta = random.uniform(-0.18, 0.12)
            price = round(base * (1 + delta), 1)
            c.execute("INSERT INTO gift_prices (gift_id, merchant, price) VALUES (?,?,?)", (gid, m, price))
    conn.commit()


# ---------------------- CRUD ----------------------
def list_persons():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM persons ORDER BY name").fetchall()
    conn.close()
    valid = list_scenes()          # 一次取，避免逐人查库
    out = []
    for r in rows:
        d = dict(r)
        d["scenes"] = _parse_scenes(d.get("scenes"), d.get("relation_type"), valid)
        out.append(d)
    return out


def _parse_scenes(raw, relation_type, valid=None):
    """scenes 字段 → 有效场景列表。空/失效则按关系类型给默认场景（只在内存补）。"""
    valid = valid if valid is not None else list_scenes()
    got = [s for s in _raw_scenes(raw) if s in valid]
    return got or default_scenes(relation_type, valid)


def _dump_scenes(scenes, relation_type):
    """场景标签 → 存库 JSON。空/失效则按关系类型默认映射（同步过滤已删场景）。"""
    valid = list_scenes()
    if not scenes:
        scenes = default_scenes(relation_type, valid)
    scenes = [s for s in scenes if s in valid] or default_scenes(relation_type, valid)
    return json.dumps(scenes, ensure_ascii=False)


def get_person(pid):
    conn = get_conn()
    r = conn.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(r) if r else None


def save_person(data, pid=None):
    conn = get_conn()
    if pid:
        conn.execute(
            """UPDATE persons SET name=?, relation_type=?, closeness=?, sentiment_weight=?,
               intro_by=?, address=?, phone=?, birthday=?, important_dates=?, prefs=?, taboo=?, notes=?, source=?, avatar=?, scenes=?
               WHERE id=?""",
            (data["name"], data["relation_type"], data["closeness"], data["sentiment_weight"],
             data["intro_by"], data["address"], data["phone"], data["birthday"],
             data["important_dates"], data["prefs"], data["taboo"], data["notes"],
             data.get("source", "manual"), data.get("avatar"),
             _dump_scenes(data.get("scenes"), data["relation_type"]), pid),
        )
    else:
        cur = conn.execute(
            """INSERT INTO persons
               (name, relation_type, closeness, sentiment_weight, intro_by, address, phone,
                birthday, important_dates, prefs, taboo, notes, source, avatar, scenes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (data["name"], data["relation_type"], data["closeness"], data["sentiment_weight"],
             data["intro_by"], data["address"], data["phone"], data["birthday"],
             data["important_dates"], data["prefs"], data["taboo"], data["notes"],
             data.get("source", "manual"), data.get("avatar"),
             _dump_scenes(data.get("scenes"), data["relation_type"])),
        )
        pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid


def set_avatar(pid, path):
    conn = get_conn()
    conn.execute("UPDATE persons SET avatar=? WHERE id=?", (path, pid))
    conn.commit()
    conn.close()


def set_degree(pid, closeness, sentiment):
    conn = get_conn()
    conn.execute("UPDATE persons SET closeness=?, sentiment_weight=? WHERE id=?", (closeness, sentiment, pid))
    conn.commit()
    conn.close()


def clear_position(pid):
    conn = get_conn()
    conn.execute("UPDATE persons SET pos_x=NULL, pos_y=NULL WHERE id=?", (pid,))
    conn.commit()
    conn.close()


def clear_all_positions():
    """清空所有手动位置，恢复自动放射布局"""
    conn = get_conn()
    conn.execute("UPDATE persons SET pos_x=NULL, pos_y=NULL")
    conn.commit()
    conn.close()


def delete_person(pid):
    conn = get_conn()
    conn.execute("DELETE FROM persons WHERE id=?", (pid,))
    conn.execute("DELETE FROM interactions WHERE person_id=?", (pid,))
    conn.execute("DELETE FROM gift_orders WHERE person_id=?", (pid,))
    conn.execute("DELETE FROM links WHERE person_a=? OR person_b=?", (pid, pid))
    conn.commit()
    conn.close()


def list_catalog():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM gift_catalog ORDER BY base_price").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def best_price(gift_id):
    conn = get_conn()
    rows = conn.execute("SELECT merchant, price FROM gift_prices WHERE gift_id=? ORDER BY price", (gift_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_order(person_id, gift_id, gift_name, scheduled_date, amount,
                 greeting_text="", channel="mock"):
    """建单（默认 待付款）。channel ∈ {'mock','vibe'}（v0.62 支付域）。"""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO gift_orders
           (person_id, gift_id, gift_name, scheduled_date, amount,
            greeting_text, channel)
           VALUES (?,?,?,?,?,?,?)""",
        (person_id, gift_id, gift_name, scheduled_date, amount,
         greeting_text, channel),
    )
    oid = cur.lastrowid
    conn.commit()
    conn.close()
    return oid


def mark_order_paid(oid, status, pay_ref=""):
    """订单状态迁移统一入口：落状态 + 支付流水号（pay_ref=支付宝 trade_no）。"""
    conn = get_conn()
    if pay_ref:
        conn.execute(
            "UPDATE gift_orders SET status=?, paid_at=datetime('now'), pay_ref=? "
            "WHERE id=?", (status, pay_ref, oid))
    else:
        conn.execute(
            "UPDATE gift_orders SET status=?, paid_at=datetime('now') WHERE id=?",
            (status, oid))
    conn.commit()
    conn.close()


def update_order_status(oid, status):
    """改单状态（不给 paid_at；vibe 查单前标「等待买家付款」等中间态用）。"""
    conn = get_conn()
    conn.execute("UPDATE gift_orders SET status=? WHERE id=?", (status, oid))
    conn.commit()
    conn.close()


def mock_pay(oid):
    """模拟付款：仅本地标记，不产生任何真实交易"""
    conn = get_conn()
    conn.execute("UPDATE gift_orders SET status='模拟已付款', paid_at=datetime('now') WHERE id=?", (oid,))
    conn.commit()
    conn.close()


def list_orders():
    conn = get_conn()
    rows = conn.execute(
        "SELECT o.*, p.name AS person_name FROM gift_orders o LEFT JOIN persons p ON o.person_id=p.id ORDER BY o.id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------- 人物连接关系 ----------------------
def add_link(a, b, relation="认识", note="", score=50):
    """建立 A→B 连接；同对（不分方向）同关系已存在则更新分数/备注并返回原 id（不重复）"""
    if not a or not b or a == b:
        return None
    relation = normalize_link_relation(relation)
    conn = get_conn()
    if relation == "合作伙伴":
        exists = conn.execute(
            """SELECT id FROM links WHERE relation IN ('合作', '合作伙伴')
               AND ((person_a=? AND person_b=?) OR (person_a=? AND person_b=?))
               ORDER BY id LIMIT 1""", (a, b, b, a)).fetchone()
    else:
        exists = conn.execute(
            """SELECT id FROM links WHERE relation=?
               AND ((person_a=? AND person_b=?) OR (person_a=? AND person_b=?))""",
            (relation, a, b, b, a)).fetchone()
    if exists:
        conn.execute("UPDATE links SET note=?, score=? WHERE id=?",
                     (note or "", int(score), exists["id"]))
        conn.commit()
        conn.close()
        return exists["id"]
    cur = conn.execute(
        "INSERT INTO links (person_a, person_b, relation, note, score) VALUES (?,?,?,?,?)",
        (a, b, relation, note or "", int(score)))
    conn.commit()
    lid = cur.lastrowid
    conn.close()
    return lid


def update_link_score(lid, score):
    """更新某条连接的关系分数（0-100）"""
    conn = get_conn()
    conn.execute("UPDATE links SET score=? WHERE id=?",
                 (max(0, min(100, int(score))), lid))
    conn.commit()
    conn.close()


def list_links():
    conn = get_conn()
    rows = conn.execute(
        """SELECT l.*, pa.name AS name_a, pb.name AS name_b
           FROM links l
           LEFT JOIN persons pa ON pa.id = l.person_a
           LEFT JOIN persons pb ON pb.id = l.person_b
           ORDER BY l.id"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def links_of(pid):
    """某人物参与的所有连接（含对方姓名）"""
    conn = get_conn()
    rows = conn.execute(
        """SELECT l.*, pa.name AS name_a, pb.name AS name_b
           FROM links l
           LEFT JOIN persons pa ON pa.id = l.person_a
           LEFT JOIN persons pb ON pb.id = l.person_b
           WHERE l.person_a=? OR l.person_b=?""",
        (pid, pid)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_link(lid):
    conn = get_conn()
    conn.execute("DELETE FROM links WHERE id=?", (lid,))
    conn.commit()
    conn.close()


def add_interaction(person_id, interaction_type, note="", date=None):
    """新增人物互动并返回记录 ID；人物不存在或类型为空时抛出 ValueError。"""
    if not isinstance(interaction_type, str) or not interaction_type.strip():
        raise ValueError("互动类型不能为空")
    interaction_type = interaction_type.strip()
    if date is None:
        date = calendar_date.today().isoformat()
    conn = get_conn()
    try:
        if conn.execute("SELECT id FROM persons WHERE id=?", (person_id,)).fetchone() is None:
            raise ValueError("人物不存在")
        cur = conn.execute(
            "INSERT INTO interactions (person_id, type, date, note) VALUES (?,?,?,?)",
            (person_id, interaction_type, date, note))
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_interactions(person_id=None):
    """互动记录；person_id=None 时返回全量（v0.21：批量评分上下文预取用）"""
    conn = get_conn()
    if person_id is None:
        rows = conn.execute("SELECT * FROM interactions ORDER BY date DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM interactions WHERE person_id=? ORDER BY date DESC", (person_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_position(pid, x, y):
    """保存拖拽后的节点位置（x,y 为 0~1 画布比例坐标，窗口缩放后仍生效）"""
    conn = get_conn()
    conn.execute("UPDATE persons SET pos_x=?, pos_y=? WHERE id=?", (float(x), float(y), pid))
    conn.commit()
    conn.close()


def set_memo(pid, text):
    conn = get_conn()
    conn.execute("UPDATE persons SET memo_md=? WHERE id=?", (text, pid))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("DB 初始化完成:", DB_PATH)
    print("人物数:", len(list_persons()), " 礼品数:", len(list_catalog()))

# ---------------------- 积分域（v0.68 P0） ----------------------
def get_meta(key, default=""):
    conn = get_conn()
    row = conn.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_meta(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?,?)",
                 (key, value))
    conn.commit()
    conn.close()


POINTS_FREE_GIFT = 100          # 每个账号首次领取的体验积分


def _points_balance_conn(conn, user_key):
    row = conn.execute(
        "SELECT COALESCE(SUM(delta),0) AS b FROM points_ledger WHERE user_key=?",
        (user_key,)).fetchone()
    return int(row["b"] or 0)


def points_balance(user_key=None):
    user_key = user_key or current_account()
    conn = get_conn()
    bal = _points_balance_conn(conn, user_key)
    conn.close()
    return bal


def points_record(user_key, delta, reason, source, ref_trade_no=""):
    """积分流水唯一写入口；同一非空交易号全库只能入账一次。"""
    user_key = user_key or current_account()
    delta = int(delta)
    if delta == 0:
        raise ValueError("积分变动不能为 0")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        bal = _points_balance_conn(conn, user_key) + delta
        if bal < 0:
            raise ValueError("积分不足")
        conn.execute(
            "INSERT INTO points_ledger (user_key, delta, balance_after, reason, "
            "source, ref_trade_no) VALUES (?,?,?,?,?,?)",
            (user_key, delta, bal, reason, source, ref_trade_no or ""))
        conn.commit()
        return bal
    except sqlite3.IntegrityError as ex:
        conn.rollback()
        raise ValueError("该交易号已入账，不能重复记账") from ex
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def points_consume(user_key, amount, reason):
    """原子扣减；余额不足时不调用服务、不产生负余额。"""
    user_key = user_key or current_account()
    amount = int(amount)
    if amount <= 0:
        return points_balance(user_key)
    try:
        return points_record(user_key, -amount, reason, "consume")
    except ValueError as ex:
        if str(ex) == "积分不足":
            bal = points_balance(user_key)
            raise ValueError("积分不足（余额 %d，需要 %d）——请先充值" % (bal, amount)) from ex
        raise


def points_refund(user_key, amount, reason, ref_trade_no):
    """API 失败后的预扣退回，交易号保证重试不重复退款。"""
    return points_record(user_key, int(amount), reason, "refund", ref_trade_no)


def create_points_order(user_key, product_key, product_name, points, amount,
                        channel, out_trade_no):
    """只建积分订单，不加积分；支付确认必须走 points_mark_order_paid。"""
    user_key = user_key or current_account()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO points_orders "
        "(user_key,product_key,product_name,points,amount,channel,out_trade_no,status) "
        "VALUES (?,?,?,?,?,?,?,'待付款')",
        (user_key, product_key, product_name, int(points), float(amount),
         channel, out_trade_no))
    conn.commit()
    oid = int(cur.lastrowid)
    conn.close()
    return oid


def get_points_order(order_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM points_orders WHERE id=?",
                       (int(order_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_points_orders(user_key=None, limit=50):
    user_key = user_key or current_account()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM points_orders WHERE user_key=? ORDER BY id DESC LIMIT ?",
        (user_key, int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_points_ledger(user_key=None, limit=100, direction="all"):
    """查询当前账号的积分流水；只读、倒序，收支筛选不暴露其他账号。"""
    user_key = user_key or current_account()
    direction = (direction or "all").strip().lower()
    if direction not in ("all", "income", "expense"):
        raise ValueError("流水方向仅支持 all / income / expense")
    limit = max(1, min(500, int(limit)))
    where = "user_key=?"
    params = [user_key]
    if direction == "income":
        where += " AND delta>0"
    elif direction == "expense":
        where += " AND delta<0"
    params.append(limit)
    conn = get_conn()
    rows = conn.execute(
        "SELECT id,user_key,delta,balance_after,reason,source,ref_trade_no,"
        "datetime(created_at,'localtime') AS created_at "
        "FROM points_ledger WHERE " + where + " ORDER BY id DESC LIMIT ?", params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_points_order_status(order_id, status):
    """已到账状态不可被后续轮询或重复点击回退。"""
    conn = get_conn()
    conn.execute(
        "UPDATE points_orders SET status=? WHERE id=? "
        "AND status NOT IN ('已付款','模拟已付款')",
        (status, int(order_id)))
    conn.commit()
    conn.close()


def points_mark_order_paid(order_id, trade_no, status="已付款"):
    """支付确认与积分入账同事务；返回 (当前余额, 是否本次入账)。"""
    trade_no = (trade_no or "").strip()
    if not trade_no:
        raise ValueError("支付成功但网关未返回交易号，已暂停自动入账，请人工核对")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM points_orders WHERE id=?",
                           (int(order_id),)).fetchone()
        if row is None:
            raise ValueError("积分订单不存在：%s" % order_id)
        if row["status"] in ("已付款", "模拟已付款"):
            conn.commit()
            return _points_balance_conn(conn, row["user_key"]), False
        used = conn.execute(
            "SELECT id FROM points_orders WHERE pay_ref=? AND id<>?",
            (trade_no, int(order_id))).fetchone()
        if used:
            raise ValueError("该支付交易号已绑定其他积分订单")
        bal = _points_balance_conn(conn, row["user_key"]) + int(row["points"])
        conn.execute(
            "INSERT INTO points_ledger (user_key, delta, balance_after, reason, "
            "source, ref_trade_no) VALUES (?,?,?,?,?,?)",
            (row["user_key"], int(row["points"]), bal,
             "购买%s" % row["product_name"],
             "mock_purchase" if row["channel"] == "mock" else "purchase",
             trade_no))
        conn.execute(
            "UPDATE points_orders SET status=?,pay_ref=?,"
            "paid_at=datetime('now','localtime') WHERE id=?",
            (status, trade_no, int(order_id)))
        conn.commit()
        return bal, True
    except sqlite3.IntegrityError as ex:
        conn.rollback()
        raise ValueError("该交易号已入账，不能重复记账") from ex
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def points_purchase_url():
    return get_meta("points_purchase_url", "")


def set_points_purchase_url(url):
    set_meta("points_purchase_url", (url or "").strip())
