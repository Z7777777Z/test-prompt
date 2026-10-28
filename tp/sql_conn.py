"""真实数据库连接 + 表结构内省（「表结构」页「连接数据库」的后端）。

支持的数据库类型与依赖：
  · SQLite            —— 标准库 sqlite3（零依赖）
  · MySQL / MariaDB   —— pymysql（纯 Python，requirements.txt 已列）
  · PostgreSQL        —— pg8000（纯 Python，requirements.txt 已列）
  · Oracle / SQL Server —— 需要 cx_Oracle / pyodbc 等重驱动，项目未安装，
                           连接时返回明确提示，不会静默假装成功。

统一输出（与页面左侧树「服务器 → 数据库 → 表」同构）：
  [{"name": 库名, "cn": 库注释,
    "tables": [{"name": 表名, "cn": 表注释,
                "cols": [[字段名, 类型, 是否必填(1/0), 键(PK/UK/FK/""), 默认值, 注释], ...],
                "rels": [{"col": 本表列, "ref_table": 被引用表, "ref_col": 被引用列}, ...]}]}]
  · rels 只收录数据库里**真实存在的外键约束**（SQLite PRAGMA / MySQL & PG
    information_schema & pg_catalog）；库里没建外键时为空数组，交由页面按需用 AI 兜底。

口径与项目内 SQLite 内省（tp/db.py）保持一致：
  · 是否必填：NOT NULL 或主键 → 1
  · 键：PK 主键 / UK 单列唯一 / FK 外键 / 空
  · 默认值：无默认值时，必填 → AUTO_INCREMENT（自增主键）或 ""，可空 → "NULL"
"""
import os
import re
import sqlite3
from urllib.parse import unquote, urlparse

# 连接超时（秒）：连不上时快速失败，避免页面长时间转圈
DEFAULT_TIMEOUT = 8

# 不支持的类型 → 提示语
NEED_DRIVER = {
    "Oracle": "cx_Oracle（Oracle 官方 Python 驱动）",
    "SQL Server": "pyodbc（另需安装 ODBC Driver 17/18 for SQL Server）",
}


class ConnError(Exception):
    """连接 / 内省过程中的可预期错误，消息直接展示给用户。"""


# ------------------------------------------------------------
# URL 解析：兼容 JDBC 写法与常见的 xxx:// 写法
# ------------------------------------------------------------
_PREFIXES = ("jdbc:", "sqlite:///", "sqlite://", "sqlite:", "mysql://", "mariadb://",
             "postgresql://", "postgres://")


def _strip_jdbc(url: str) -> str:
    s = (url or "").strip()
    if s.lower().startswith("jdbc:"):
        s = s[5:]
    return s


def sqlite_path_from_url(url: str) -> str:
    """从 SQLite 的 URL / JDBC URL 里取出真实文件路径。

    兼容：jdbc:sqlite:C:/a/b.db、jdbc:sqlite:///C:/a/b.db、sqlite:///C:/a/b.db、
          file:C:/a/b.db、以及直接粘贴的裸路径。
    """
    s = _strip_jdbc(url)
    for p in ("sqlite:///", "sqlite://", "sqlite:"):
        if s.lower().startswith(p):
            s = s[len(p):]
            break
    s = s.split("?")[0]
    if s.lower().startswith("file:"):
        s = s[5:]
    # ///C:/x.db → C:/x.db（Windows 盘符前多余的斜杠）
    m = re.match(r"^/+(?=[A-Za-z]:)", s)
    if m:
        s = s[m.end():]
    return unquote(s).strip()


def parse_server_url(url: str):
    """解析 mysql:// / postgresql:// 形式的 URL → (host, port, user, password, database)。"""
    s = _strip_jdbc(url)
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", s):
        s = "tcp://" + s          # 允许「host:port/db」这种省略协议的写法
    u = urlparse(s)
    db = (u.path or "").lstrip("/").split("/")[0]
    return {
        "host": u.hostname or "",
        "port": u.port,
        "user": unquote(u.username) if u.username else "",
        "password": unquote(u.password) if u.password else "",
        "database": unquote(db),
    }


