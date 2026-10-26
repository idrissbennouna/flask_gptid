"""Couche d'accès SQLite : schéma + fonctions de lecture/écriture.

Chaque fonction ouvre une connexion courte : simple, thread-safe, et utilisable
depuis le générateur de streaming (qui s'exécute hors du contexte de requête).
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    n_rows      INTEGER NOT NULL DEFAULT 0,
    n_tables    INTEGER NOT NULL DEFAULT 0,
    score       REAL,
    report      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL DEFAULT 'Nouvelle conversation',
    domain      TEXT    NOT NULL DEFAULT 'general',
    dataset_id  INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id   INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role              TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    content           TEXT    NOT NULL,
    domain            TEXT,
    model             TEXT,
    response_time_ms  INTEGER,
    first_token_ms    INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    tokens_estimated  INTEGER NOT NULL DEFAULT 0,
    rating            INTEGER CHECK (rating BETWEEN 1 AND 5),
    sources           TEXT,
    created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    ext         TEXT    NOT NULL,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    n_chunks    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    content     TEXT    NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc    ON chunks(document_id, position);
"""


# ----------------------------------------------------------------------------
# Utilitaires bas niveau
# ----------------------------------------------------------------------------
def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATABASE_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(connect()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def query(sql: str, params: tuple = (), one: bool = False):
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql: str, params: tuple = ()) -> int:
    with closing(connect()) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


# ----------------------------------------------------------------------------
# Conversations
# ----------------------------------------------------------------------------
DEFAULT_TITLE = "Nouvelle conversation"


def create_conversation(domain: str = "general") -> dict:
    ts = now()
    cid = execute(
        "INSERT INTO conversations (title, domain, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (DEFAULT_TITLE, domain, ts, ts),
    )
    return get_conversation(cid)


def get_conversation(cid: int):
    return query(
        """SELECT c.*, d.name AS dataset_name
           FROM conversations c LEFT JOIN datasets d ON d.id = c.dataset_id
           WHERE c.id = ?""",
        (cid,),
        one=True,
    )


def list_conversations() -> list[dict]:
    return query(
        """SELECT c.id, c.title, c.domain, c.updated_at,
                  (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n_messages
           FROM conversations c
           ORDER BY c.updated_at DESC, c.id DESC"""
    )


def update_conversation(cid: int, **fields) -> None:
    allowed = {"title", "domain", "dataset_id"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = now()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE conversations SET {assignments} WHERE id = ?", (*fields.values(), cid))


def delete_conversation(cid: int) -> None:
    execute("DELETE FROM conversations WHERE id = ?", (cid,))


# ----------------------------------------------------------------------------
# Messages
# ----------------------------------------------------------------------------
def _parse_message(row: dict) -> dict:
    row["sources"] = json.loads(row["sources"]) if row.get("sources") else []
    return row


def add_message(conversation_id: int, role: str, content: str, **extra) -> int:
    columns = ["conversation_id", "role", "content", "created_at"]
    values = [conversation_id, role, content, now()]
    allowed = {
        "domain", "model", "response_time_ms", "first_token_ms", "prompt_tokens",
        "completion_tokens", "total_tokens", "tokens_estimated", "sources",
    }
    for key, value in extra.items():
        if key in allowed:
            columns.append(key)
            values.append(json.dumps(value, ensure_ascii=False) if key == "sources" else value)
    placeholders = ", ".join("?" for _ in columns)
    mid = execute(
        f"INSERT INTO messages ({', '.join(columns)}) VALUES ({placeholders})", tuple(values)
    )
    execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), conversation_id))
    return mid


def get_message(mid: int):
    row = query("SELECT * FROM messages WHERE id = ?", (mid,), one=True)
    return _parse_message(row) if row else None


def get_messages(conversation_id: int) -> list[dict]:
    rows = query(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)
    )
    return [_parse_message(r) for r in rows]


def set_rating(mid: int, rating) -> None:
    execute("UPDATE messages SET rating = ? WHERE id = ?", (rating, mid))


