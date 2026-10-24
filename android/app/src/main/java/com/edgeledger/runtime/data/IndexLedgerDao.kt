package com.edgeledger.runtime.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction

@Dao
interface IndexLedgerDao {

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertInventoryIgnore(file: FileInventoryEntity): Long

    @Query("""
        UPDATE file_inventory 
        SET filename = :filename, extension = :extension, size_bytes = :sizeBytes, 
            last_modified = :lastModified, scan_status = :scanStatus, 
            last_indexed_at = :lastIndexedAt, display_name = :displayName, 
            mime_type = :mimeType, is_deleted = 0, last_observed_epoch_ms = :lastObservedEpochMs
        WHERE path = :path
    """)
    suspend fun updateInventoryByPath(
        path: String,
        filename: String,
        extension: String,
        sizeBytes: Long,
        lastModified: Long,
        scanStatus: String,
        lastIndexedAt: Long,
        displayName: String,
        mimeType: String?,
        lastObservedEpochMs: Long
    )

    @Transaction
    suspend fun upsertInventory(file: FileInventoryEntity) {
        val rowId = insertInventoryIgnore(file)
        if (rowId == -1L) {
            updateInventoryByPath(
                path = file.path,
                filename = file.filename,
                extension = file.extension,
                sizeBytes = file.sizeBytes,
                lastModified = file.lastModified,
                scanStatus = file.scanStatus,
                lastIndexedAt = file.lastIndexedAt,
                displayName = file.displayName,
                mimeType = file.mimeType,
                lastObservedEpochMs = file.lastObservedEpochMs
            )
        }
    }

    @Query("SELECT * FROM file_inventory WHERE path = :path AND is_deleted = 0 LIMIT 1")
    suspend fun getFileByPath(path: String): FileInventoryEntity?

    @Query("SELECT * FROM file_inventory WHERE is_deleted = 0 AND scan_status != 'UNREADABLE'")
    suspend fun getAllActiveFiles(): List<FileInventoryEntity>

    @Query("""
        SELECT size_bytes 
        FROM file_inventory 
        WHERE is_deleted = 0 AND scan_status != 'UNREADABLE'
        GROUP BY size_bytes 
        HAVING COUNT(*) > 1
    """)
    suspend fun getStage1CandidateSizes(): List<Long>

    @Query("SELECT * FROM file_inventory WHERE size_bytes = :sizeBytes AND is_deleted = 0 AND scan_status != 'UNREADABLE'")
    suspend fun getFilesBySize(sizeBytes: Long): List<FileInventoryEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveSignature(signature: FileSignatureEntity)

    @Query("""
        SELECT * FROM file_signatures 
        WHERE file_id = :fileId AND stage = :stage AND algorithm = :algorithm 
        LIMIT 1
    """)
    suspend fun getSignature(fileId: String, stage: Int, algorithm: String): FileSignatureEntity?

    @Query("DELETE FROM file_inventory")
    suspend fun clearInventory()
}