# ------------------------------------------------------------
# 统一入口
# ------------------------------------------------------------
def introspect(db_type: str, cfg: dict):
    """连接真实数据库并读取表结构。cfg 见 views_db.db_connect 的组装说明。"""
    db_type = (db_type or "").strip()
    if db_type == "SQLite":
        return _sqlite(cfg)
    if db_type in ("MySQL", "MariaDB"):
        return _mysql(cfg)
    if db_type == "PostgreSQL":
        return _postgres(cfg)
    hint = NEED_DRIVER.get(db_type)
    if hint:
        raise ConnError(
            "暂不支持直接连接 %s：项目未安装 %s。可改用 MySQL / MariaDB / PostgreSQL / SQLite。"
            % (db_type, hint)
        )
    raise ConnError("未知的数据库类型：%s" % (db_type or "(空)"))


def server_id(db_type: str, cfg: dict) -> str:
    """左侧树根节点显示名 / 判重键（与前端原口径一致）。"""
    if db_type == "SQLite":
        if cfg.get("mode") == "url":
            return (cfg.get("url") or "").strip()
        return (cfg.get("path") or "").strip()
    if cfg.get("mode") == "url":
        return (cfg.get("url") or "").strip()
    host = (cfg.get("host") or "").strip()
    port = (cfg.get("port") or "").strip()
    if not port:
        port = {"MySQL": "3306", "MariaDB": "3306", "PostgreSQL": "5432"}.get(db_type, "")
    return host + (":" + port if port else "")


# ------------------------------------------------------------
# SQLite
# ------------------------------------------------------------
def _sqlite(cfg: dict):
    if cfg.get("mode") == "url":
        path = sqlite_path_from_url(cfg.get("url") or "")
    else:
        path = (cfg.get("path") or "").strip()
    if not path:
        raise ConnError("请填写数据库文件路径（或 JDBC URL）")
    if not os.path.isfile(path):
        raise ConnError("数据库文件不存在：%s" % path)

    try:
        conn = sqlite3.connect(path, timeout=DEFAULT_TIMEOUT)
    except sqlite3.Error as e:
        raise ConnError("打开 SQLite 文件失败：%s" % e)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        tables = [
            {"name": r["name"], "cn": "",
             "cols": _sqlite_columns(conn, r["name"], r["sql"] or ""),
             "rels": _sqlite_rels(conn, r["name"])}
            for r in rows
        ]
    except sqlite3.Error as e:
        raise ConnError("读取 SQLite 表结构失败：%s" % e)
    finally:
        conn.close()

    name = os.path.basename(path)
    return [{"name": name, "cn": "SQLite 数据库文件", "tables": tables}]


def _clean_default(v):
    """PRAGMA 的默认值带原始引号，去掉外层引号（与 tp/db.py 同口径）。"""
    if v is None:
        return None
    s = str(v).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
    return s


def _sqlite_rels(conn, table: str):
    """PRAGMA foreign_key_list → [{"col": 本表列, "ref_table": 目标表, "ref_col": 目标列}]。

    一行 = (id, seq, table, from, to, on_update, on_delete, match)；
    复合外键会有多行（同一 id），这里逐行展开即可。
    """
    out = []
    try:
        rows = conn.execute('PRAGMA foreign_key_list("%s")' % table).fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        col = r["from"]
        ref_table = r["table"]
        if not col or not ref_table:
            continue
        out.append({"col": col, "ref_table": ref_table, "ref_col": r["to"] or ""})
    return out


def _sqlite_columns(conn, table: str, ddl: str):
    unique_cols = set()
    for idx in conn.execute('PRAGMA index_list("%s")' % table).fetchall():
        if not idx["unique"]:
            continue
        cols = [c["name"] for c in conn.execute('PRAGMA index_info("%s")' % idx["name"]).fetchall()
                if c["name"]]
        # 只认单列唯一索引；复合唯一不逐列标 UK
        if len(cols) == 1:
            unique_cols.add(cols[0])

    fk_cols = {r["from"] for r in conn.execute('PRAGMA foreign_key_list("%s")' % table).fetchall()}
    auto_inc = "AUTOINCREMENT" in (ddl or "").upper()

    out = []
    for c in conn.execute('PRAGMA table_info("%s")' % table).fetchall():
        name = c["name"]
        is_pk = bool(c["pk"])
        required = bool(c["notnull"]) or is_pk
        key = "PK" if is_pk else ("UK" if name in unique_cols else ("FK" if name in fk_cols else ""))

        dflt = _clean_default(c["dflt_value"])
        if dflt is None:
            dflt = "AUTO_INCREMENT" if (is_pk and auto_inc) else ("" if required else "NULL")

        out.append([name, c["type"] or "", 1 if required else 0, key, dflt, ""])
    return out


