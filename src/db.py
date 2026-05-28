"""PowerMesh — SQLite schema, migrations, and query helpers."""

from __future__ import annotations

import sqlite3
import threading
import csv
import io
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

_SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS node_config (
    node_id       TEXT PRIMARY KEY,
    hostname      TEXT,
    node_ip       TEXT,
    os            TEXT,
    cpu_tdp_w     REAL DEFAULT 65,
    gpu_tdp_w     REAL DEFAULT 0,
    base_power_w  REAL DEFAULT 35,
    psu_wattage   REAL DEFAULT 650,
    psu_rating    TEXT DEFAULT 'bronze',
    psu_efficiency REAL DEFAULT 0.85,
    cost_per_kwh  REAL DEFAULT 0.12,
    currency      TEXT DEFAULT 'USD',
    has_smart_plug INTEGER DEFAULT 0,
    smart_plug_ip TEXT,
    last_seen     TEXT,
    created_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS power_readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    node_id         TEXT NOT NULL,
    node_ip         TEXT,
    cpu_power_w     REAL,
    cpu_util_pct    REAL,
    cpu_method      TEXT,
    gpu_count       INTEGER DEFAULT 0,
    gpu_power_w     REAL DEFAULT 0,
    gpu_util_pct    REAL DEFAULT 0,
    gpu_vram_used_mb REAL DEFAULT 0,
    gpu_temp_c      REAL DEFAULT 0,
    ram_used_gb     REAL,
    ram_total_gb    REAL,
    disk_io_read_mb REAL DEFAULT 0,
    disk_io_write_mb REAL DEFAULT 0,
    net_sent_mb     REAL DEFAULT 0,
    net_recv_mb     REAL DEFAULT 0,
    total_power_w   REAL,
    psu_efficiency  REAL DEFAULT 0.85,
    wall_power_w    REAL,
    UNIQUE(node_id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_readings_node_ts
    ON power_readings(node_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_readings_ts
    ON power_readings(timestamp);

CREATE TABLE IF NOT EXISTS power_aggregates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id       TEXT NOT NULL,
    period_start  TEXT NOT NULL,
    period_end    TEXT,
    period_type   TEXT NOT NULL,
    avg_power_w   REAL,
    max_power_w   REAL,
    min_power_w   REAL,
    avg_cpu_w     REAL DEFAULT 0,
    avg_gpu_w     REAL DEFAULT 0,
    avg_cpu_util  REAL DEFAULT 0,
    avg_gpu_util  REAL DEFAULT 0,
    avg_gpu_temp  REAL DEFAULT 0,
    energy_wh     REAL,
    cost          REAL,
    currency      TEXT DEFAULT 'USD',
    reading_count INTEGER DEFAULT 0,
    UNIQUE(node_id, period_start, period_type)
);

CREATE INDEX IF NOT EXISTS idx_agg_node_period
    ON power_aggregates(node_id, period_start, period_type);
"""


class PowerDB:
    """Thread-safe SQLite wrapper for power monitoring data."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._get_conn().execute(sql, params)

    def executemany(self, sql: str, params_seq: list[tuple]) -> None:
        conn = self._get_conn()
        conn.executemany(sql, params_seq)
        conn.commit()

    def fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        row = self.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    def _init_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA_SQL)
        self._ensure_column("node_config", "hostname", "TEXT")
        row = conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
            conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        conn = self._get_conn()
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            conn.commit()

    # ── Reading inserts ──────────────────────────────────────────────

    def insert_reading(self, reading: dict[str, Any]) -> None:
        cols = [
            "timestamp", "node_id", "node_ip",
            "cpu_power_w", "cpu_util_pct", "cpu_method",
            "gpu_count", "gpu_power_w", "gpu_util_pct",
            "gpu_vram_used_mb", "gpu_temp_c",
            "ram_used_gb", "ram_total_gb",
            "disk_io_read_mb", "disk_io_write_mb",
            "net_sent_mb", "net_recv_mb",
            "total_power_w", "psu_efficiency", "wall_power_w",
        ]
        present = [c for c in cols if c in reading]
        placeholders = ", ".join("?" for _ in present)
        col_names = ", ".join(present)
        values = tuple(reading[c] for c in present)
        self.execute(
            f"INSERT OR REPLACE INTO power_readings ({col_names}) VALUES ({placeholders})",
            values,
        )
        self._get_conn().commit()

    def insert_readings_batch(self, readings: list[dict[str, Any]]) -> None:
        if not readings:
            return
        cols = [
            "timestamp", "node_id", "node_ip",
            "cpu_power_w", "cpu_util_pct", "cpu_method",
            "gpu_count", "gpu_power_w", "gpu_util_pct",
            "gpu_vram_used_mb", "gpu_temp_c",
            "ram_used_gb", "ram_total_gb",
            "disk_io_read_mb", "disk_io_write_mb",
            "net_sent_mb", "net_recv_mb",
            "total_power_w", "psu_efficiency", "wall_power_w",
        ]
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        rows = []
        for r in readings:
            rows.append(tuple(r.get(c) for c in cols))
        self.executemany(
            f"INSERT OR REPLACE INTO power_readings ({col_names}) VALUES ({placeholders})",
            rows,
        )

    # ── Node config ──────────────────────────────────────────────────

    def upsert_node(self, node: dict[str, Any]) -> None:
        cols = [
            "node_id", "hostname", "node_ip", "os",
            "cpu_tdp_w", "gpu_tdp_w", "base_power_w",
            "psu_wattage", "psu_rating", "psu_efficiency",
            "cost_per_kwh", "currency",
            "has_smart_plug", "smart_plug_ip", "last_seen",
        ]
        present = [c for c in cols if c in node]
        placeholders = ", ".join("?" for _ in present)
        col_names = ", ".join(present)
        update_cols = [c for c in present if c != "node_id"]
        values = tuple(node[c] for c in present)
        if update_cols:
            updates = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
            sql = (
                f"INSERT INTO node_config ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT(node_id) DO UPDATE SET {updates}"
            )
        else:
            sql = (
                f"INSERT OR IGNORE INTO node_config ({col_names}) VALUES ({placeholders})"
            )
        self.execute(sql, values)
        self._get_conn().commit()

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM node_config WHERE node_id = ?", (node_id,))

    def get_all_nodes(self) -> list[dict[str, Any]]:
        return self.fetchall("SELECT * FROM node_config ORDER BY node_id")

    def get_all_node_ids(self) -> list[str]:
        rows = self.fetchall("SELECT node_id FROM node_config ORDER BY node_id")
        return [r["node_id"] for r in rows]

    # ── Reading queries ──────────────────────────────────────────────

    def get_latest_reading(self, node_id: str) -> dict[str, Any] | None:
        return self.fetchone(
            "SELECT * FROM power_readings WHERE node_id = ? ORDER BY timestamp DESC LIMIT 1",
            (node_id,),
        )

    def get_oldest_reading_time(self, node_id: str) -> str | None:
        row = self.fetchone(
            "SELECT MIN(timestamp) as ts FROM power_readings WHERE node_id = ?",
            (node_id,),
        )
        return row["ts"] if row and row["ts"] else None

    def get_readings_in_range(
        self, node_id: str, from_ts: str, to_ts: str
    ) -> list[dict[str, Any]]:
        return self.fetchall(
            "SELECT * FROM power_readings WHERE node_id = ? AND timestamp >= ? AND timestamp < ? "
            "ORDER BY timestamp",
            (node_id, from_ts, to_ts),
        )

    def get_latest_readings(self) -> list[dict[str, Any]]:
        return self.fetchall("""
            SELECT r.* FROM power_readings r
            INNER JOIN (
                SELECT node_id, MAX(timestamp) as max_ts
                FROM power_readings GROUP BY node_id
            ) latest ON r.node_id = latest.node_id AND r.timestamp = latest.max_ts
            ORDER BY r.node_id
        """)

    def get_readings(
        self,
        node_id: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        conditions = []
        params: list[Any] = []
        if node_id:
            conditions.append("node_id = ?")
            params.append(node_id)
        if from_ts:
            conditions.append("timestamp >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("timestamp <= ?")
            params.append(to_ts)
        where = " AND ".join(conditions) if conditions else "1=1"
        return self.fetchall(
            f"SELECT * FROM power_readings WHERE {where} ORDER BY timestamp DESC LIMIT ?",
            tuple(params) + (limit,),
        )

    # ── Aggregates ───────────────────────────────────────────────────

    def get_latest_aggregate(
        self, node_id: str, period_type: str
    ) -> dict[str, Any] | None:
        return self.fetchone(
            "SELECT * FROM power_aggregates WHERE node_id = ? AND period_type = ? "
            "ORDER BY period_start DESC LIMIT 1",
            (node_id, period_type),
        )

    def insert_aggregate(self, agg: dict[str, Any]) -> None:
        cols = [
            "node_id", "period_start", "period_end", "period_type",
            "avg_power_w", "max_power_w", "min_power_w",
            "avg_cpu_w", "avg_gpu_w",
            "avg_cpu_util", "avg_gpu_util", "avg_gpu_temp",
            "energy_wh", "cost", "currency", "reading_count",
        ]
        present = [c for c in cols if c in agg]
        placeholders = ", ".join("?" for _ in present)
        col_names = ", ".join(present)
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in present
            if c not in ("node_id", "period_start", "period_type")
        )
        values = tuple(agg[c] for c in present)
        self.execute(
            f"INSERT INTO power_aggregates ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(node_id, period_start, period_type) DO UPDATE SET {updates}",
            values,
        )
        self._get_conn().commit()

    def get_aggregates(
        self,
        node_id: str | None = None,
        period_type: str = "hourly",
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        conditions = ["period_type = ?"]
        params: list[Any] = [period_type]
        if node_id:
            conditions.append("node_id = ?")
            params.append(node_id)
        if from_ts:
            conditions.append("period_start >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("period_start <= ?")
            params.append(to_ts)
        where = " AND ".join(conditions)
        return self.fetchall(
            f"SELECT * FROM power_aggregates WHERE {where} "
            f"ORDER BY period_start DESC LIMIT ?",
            tuple(params) + (limit,),
        )

    # ── Export helpers ───────────────────────────────────────────────

    def export_readings_csv(
        self,
        node_id: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 100000,
    ) -> str:
        rows = self.get_readings(node_id=node_id, from_ts=from_ts, to_ts=to_ts, limit=limit)
        if not rows:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(reversed(rows))
        return output.getvalue()

    # ── Maintenance ──────────────────────────────────────────────────

    def prune_readings(self, older_than: str) -> int:
        with self.transaction() as cur:
            cur.execute(
                "DELETE FROM power_readings WHERE timestamp < ?", (older_than,)
            )
            return cur.rowcount

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
