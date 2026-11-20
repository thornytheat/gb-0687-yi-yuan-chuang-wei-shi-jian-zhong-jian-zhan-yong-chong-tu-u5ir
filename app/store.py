import os, sqlite3, json, hashlib
from pathlib import Path
from app.errors import DomainError
from app.normalize import serial


def connection():
    path = Path(os.environ.get("DATA_DIR", "data")); path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path / "runs.sqlite", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,review TEXT NOT NULL DEFAULT '待复核')")
    db.execute(
        "CREATE TABLE IF NOT EXISTS admissions ("
        "id TEXT PRIMARY KEY, payload TEXT NOT NULL, run_id TEXT NOT NULL, "
        "shift_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT '现行', "
        "superseded_by TEXT, created_seq INTEGER NOT NULL DEFAULT 0)"
    )
    return db


def save(result):
    run_id = result["manifest"]["run_id"]
    with connection() as db:
        old = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        if old is not None:
            # 相同输入重跑复用首次创建时间，保证 run_id 与 created 均幂等
            previous = json.loads(old["payload"])
            result["manifest"]["created"] = previous["manifest"]["created"]
        # 幂等 upsert：同 run_id 覆盖 payload 以保留最新阶段，复核版本与决定独立维护不被重置
        db.execute(
            "INSERT INTO runs(id,payload) VALUES(?,?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (run_id, json.dumps(result, ensure_ascii=False))
        )
    return fetch(run_id)


def fetch(run_id):
    with connection() as db: row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None: raise DomainError("NOT_FOUND", "批次不存在", 404)
    result = json.loads(row["payload"]); result["version"] = row["version"]; result["review"] = row["review"]
    return result


def history():
    with connection() as db: rows = db.execute("SELECT id,version,review FROM runs ORDER BY rowid DESC LIMIT 100").fetchall()
    return [dict(row) for row in rows]


def review(run_id, version, decision):
    if decision not in ("通过", "退回"): raise DomainError("REVIEW", "请选择通过或退回")
    if not isinstance(version, int): raise DomainError("STALE", "版本必须为整数")
    with connection() as db:
        cur = db.execute("UPDATE runs SET review=?,version=version+1 WHERE id=? AND version=?", (decision, run_id, version))
        if cur.rowcount != 1: raise DomainError("STALE", "复核版本已更新", 409)
    return fetch(run_id)


def _admission_id(result):
    plan = result["admission"]
    key = json.dumps({
        "run": result["manifest"]["run_id"],
        "shift": plan["shift_time"],
        "isolation_beds": plan["isolation_beds"],
        "reserve": plan["suspect_reserve_requested"],
        "critical": plan["critical_pending"],
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _shift_key(plan):
    return "|".join(plan["isolation_beds"]) + "@" + plan["shift_time"]


def save_admission(result):
    """同一记录重试：结论ID基于输入哈希，重复提交不新增占用行。"""
    admission = result["admission"]
    admission_id = _admission_id(result)
    run_id = result["manifest"]["run_id"]
    shift_key = _shift_key(admission)
    triggers = admission.get("late_isolation_triggers", [])
    with connection() as db:
        existing = db.execute("SELECT id FROM admissions WHERE id=?", (admission_id,)).fetchone()
        if existing is not None:
            row = db.execute("SELECT payload FROM admissions WHERE id=?", (admission_id,)).fetchone()
            return json.loads(row["payload"])
        superseded = []
        if triggers:
            # 晚到的隔离记录使同班次旧结论失效；旧 payload 保留床位去向与依据，仅改写失效状态
            old_rows = db.execute(
                "SELECT id,payload FROM admissions WHERE shift_key=? AND status='现行'",
                (shift_key,)
            ).fetchall()
            for old in old_rows:
                old_payload = json.loads(old["payload"])
                old_payload["status"] = "已失效"
                old_payload["superseded_by"] = admission_id
                old_payload["supersede_reason"] = "SUPERSEDED"
                old_payload["supersede_triggers"] = serial(triggers)
                db.execute("UPDATE admissions SET status='已失效',superseded_by=?,payload=? WHERE id=?",
                           (admission_id, json.dumps(old_payload, ensure_ascii=False), old["id"]))
                superseded.append({"id": old["id"], "status": "已失效", "superseded_by": admission_id})
        cur = db.execute("SELECT COALESCE(MAX(created_seq),0)+1 AS next_seq FROM admissions").fetchone()
        seq = cur["next_seq"]
        payload = serial(admission)
        payload.update({
            "admission_id": admission_id, "run_id": run_id, "shift_key": shift_key,
            "status": "现行", "superseded_by": None, "created_seq": seq,
            "superseded": superseded,
        })
        db.execute(
            "INSERT INTO admissions(id,payload,run_id,shift_key,status,superseded_by,created_seq) "
            "VALUES(?,?,?,?,'现行',NULL,?)",
            (admission_id, json.dumps(payload, ensure_ascii=False), run_id, shift_key, seq)
        )
        return payload


def fetch_admission(admission_id):
    with connection() as db:
        row = db.execute("SELECT payload FROM admissions WHERE id=?", (admission_id,)).fetchone()
    if row is None: raise DomainError("NOT_FOUND", "收治结论不存在", 404)
    return json.loads(row["payload"])


def admission_history(shift_key=None):
    with connection() as db:
        if shift_key:
            rows = db.execute("SELECT payload FROM admissions WHERE shift_key=? ORDER BY created_seq", (shift_key,)).fetchall()
        else:
            rows = db.execute("SELECT payload FROM admissions ORDER BY created_seq DESC LIMIT 100").fetchall()
    return [json.loads(r["payload"]) for r in rows]
