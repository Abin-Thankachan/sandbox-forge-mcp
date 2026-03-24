from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


LEASE_FIELDS = (
    "instance_id",
    "backend_name",
    "profile_name",
    "workspace_root",
    "workspace_id",
    "status",
    "created_at",
    "expires_at",
    "last_used_at",
    "owner_session",
    "ssh_port",
    "backend_instance_name",
    "runtime_name",
    "runtime_ready",
    "docker_command",
)

TASK_FIELDS = (
    "task_id",
    "instance_id",
    "command",
    "cwd",
    "env_json",
    "status",
    "pid",
    "created_at",
    "started_at",
    "finished_at",
    "exit_code",
    "log_path",
    "exit_code_path",
    "error_message",
)


class LeaseStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _has_column(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(str(row[1]) == column for row in rows)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS leases (
                    instance_id TEXT PRIMARY KEY,
                    backend_name TEXT NOT NULL,
                    profile_name TEXT NOT NULL,
                    workspace_root TEXT,
                    workspace_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    owner_session TEXT,
                    ssh_port INTEGER,
                    backend_instance_name TEXT NOT NULL,
                    runtime_name TEXT,
                    runtime_ready INTEGER NOT NULL DEFAULT 0,
                    docker_command TEXT
                )
                """
            )
            if not self._has_column(conn, "leases", "workspace_root"):
                conn.execute("ALTER TABLE leases ADD COLUMN workspace_root TEXT")
            if not self._has_column(conn, "leases", "workspace_id"):
                conn.execute("ALTER TABLE leases ADD COLUMN workspace_id TEXT")
            if not self._has_column(conn, "leases", "backend_instance_name"):
                conn.execute("ALTER TABLE leases ADD COLUMN backend_instance_name TEXT")
            if self._has_column(conn, "leases", "lima_name"):
                conn.execute(
                    """
                    UPDATE leases
                    SET backend_instance_name = COALESCE(NULLIF(backend_instance_name, ''), lima_name)
                    WHERE backend_instance_name IS NULL OR backend_instance_name = ''
                    """
                )
            if not self._has_column(conn, "leases", "runtime_name"):
                conn.execute("ALTER TABLE leases ADD COLUMN runtime_name TEXT")
            if not self._has_column(conn, "leases", "runtime_ready"):
                conn.execute("ALTER TABLE leases ADD COLUMN runtime_ready INTEGER NOT NULL DEFAULT 0")
            if not self._has_column(conn, "leases", "docker_command"):
                conn.execute("ALTER TABLE leases ADD COLUMN docker_command TEXT")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_leases_expires_at ON leases(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_leases_status ON leases(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_leases_workspace_id ON leases(workspace_id)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    cwd TEXT,
                    env_json TEXT,
                    status TEXT NOT NULL,
                    pid INTEGER,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    exit_code INTEGER,
                    log_path TEXT NOT NULL,
                    exit_code_path TEXT NOT NULL,
                    error_message TEXT,
                    FOREIGN KEY(instance_id) REFERENCES leases(instance_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_instance_id ON tasks(instance_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")

    def create_lease(self, lease: dict[str, Any]) -> None:
        normalized = dict(lease)
        normalized.setdefault("backend_instance_name", normalized.get("lima_name"))
        normalized.setdefault("workspace_root", None)
        normalized.setdefault("workspace_id", None)
        normalized.setdefault("runtime_name", None)
        normalized.setdefault("runtime_ready", 0)
        normalized.setdefault("docker_command", None)
        columns = ", ".join(LEASE_FIELDS)
        placeholders = ", ".join("?" for _ in LEASE_FIELDS)
        values = [normalized.get(field) for field in LEASE_FIELDS]

        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO leases ({columns}) VALUES ({placeholders})",
                values,
            )

    def upsert_lease(self, lease: dict[str, Any]) -> None:
        normalized = dict(lease)
        normalized.setdefault("backend_instance_name", normalized.get("lima_name"))
        normalized.setdefault("workspace_root", None)
        normalized.setdefault("workspace_id", None)
        normalized.setdefault("runtime_name", None)
        normalized.setdefault("runtime_ready", 0)
        normalized.setdefault("docker_command", None)
        columns = ", ".join(LEASE_FIELDS)
        placeholders = ", ".join("?" for _ in LEASE_FIELDS)
        values = [normalized.get(field) for field in LEASE_FIELDS]

        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO leases ({columns}) VALUES ({placeholders})",
                values,
            )

    def get_lease(self, instance_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM leases WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()

        if row is None:
            return None
        return dict(row)

    def list_leases(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM leases ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_lease(self, instance_id: str, **fields: Any) -> bool:
        if not fields:
            return False

        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [instance_id]

        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE leases SET {assignments} WHERE instance_id = ?",
                params,
            )
        return cur.rowcount > 0

    def count_active(self, statuses: tuple[str, ...], now_iso: str) -> int:
        placeholders = ", ".join("?" for _ in statuses)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM leases
                WHERE status IN ({placeholders})
                  AND expires_at > ?
                """,
                (*statuses, now_iso),
            ).fetchone()
        return int(row["count"] if row else 0)

    def list_expired_active(self, statuses: tuple[str, ...], now_iso: str) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in statuses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM leases
                WHERE status IN ({placeholders})
                  AND expires_at <= ?
                ORDER BY expires_at ASC
                """,
                (*statuses, now_iso),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_task(self, task: dict[str, Any]) -> None:
        columns = ", ".join(TASK_FIELDS)
        placeholders = ", ".join("?" for _ in TASK_FIELDS)
        values = [task.get(field) for field in TASK_FIELDS]

        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO tasks ({columns}) VALUES ({placeholders})",
                values,
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_tasks(self, instance_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if instance_id:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE instance_id = ? ORDER BY created_at DESC",
                    (instance_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def update_task(self, task_id: str, **fields: Any) -> bool:
        if not fields:
            return False

        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [task_id]
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE tasks SET {assignments} WHERE task_id = ?",
                params,
            )
        return cur.rowcount > 0