# ------------------------------------------------------------
# MySQL / MariaDB
# ------------------------------------------------------------
_SYS_SCHEMAS = ("information_schema", "mysql", "performance_schema", "sys")

_MYSQL_KEY = {"PRI": "PK", "UNI": "UK"}


def _mysql(cfg: dict):
    try:
        import pymysql
    except ImportError:
        raise ConnError("缺少 MySQL 驱动 pymysql，请先执行：pip install pymysql")

    info = _server_params("MySQL", cfg, default_port=3306)
    try:
        conn = pymysql.connect(
            host=info["host"], port=info["port"], user=info["user"],
            password=info["password"], database=info["database"] or None,
            charset="utf8mb4", connect_timeout=DEFAULT_TIMEOUT,
            read_timeout=DEFAULT_TIMEOUT, write_timeout=DEFAULT_TIMEOUT,
        )
    except pymysql.err.OperationalError as e:
        raise ConnError(_mysql_error(e, info))
    except pymysql.err.MySQLError as e:
        raise ConnError("连接 MySQL 失败：%s" % e)

    try:
        with conn.cursor() as cur:
            schemas = _mysql_schemas(cur, info["database"])
            tables, columns, fks = _mysql_meta(cur, schemas)
    except pymysql.err.MySQLError as e:
        raise ConnError("读取 MySQL 表结构失败：%s" % _mysql_error(e, info))
    finally:
        conn.close()

    dbs = []
    for sch in schemas:
        cols_by_tbl = {}
        for row in columns.get(sch, []):
            rels = fks.get((sch, row[0]), [])
            fk_cols = {r["col"] for r in rels}
            cols_by_tbl.setdefault(row[0], []).append(_mysql_col(row, fk_cols))
        dbs.append({
            "name": sch,
            "cn": tables.get(sch, {}).get("_comment", ""),
            "tables": [
                {"name": t, "cn": tables.get(sch, {}).get(t, ""),
                 "cols": cols_by_tbl.get(t, []), "rels": fks.get((sch, t), [])}
                for t in tables.get(sch, {}).get("_names", [])
            ],
        })
    return dbs


def _mysql_schemas(cur, database: str):
    if database:
        return [database]
    cur.execute("SHOW DATABASES")
    all_dbs = [r[0] for r in cur.fetchall()]
    return [d for d in all_dbs if d.lower() not in _SYS_SCHEMAS]


def _mysql_meta(cur, schemas):
    """一次性把库/表/列/外键元数据取回来，避免逐表往返。"""
    ph = ",".join(["%s"] * len(schemas))

    tables = {}
    cur.execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA IN (%s) AND TABLE_TYPE='BASE TABLE' "
        "ORDER BY TABLE_SCHEMA, TABLE_NAME" % ph, schemas)
    for sch, tbl, cmt in cur.fetchall():
        d = tables.setdefault(sch, {"_names": [], "_comment": ""})
        d["_names"].append(tbl)
        d[tbl] = cmt or ""

    columns = {}
    cur.execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, "
        "       COLUMN_KEY, COLUMN_DEFAULT, COLUMN_COMMENT, EXTRA "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA IN (%s) "
        "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION" % ph, schemas)
    for row in cur.fetchall():
        columns.setdefault(row[0], []).append(row[1:])

    fks = {}
    cur.execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, "
        "       REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
        "FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA IN (%s) AND REFERENCED_TABLE_NAME IS NOT NULL" % ph, schemas)
    for sch, tbl, col, ref_tbl, ref_col in cur.fetchall():
        fks.setdefault((sch, tbl), []).append(
            {"col": col, "ref_table": ref_tbl or "", "ref_col": ref_col or ""})

    return tables, columns, fks


