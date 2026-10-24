package com.edgeledger.runtime.data

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "file_inventory",
    indices = [
        Index(value = ["size_bytes"], name = "idx_file_inventory_size"),
        Index(value = ["path"], name = "idx_file_inventory_path", unique = true),
        Index(value = ["scan_status", "is_deleted"], name = "idx_file_inventory_scan")
    ]
)
data class FileInventoryEntity(
    @PrimaryKey @ColumnInfo(name = "file_id") val fileId: String,
    @ColumnInfo(name = "path") val path: String, // URI string for SAF or file path
    @ColumnInfo(name = "filename") val filename: String,
    @ColumnInfo(name = "extension") val extension: String,
    @ColumnInfo(name = "size_bytes") val sizeBytes: Long,
    @ColumnInfo(name = "last_modified") val lastModified: Long, // epoch ms
    @ColumnInfo(name = "scan_status") val scanStatus: String = "INDEXED", // INDEXED, UNREADABLE, STALE
    @ColumnInfo(name = "last_indexed_at") val lastIndexedAt: Long,
    @ColumnInfo(name = "storage_volume") val storageVolume: String = "saf_tree",
    @ColumnInfo(name = "relative_path") val relativePath: String = "",
    @ColumnInfo(name = "display_name") val displayName: String = "",
    @ColumnInfo(name = "mime_type") val mimeType: String? = null,
    @ColumnInfo(name = "ctime_epoch_ms") val ctimeEpochMs: Long? = null,
    @ColumnInfo(name = "is_deleted") val isDeleted: Int = 0,
    @ColumnInfo(name = "last_observed_epoch_ms") val lastObservedEpochMs: Long
)
