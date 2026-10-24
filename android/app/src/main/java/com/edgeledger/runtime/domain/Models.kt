package com.edgeledger.runtime.domain

import android.net.Uri

data class DocumentMetadata(
    val uri: Uri,
    val displayName: String,
    val sizeBytes: Long,
    val lastModifiedEpochMs: Long,
    val mimeType: String?,
    val isDirectory: Boolean
)

data class StorageCapacityInfo(
    val targetUri: String = "",
    val totalCapacityBytes: Long? = null,
    val usedBytes: Long? = null,
    val availableBytes: Long? = null,
    val status: String = "AVAILABLE"
)

data class StageMetrics(
    val filesEntered: Int = 0,
    val candidateFilesRemaining: Int = 0,
    val bytesRead: Long = 0L,
    val elapsedMs: Double = 0.0
)

data class DuplicateGroupDetails(
    val groupId: String,
    val fileCount: Int,
    val fileUris: List<String>,
    val fileNames: List<String>,
    val fileSizeByes: Long,
    val totalGroupSizeBytes: Long,
    val retainedCopySizeBytes: Long,
    val potentialRecoverableBytes: Long
)

data class MemoryMetrics(
    val peakHeapUsedBytes: Long = 0L,
    val maxHeapLimitBytes: Long = 0L,
    val note: String = "Runtime.getRuntime() heap memory tracking"
)

data class DeduplicationReport(
    val timestampEpochMs: Long = System.currentTimeMillis(),
    val hashAlgorithm: String = "SHA256",
    val sampleWindowBytes: Int = 8192,
    val storageCapacity: StorageCapacityInfo = StorageCapacityInfo(),
    val totalFilesDiscovered: Int = 0,
    val totalBytesDiscovered: Long = 0L,
    val unreadableFilesCount: Int = 0,
    val skippedModifiedFilesCount: Int = 0,
    val stage1Metrics: StageMetrics = StageMetrics(),
    val stage2Metrics: StageMetrics = StageMetrics(),
    val stage3Metrics: StageMetrics = StageMetrics(),
    val duplicateGroupsConfirmed: Int = 0,
    val duplicateFilesCount: Int = 0,
    val totalBytesRead: Long = 0L,
    val overallByteReductionPercentage: Double = 0.0,
    val aggregatePotentialRecoverableBytes: Long = 0L,
    val totalElapsedMs: Double = 0.0,
    val memoryMetrics: MemoryMetrics = MemoryMetrics(),
    val duplicateGroups: List<DuplicateGroupDetails> = emptyList(),
    val errors: List<String> = emptyList()
)

sealed class ScanState {
    object Idle : ScanState()
    data class Scanning(
        val currentFile: String,
        val filesDiscovered: Int,
        val currentStage: String,
        val bytesReadSoFar: Long
    ) : ScanState()
    data class Completed(val report: DeduplicationReport) : ScanState()
    data class Error(val message: String) : ScanState()
    object Cancelled : ScanState()
}