def _mysql_col(row, fk_cols):
    """information_schema.COLUMNS 一行（去掉 schema 列后）→ [字段名, 类型, 是否必填, 键, 默认值, 注释]

    row = (表名, 字段名, 类型, 是否可空, 键, 默认值, 注释, 额外)
    """
    _tbl, name, ctype, nullable, ckey, dflt, comment, extra = row
    is_pk = ckey == "PRI"
    required = (nullable == "NO") or is_pk
    key = _MYSQL_KEY.get(ckey) or ("FK" if name in fk_cols else "")

    if dflt is None:
        if is_pk and "auto_increment" in (extra or "").lower():
            dflt = "AUTO_INCREMENT"
        elif required:
            dflt = ""
        else:
            dflt = "NULL"
    return [name, ctype or "", 1 if required else 0, key, str(dflt), comment or ""]


def _mysql_error(e, info):
    code = e.args[0] if e.args else None
    if code == 1045:
        return "MySQL 拒绝访问：用户名或密码错误（%s）" % info["user"]
    if code == 1049:
        return "MySQL 中不存在数据库「%s」" % info["database"]
    if code in (2003, 2002):
        return "连不上 MySQL 服务（%s:%s）：请确认服务已启动、端口可达、未被防火墙拦截" % (
            info["host"], info["port"])
    return "连接 MySQL 失败：%s" % e


# ------------------------------------------------------------
# PostgreSQL
# ------------------------------------------------------------
_PG_KEY = {"PRIMARY KEY": "PK", "UNIQUE": "UK", "FOREIGN KEY": "FK"}


def _postgres(cfg: dict):
    try:
        import pg8000.dbapi
    except ImportError:
        raise ConnError("缺少 PostgreSQL 驱动 pg8000，请先执行：pip install pg8000")

    info = _server_params("PostgreSQL", cfg, default_port=5432)
    database = info["database"] or "postgres"
    try:
        conn = pg8000.dbapi.connect(
            host=info["host"], port=info["port"], user=info["user"],
            password=info["password"], database=database, timeout=DEFAULT_TIMEOUT,
        )
    except Exception as e:                       # pg8000 抛多种异常，统一转可读提示
        raise ConnError(_pg_error(e, info, database))

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type='BASE TABLE' AND table_schema NOT IN ('pg_catalog','information_schema') "
            "ORDER BY table_schema, table_name")
        listed = cur.fetchall()

        cur.execute(
            "SELECT tc.table_schema, tc.table_name, kcu.column_name, tc.constraint_type "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema "
            "WHERE tc.constraint_type IN ('PRIMARY KEY','UNIQUE','FOREIGN KEY')")
        keys = {}
        for sch, tbl, col, ctype in cur.fetchall():
            # 一个列可能同时是 PK 与外键，PK 优先
            keys.setdefault((sch, tbl), {}).setdefault(col, set()).add(_PG_KEY.get(ctype, ""))

        cur.execute(
            "SELECT c.table_schema, c.table_name, c.column_name, "
            "       CASE WHEN c.data_type IN ('character varying','character') "
            "            THEN c.data_type || '(' || COALESCE(c.character_maximum_length::text, '') || ')' "
            "            WHEN c.data_type = 'numeric' AND c.numeric_precision IS NOT NULL "
            "            THEN 'numeric(' || c.numeric_precision::text || ',' || COALESCE(c.numeric_scale::text,'0') || ')' "
            "            ELSE c.data_type END, "
            "       c.is_nullable, c.column_default, "
            "       COALESCE(pgd.description, '') "
            "FROM information_schema.columns c "
            "LEFT JOIN pg_catalog.pg_statio_all_tables st "
            "  ON st.schemaname = c.table_schema AND st.relname = c.table_name "
            "LEFT JOIN pg_catalog.pg_description pgd "
            "  ON pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position "
            "ORDER BY c.table_schema, c.table_name, c.ordinal_position")
        cols = {}
        for sch, tbl, name, ctype, nullable, dflt, comment in cur.fetchall():
            is_pk = "PK" in keys.get((sch, tbl), {}).get(name, set())
            required = (nullable == "NO") or is_pk
            key = ""
            for k in ("PK", "UK", "FK"):        # PK > UK > FK
                if k in keys.get((sch, tbl), {}).get(name, set()):
                    key = k
                    break
            dflt_txt = "" if dflt is None else str(dflt)
            if dflt is None:
                dflt_txt = "" if required else "NULL"
            cols.setdefault((sch, tbl), []).append(
                [name, ctype or "", 1 if required else 0, key, dflt_txt, comment or ""])

        # 真实外键：本表列 → 被引用表.被引用列（复合外键取第一列，足够描述关系）
        cur.execute(
            "SELECT nsp.nspname, cls.relname, att.attname, fcls.relname, fatt.attname "
            "FROM pg_catalog.pg_constraint con "
            "JOIN pg_catalog.pg_class cls ON cls.oid = con.conrelid "
            "JOIN pg_catalog.pg_namespace nsp ON nsp.oid = cls.relnamespace "
            "JOIN pg_catalog.pg_class fcls ON fcls.oid = con.confrelid "
            "JOIN pg_catalog.pg_attribute att "
            "  ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1] "
            "JOIN pg_catalog.pg_attribute fatt "
            "  ON fatt.attrelid = con.confrelid AND fatt.attnum = con.confkey[1] "
            "WHERE con.contype = 'f'")
        fk_rels = {}
        for sch, tbl, col, ref_tbl, ref_col in cur.fetchall():
            fk_rels.setdefault((sch, tbl), []).append(
                {"col": col, "ref_table": ref_tbl or "", "ref_col": ref_col or ""})
    except Exception as e:
        raise ConnError("读取 PostgreSQL 表结构失败：%s" % e)
    finally:
        conn.close()

    by_schema = {}
    for sch, tbl in listed:
        by_schema.setdefault(sch, []).append(
            {"name": tbl, "cn": "", "cols": cols.get((sch, tbl), []),
             "rels": fk_rels.get((sch, tbl), [])})

    return [{"name": sch, "cn": "", "tables": by_schema[sch]} for sch in sorted(by_schema)]