# ----------------------------------------------------------------------------
# Statistiques de performance
# ----------------------------------------------------------------------------
def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def conversation_stats(cid: int) -> dict:
    messages = get_messages(cid)
    items, last_question = [], ""
    for m in messages:
        if m["role"] == "user":
            last_question = m["content"]
            continue
        items.append({
            "id": m["id"],
            "question": last_question[:120],
            "domain": m["domain"],
            "response_time_ms": m["response_time_ms"],
            "first_token_ms": m["first_token_ms"],
            "prompt_tokens": m["prompt_tokens"],
            "completion_tokens": m["completion_tokens"],
            "total_tokens": m["total_tokens"],
            "tokens_estimated": bool(m["tokens_estimated"]),
            "rating": m["rating"],
            "n_sources": len(m["sources"]),
        })
    times = [i["response_time_ms"] for i in items]
    return {
        "conversation_id": cid,
        "n_messages": len(messages),
        "n_answers": len(items),
        "avg_response_ms": _avg(times),
        "max_response_ms": max([t for t in times if t is not None], default=None),
        "prompt_tokens": sum(i["prompt_tokens"] or 0 for i in items),
        "completion_tokens": sum(i["completion_tokens"] or 0 for i in items),
        "total_tokens": sum(i["total_tokens"] or 0 for i in items),
        "avg_rating": _avg([i["rating"] for i in items]),
        "n_rated": sum(1 for i in items if i["rating"]),
        "items": items,
    }


def global_stats() -> dict:
    totals = query(
        """SELECT COUNT(*) AS n_answers,
                  AVG(response_time_ms) AS avg_response_ms,
                  SUM(prompt_tokens) AS prompt_tokens,
                  SUM(completion_tokens) AS completion_tokens,
                  SUM(total_tokens) AS total_tokens,
                  AVG(rating) AS avg_rating,
                  COUNT(rating) AS n_rated
           FROM messages WHERE role = 'assistant'""",
        one=True,
    )
    by_domain = query(
        """SELECT COALESCE(domain, 'general') AS domain,
                  COUNT(*) AS n_answers,
                  AVG(response_time_ms) AS avg_response_ms,
                  AVG(total_tokens) AS avg_tokens,
                  SUM(total_tokens) AS total_tokens,
                  AVG(rating) AS avg_rating
           FROM messages WHERE role = 'assistant'
           GROUP BY COALESCE(domain, 'general')
           ORDER BY n_answers DESC"""
    )
    n_conversations = query("SELECT COUNT(*) AS n FROM conversations", one=True)["n"]
    for key in ("avg_response_ms", "avg_rating"):
        if totals[key] is not None:
            totals[key] = round(totals[key], 2)
    for row in by_domain:
        for key in ("avg_response_ms", "avg_tokens", "avg_rating"):
            if row[key] is not None:
                row[key] = round(row[key], 2)
    totals.update({k: totals[k] or 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens")})
    return {"n_conversations": n_conversations, **totals, "by_domain": by_domain}


# ----------------------------------------------------------------------------
# Documents (sources) et passages
# ----------------------------------------------------------------------------
def add_document(name: str, ext: str, size_bytes: int, chunks: list[dict]) -> int:
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO documents (name, ext, size_bytes, n_chunks, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, ext, size_bytes, len(chunks), now()),
        )
        doc_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO chunks (document_id, position, content, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
            [(doc_id, c["position"], c["content"], c["start_line"], c["end_line"]) for c in chunks],
        )
        conn.commit()
    return doc_id


def list_documents() -> list[dict]:
    return query("SELECT * FROM documents ORDER BY id DESC")


def get_document(doc_id: int):
    return query("SELECT * FROM documents WHERE id = ?", (doc_id,), one=True)


def delete_document(doc_id: int) -> None:
    execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def get_chunk(doc_id: int, position: int):
    return query(
        "SELECT * FROM chunks WHERE document_id = ? AND position = ?", (doc_id, position), one=True
    )


# ----------------------------------------------------------------------------
# Jeux de données analysés
# ----------------------------------------------------------------------------
def add_dataset(name: str, report: dict) -> int:
    summary = report.get("summary", {})
    return execute(
        """INSERT INTO datasets (name, n_rows, n_tables, score, report, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            name,
            summary.get("n_rows", 0),
            len(report.get("tables", [])),
            summary.get("score"),
            json.dumps(report, ensure_ascii=False),
            now(),
        ),
    )


def list_datasets() -> list[dict]:
    return query(
        "SELECT id, name, n_rows, n_tables, score, created_at FROM datasets ORDER BY id DESC"
    )


def get_dataset(ds_id: int):
    row = query("SELECT * FROM datasets WHERE id = ?", (ds_id,), one=True)
    if row:
        row["report"] = json.loads(row["report"])
    return row


def delete_dataset(ds_id: int) -> None:
    execute("DELETE FROM datasets WHERE id = ?", (ds_id,))
