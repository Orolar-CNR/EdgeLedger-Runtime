# Index Ledger Schema Specification

## 1. Overview & Storage Engine Configuration

The **Index Ledger** is the local persistence layer responsible for storing file inventory metadata and calculated signature hashes. On Android runtime environments, this is backed by an SQLite database accessed via Room.

### Database Settings
* **Engine:** SQLite 3.x
* **Journal Mode:** Write-Ahead Logging (`PRAGMA journal_mode=WAL;`)
* **Synchronous Setting:** `PRAGMA synchronous=NORMAL;`
* **Foreign Key Constraints:** Enabled (`PRAGMA foreign_keys=ON;`)
* **Rationale:** WAL mode allows concurrent read operations while background worker threads write new metadata and hash results. `synchronous=NORMAL` reduces disk sync overhead while preserving integrity across application crashes.

---

## 2. Table Schemas & SQL DDL

### 2.1 `file_inventory` Table
Stores metadata for all discovered or observed files within accessible storage boundaries.

```sql
CREATE TABLE IF NOT EXISTS file_inventory (
    file_id TEXT PRIMARY KEY NOT NULL,             -- Unique UUID or platform path hash
    storage_volume TEXT NOT NULL,                  -- e.g. 'primary', 'sdcard', 'app_internal'
    relative_path TEXT NOT NULL,                   -- Relative path within volume scope
    display_name TEXT NOT NULL,                    -- File name with extension
    size_bytes INTEGER NOT NULL,                   -- Exact file size in bytes
    mime_type TEXT,                                -- MIME type if available
    mtime_epoch_ms INTEGER NOT NULL,               -- File last modified timestamp in epoch ms
    ctime_epoch_ms INTEGER,                        -- Creation timestamp in epoch ms (if available)
    is_deleted INTEGER NOT NULL DEFAULT 0,         -- Soft-deletion marker (0 = active, 1 = deleted)
    last_observed_epoch_ms INTEGER NOT NULL,       -- Timestamp when last confirmed present
    sync_status TEXT NOT NULL DEFAULT 'PENDING'    -- PENDING, SYNCED, STALE, UNREADABLE
);

CREATE INDEX IF NOT EXISTS idx_file_inventory_size ON file_inventory(size_bytes) WHERE is_deleted = 0;
CREATE INDEX IF NOT EXISTS idx_file_inventory_path ON file_inventory(storage_volume, relative_path);
CREATE INDEX IF NOT EXISTS idx_file_inventory_sync ON file_inventory(sync_status, is_deleted);
```

### 2.2 `file_signatures` Table
Stores staged hash signatures calculated during the cascading deduplication pipeline.

```sql
CREATE TABLE IF NOT EXISTS file_signatures (
    signature_id TEXT PRIMARY KEY NOT NULL,        -- UUID
    file_id TEXT NOT NULL,                         -- Foreign key referencing file_inventory(file_id)
    stage INTEGER NOT NULL,                        -- Stage level: 2 (Partial Hash), 3 (Full Hash)
    algorithm TEXT NOT NULL,                       -- Algorithm identifier (e.g., 'BLAKE2b-128', 'XXH3-128')
    hash_value TEXT NOT NULL,                      -- Hex-encoded hash output
    sample_window_bytes INTEGER NOT NULL,          -- Sample size per window (e.g., 8192 for 8 KiB)
    bytes_read INTEGER NOT NULL,                   -- Total bytes read to produce this signature
    computed_at_mtime_epoch_ms INTEGER NOT NULL,   -- File mtime snapshot when hash was computed
    computed_at_size_bytes INTEGER NOT NULL,       -- File size snapshot when hash was computed
    created_at_epoch_ms INTEGER NOT NULL,          -- Timestamp when row was inserted
    FOREIGN KEY (file_id) REFERENCES file_inventory(file_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_file_signatures_lookup
ON file_signatures(stage, algorithm, hash_value);

CREATE UNIQUE INDEX IF NOT EXISTS idx_file_signatures_file_stage
ON file_signatures(file_id, stage, algorithm);
```

---

## 3. Snapshot Consistency & Invalidation Rules

To prevent stale signature matches (e.g., if a file is edited after its signature was computed), every signature entry stores snapshot validation metadata: `computed_at_mtime_epoch_ms` and `computed_at_size_bytes`.

### Invalidation Logic
When querying or utilizing a signature from `file_signatures`:
1. The system joins `file_signatures` with `file_inventory`.
2. A signature is considered **VALID** if and only if:
   $$\text{file\_inventory.mtime\_epoch\_ms} == \text{file\_signatures.computed\_at\_mtime\_epoch\_ms}$$
   $$\text{and } \text{file\_inventory.size\_bytes} == \text{file\_signatures.computed\_at\_size\_bytes}$$
3. If `mtime` or `size` differs, the signature is marked **STALE**. Stale signatures are purged and recalculated on demand.

---

## 4. Transaction Semantics & Worker Interrupt Recovery

1. **Batch Atomic Commit:** Signature updates are written in batch transactions (e.g., 50 files per batch transaction).
2. **Worker Cancellation:** If a background worker (e.g., Android WorkManager or Python background process) is interrupted, uncommitted in-flight hash calculations are discarded without corrupting committed signatures.
3. **Resumability:** Upon restart, the system queries `file_inventory` for files lacking valid Stage 2 or Stage 3 signatures and resumes computation from the last committed batch.