def _pg_error(e, info, database):
    code = getattr(e, "args", [None])[0] if getattr(e, "args", None) else None
    msg = str(e)
    if "password authentication failed" in msg or code == "28P01":
        return "PostgreSQL 拒绝访问：用户名或密码错误（%s）" % info["user"]
    if code == "3D000" or ("does not exist" in msg and "database" in msg.lower()):
        return "PostgreSQL 中不存在数据库「%s」" % database
    # pg8000 连不上时抛 InterfaceError（不是 OSError），消息为
    # "Can't create a connection to host x and port y (timeout is 8 ...)"
    if (isinstance(e, (OSError, ConnectionError))
            or "Can't create a connection" in msg
            or "timeout" in msg.lower()):
        return "连不上 PostgreSQL 服务（%s:%s）：请确认服务已启动、端口可达、未被防火墙拦截" % (
            info["host"], info["port"])
    return "连接 PostgreSQL 失败：%s" % msg


# ------------------------------------------------------------
# 表单 → 连接参数
# ------------------------------------------------------------
def _server_params(db_type: str, cfg: dict, default_port: int):
    """URL 模式优先用 URL 里的地址/库名；用户名密码 URL 里没有则取表单输入。"""
    host = (cfg.get("host") or "").strip()
    port = cfg.get("port") or ""
    user = (cfg.get("user") or "").strip()
    password = cfg.get("password") or ""
    database = ""

    if cfg.get("mode") == "url":
        parsed = parse_server_url(cfg.get("url") or "")
        host = parsed["host"] or host
        port = parsed["port"] or port
        user = parsed["user"] or user
        password = parsed["password"] or password
        database = parsed["database"]

    if not host:
        raise ConnError("请填写服务器地址（或 URL）")
    try:
        port = int(port) if str(port).strip() else default_port
    except (TypeError, ValueError):
        raise ConnError("端口必须是数字：%s" % port)
    if not user:
        # MySQL / PostgreSQL 都要求账号，给一个常见默认值以免报错难懂
        user = "root" if db_type in ("MySQL", "MariaDB") else "postgres"
    return {"host": host, "port": port, "user": user,
            "password": password, "database": database}
