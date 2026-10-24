#!/usr/bin/env python3
"""
EdgeLedger Runtime — Local Index Ledger (SQLite Persistence)

Provides local SQLite metadata indexing and signature storage for file inventory
and cascading deduplication signatures, following docs/INDEX_LEDGER_SCHEMA.md.
"""

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional


def generate_file_id(path: str) -> str:
    """Generates a stable deterministic UUID-like file_id based on normalized path."""
    norm_path = os.path.abspath(path)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"file://{norm_path}"))


@dataclass
class IndexedFile:
    file_id: str
    path: str
    filename: str
    extension: str
    size_bytes: int
    last_modified: int  # epoch ms
    scan_status: str    # 'PENDING', 'INDEXED', 'UNREADABLE', 'STALE'
    last_indexed_at: int  # epoch ms
    storage_volume: str = "host"
    relative_path: str = ""
    display_name: str = ""
    mime_type: Optional[str] = None
    ctime_epoch_ms: Optional[int] = None
    is_deleted: int = 0
    last_observed_epoch_ms: int = 0


class IndexLedger:
    """SQLite-backed Index Ledger for EdgeLedger Runtime."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        with self.conn:
            if self.db_path != ":memory:":
                try:
                    self.conn.execute("PRAGMA journal_mode=WAL;")
                except sqlite3.Error:
                    pass
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self.conn.execute("PRAGMA foreign_keys=ON;")

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS file_inventory (
                file_id TEXT PRIMARY KEY NOT NULL,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                extension TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                last_modified INTEGER NOT NULL,
                scan_status TEXT NOT NULL DEFAULT 'PENDING',
                last_indexed_at INTEGER NOT NULL,
                storage_volume TEXT NOT NULL DEFAULT 'host',
                relative_path TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                mime_type TEXT,
                ctime_epoch_ms INTEGER,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                last_observed_epoch_ms INTEGER NOT NULL DEFAULT 0
            );
            """)

            self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_inventory_size 
            ON file_inventory(size_bytes) WHERE is_deleted = 0;
            """)

            self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_inventory_path 
            ON file_inventory(path);
            """)

            self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_inventory_scan 
            ON file_inventory(scan_status, is_deleted);
            """)

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS file_signatures (
                signature_id TEXT PRIMARY KEY NOT NULL,
                file_id TEXT NOT NULL,
                stage INTEGER NOT NULL,
                algorithm TEXT NOT NULL,
                hash_value TEXT NOT NULL,
                sample_window_bytes INTEGER NOT NULL,
                bytes_read INTEGER NOT NULL,
                computed_at_mtime_epoch_ms INTEGER NOT NULL,
                computed_at_size_bytes INTEGER NOT NULL,
                created_at_epoch_ms INTEGER NOT NULL,
                FOREIGN KEY (file_id) REFERENCES file_inventory(file_id) ON DELETE CASCADE
            );
            """)

            self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_signatures_lookup
            ON file_signatures(stage, algorithm, hash_value);
            """)

            self.conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_file_signatures_file_stage
            ON file_signatures(file_id, stage, algorithm);
            """)

    def close(self):
        if self.conn:
            self.conn.close()

    def upsert_file_metadata(
        self,
        path: str,
        size_bytes: int,
        last_modified: int,
        scan_status: str = "INDEXED",
        ctime_epoch_ms: Optional[int] = None,
        storage_volume: str = "host",
        relative_path: str = ""
    ) -> IndexedFile:
        abs_path = os.path.abspath(path)
        file_id = generate_file_id(abs_path)
        filename = os.path.basename(abs_path)
        _, ext = os.path.splitext(filename)
        now_ms = int(time.time() * 1000)

        display_name = filename
        if not relative_path:
            relative_path = abs_path

        indexed_file = IndexedFile(
            file_id=file_id,
            path=abs_path,
            filename=filename,
            extension=ext,
            size_bytes=size_bytes,
            last_modified=last_modified,
            scan_status=scan_status,
            last_indexed_at=now_ms,
            storage_volume=storage_volume,
            relative_path=relative_path,
            display_name=display_name,
            ctime_epoch_ms=ctime_epoch_ms,
            is_deleted=0,
            last_observed_epoch_ms=now_ms
        )

        with self.conn:
            self.conn.execute("""
            INSERT INTO file_inventory (
                file_id, path, filename, extension, size_bytes, last_modified,
                scan_status, last_indexed_at, storage_volume, relative_path,
                display_name, ctime_epoch_ms, is_deleted, last_observed_epoch_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(path) DO UPDATE SET
                size_bytes = excluded.size_bytes,
                last_modified = excluded.last_modified,
                scan_status = excluded.scan_status,
                last_indexed_at = excluded.last_indexed_at,
                is_deleted = 0,
                last_observed_epoch_ms = excluded.last_observed_epoch_ms;
            """, (
                indexed_file.file_id, indexed_file.path, indexed_file.filename,
                indexed_file.extension, indexed_file.size_bytes, indexed_file.last_modified,
                indexed_file.scan_status, indexed_file.last_indexed_at, indexed_file.storage_volume,
                indexed_file.relative_path, indexed_file.display_name, indexed_file.ctime_epoch_ms,
                indexed_file.last_observed_epoch_ms
            ))

        return indexed_file

    def record_unreadable_file(self, path: str, reason: str = ""):
        abs_path = os.path.abspath(path)
        file_id = generate_file_id(abs_path)
        filename = os.path.basename(abs_path)
        _, ext = os.path.splitext(filename)
        now_ms = int(time.time() * 1000)

        with self.conn:
            self.conn.execute("""
            INSERT INTO file_inventory (
                file_id, path, filename, extension, size_bytes, last_modified,
                scan_status, last_indexed_at, storage_volume, relative_path,
                display_name, is_deleted, last_observed_epoch_ms
            ) VALUES (?, ?, ?, ?, 0, 0, 'UNREADABLE', ?, 'host', ?, ?, 0, ?)
            ON CONFLICT(path) DO UPDATE SET
                scan_status = 'UNREADABLE',
                last_indexed_at = excluded.last_indexed_at,
                last_observed_epoch_ms = excluded.last_observed_epoch_ms;
            """, (
                file_id, abs_path, filename, ext, now_ms, abs_path, filename, now_ms
            ))

    def get_file_by_path(self, path: str) -> Optional[IndexedFile]:
        abs_path = os.path.abspath(path)
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM file_inventory WHERE path = ? AND is_deleted = 0", (abs_path,))
        row = cursor.fetchone()
        if not row:
            return None
        return IndexedFile(**dict(row))

    def get_all_active_files(self) -> List[IndexedFile]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM file_inventory WHERE is_deleted = 0 AND scan_status != 'UNREADABLE'")
        return [IndexedFile(**dict(row)) for row in cursor.fetchall()]

    def get_stage1_candidate_sizes(self) -> List[int]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT size_bytes
            FROM file_inventory
            WHERE is_deleted = 0 AND scan_status != 'UNREADABLE'
            GROUP BY size_bytes
            HAVING COUNT(*) > 1
        """)
        return [row[0] for row in cursor.fetchall()]

    def get_files_by_size(self, size_bytes: int) -> List[IndexedFile]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM file_inventory
            WHERE size_bytes = ? AND is_deleted = 0 AND scan_status != 'UNREADABLE'
        """, (size_bytes,))
        return [IndexedFile(**dict(row)) for row in cursor.fetchall()]

    def save_signature(
        self,
        file_id: str,
        stage: int,
        algorithm: str,
        hash_value: str,
        sample_window_bytes: int,
        bytes_read: int,
        computed_at_mtime_epoch_ms: int,
        computed_at_size_bytes: int
    ):
        sig_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        with self.conn:
            self.conn.execute("""
            INSERT INTO file_signatures (
                signature_id, file_id, stage, algorithm, hash_value,
                sample_window_bytes, bytes_read, computed_at_mtime_epoch_ms,
                computed_at_size_bytes, created_at_epoch_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id, stage, algorithm) DO UPDATE SET
                hash_value = excluded.hash_value,
                sample_window_bytes = excluded.sample_window_bytes,
                bytes_read = excluded.bytes_read,
                computed_at_mtime_epoch_ms = excluded.computed_at_mtime_epoch_ms,
                computed_at_size_bytes = excluded.computed_at_size_bytes,
                created_at_epoch_ms = excluded.created_at_epoch_ms;
            """, (
                sig_id, file_id, stage, algorithm, hash_value,
                sample_window_bytes, bytes_read, computed_at_mtime_epoch_ms,
                computed_at_size_bytes, now_ms
            ))

    def get_valid_signature(
        self,
        file_id: str,
        stage: int,
        algorithm: str,
        current_mtime_epoch_ms: int,
        current_size_bytes: int
    ) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM file_signatures
            WHERE file_id = ? AND stage = ? AND algorithm = ?
        """, (file_id, stage, algorithm))
        row = cursor.fetchone()
        if not row:
            return None
        sig = dict(row)
        if (sig["computed_at_mtime_epoch_ms"] == current_mtime_epoch_ms and
                sig["computed_at_size_bytes"] == current_size_bytes):
            return sig
        return None
