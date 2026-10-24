package com.edgeledger.runtime.data

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "file_signatures",
    foreignKeys = [
        ForeignKey(
            entity = FileInventoryEntity::class,
            parentColumns = ["file_id"],
            childColumns = ["file_id"],
            onDelete = ForeignKey.CASCADE
        )
    ],
    indices = [
        Index(value = ["stage", "algorithm", "hash_value"], name = "idx_file_signatures_lookup"),
        Index(value = ["file_id", "stage", "algorithm"], name = "idx_file_signatures_file_stage", unique = true)
    ]
)
data class FileSignatureEntity(
    @PrimaryKey @ColumnInfo(name = "signature_id") val signatureId: String,
    @ColumnInfo(name = "file_id") val fileId: String,
    @ColumnInfo(name = "stage") val stage: Int, // 2 = Partial, 3 = Full
    @ColumnInfo(name = "algorithm") val algorithm: String, // 'SHA256'
    @ColumnInfo(name = "hash_value") val hashValue: String,
    @ColumnInfo(name = "sample_window_bytes") val sampleWindowBytes: Int,
    @ColumnInfo(name = "bytes_read") val bytesRead: Long,
    @ColumnInfo(name = "computed_at_mtime_epoch_ms") val computedAtMtimeEpochMs: Long,
    @ColumnInfo(name = "computed_at_size_bytes") val computedAtSizeBytes: Long,
    @ColumnInfo(name = "created_at_epoch_ms") val createdAtEpochMs: Long
)
