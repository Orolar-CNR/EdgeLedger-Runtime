package com.edgeledger.runtime.domain

import android.content.Context
import android.net.Uri
import com.edgeledger.runtime.data.AppDatabase
import com.edgeledger.runtime.data.FileInventoryEntity
import com.edgeledger.runtime.data.FileSignatureEntity
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import java.security.MessageDigest
import java.util.UUID

class DeduplicationEngine(
    private val context: Context,
    private val database: AppDatabase
) {
    private val scanner = SafScanner(context)
    private val dao = database.indexLedgerDao()

    suspend fun runPipeline(
        treeUri: Uri,
        sampleWindowBytes: Int = 8192,
        onProgress: (ScanState.Scanning) -> Unit
    ): DeduplicationReport {
        val startTimeTotal = System.currentTimeMillis()
        val errors = mutableListOf<String>()

        var discoveredCount = 0
        var totalDiscoveredBytes = 0L
        val activePathSet = mutableSetOf<String>()

        scanner.scanDirectory(treeUri).collect { doc ->
            currentCoroutineContext().ensureActive()
            discoveredCount++
            totalDiscoveredBytes += doc.sizeBytes
            val fileUriStr = doc.uri.toString()
            activePathSet.add(fileUriStr)

            val fileId = UUID.nameUUIDFromBytes(fileUriStr.toByteArray()).toString()
            val entity = FileInventoryEntity(
                fileId = fileId,
                path = fileUriStr,
                filename = doc.displayName,
                extension = doc.displayName.substringAfterLast('.', ""),
                sizeBytes = doc.sizeBytes,
                lastModified = doc.lastModifiedEpochMs,
                scanStatus = "INDEXED",
                lastIndexedAt = System.currentTimeMillis(),
                displayName = doc.displayName,
                mimeType = doc.mimeType,
                lastObservedEpochMs = System.currentTimeMillis()
            )
            dao.upsertInventory(entity)

            onProgress(
                ScanState.Scanning(
                    currentFile = doc.displayName,
                    filesDiscovered = discoveredCount,
                    currentStage = "Metadata Indexing",
                    bytesReadSoFar = 0L
                )
            )
        }

        val allActiveFiles = dao.getAllActiveFiles()

        // Stage 1: Size correlation
        val t1Start = System.currentTimeMillis()
        val candidateSizes = dao.getStage1CandidateSizes()
        val stage1Candidates = mutableListOf<FileInventoryEntity>()
        for (sz in candidateSizes) {
            stage1Candidates.addAll(dao.getFilesBySize(sz))
        }
        val stage1Ms = (System.currentTimeMillis() - t1Start).toDouble()

        if (stage1Candidates.isEmpty()) {
            return DeduplicationReport(
                totalFilesDiscovered = discoveredCount,
                totalBytesDiscovered = totalDiscoveredBytes,
                stage1Metrics = StageMetrics(allActiveFiles.size, 0, 0L, stage1Ms),
                totalElapsedMs = (System.currentTimeMillis() - startTimeTotal).toDouble()
            )
        }

        // Stage 2: Head-Tail Partial Hash (with cache verification & reuse)
        val t2Start = System.currentTimeMillis()
        var stage2BytesRead = 0L
        val partialHashGroups = mutableMapOf<Pair<Long, String>, MutableList<FileInventoryEntity>>()

        for (f in stage1Candidates) {
            currentCoroutineContext().ensureActive()
            onProgress(
                ScanState.Scanning(
                    currentFile = f.filename,
                    filesDiscovered = discoveredCount,
                    currentStage = "Stage 2: Partial Hash",
                    bytesReadSoFar = stage2BytesRead
                )
            )

            val uri = Uri.parse(f.path)

            val cachedSig = dao.getSignature(f.fileId, stage = 2, algorithm = "SHA256")
            var partHashHex: String? = null

            if (cachedSig != null &&
                (f.lastModified == 0L || cachedSig.computedAtMtimeEpochMs == f.lastModified) &&
                cachedSig.computedAtSizeBytes == f.sizeBytes
            ) {
                partHashHex = cachedSig.hashValue
            } else {
                val (bytes, bytesRead) = scanner.readPartialBytes(uri, f.sizeBytes, sampleWindowBytes)
                stage2BytesRead += bytesRead

                if (bytes != null) {
                    val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
                    partHashHex = digest.joinToString("") { "%02x".format(it) }

                    dao.saveSignature(
                        FileSignatureEntity(
                            signatureId = UUID.randomUUID().toString(),
                            fileId = f.fileId,
                            stage = 2,
                            algorithm = "SHA256",
                            hashValue = partHashHex,
                            sampleWindowBytes = sampleWindowBytes,
                            bytesRead = bytesRead,
                            computedAtMtimeEpochMs = f.lastModified,
                            computedAtSizeBytes = f.sizeBytes,
                            createdAtEpochMs = System.currentTimeMillis()
                        )
                    )
                } else {
                    errors.add("Stage 2 read error for ${f.filename}")
                }
            }

            if (partHashHex != null) {
                val key = Pair(f.sizeBytes, partHashHex)
                partialHashGroups.getOrPut(key) { mutableListOf() }.add(f)
            }
        }

        val stage2Candidates = mutableListOf<FileInventoryEntity>()
        for ((_, group) in partialHashGroups) {
            if (group.size > 1) {
                stage2Candidates.addAll(group)
            }
        }
        val stage2Ms = (System.currentTimeMillis() - t2Start).toDouble()

        if (stage2Candidates.isEmpty()) {
            return DeduplicationReport(
                totalFilesDiscovered = discoveredCount,
                totalBytesDiscovered = totalDiscoveredBytes,
                stage1Metrics = StageMetrics(allActiveFiles.size, stage1Candidates.size, 0L, stage1Ms),
                stage2Metrics = StageMetrics(stage1Candidates.size, 0, stage2BytesRead, stage2Ms),
                totalBytesRead = stage2BytesRead,
                totalElapsedMs = (System.currentTimeMillis() - startTimeTotal).toDouble(),
                errors = errors
            )
        }

        // Stage 3: Full SHA-256 Hash (with cache verification & reuse)
        val t3Start = System.currentTimeMillis()
        var stage3BytesRead = 0L
        val fullHashGroups = mutableMapOf<Pair<Long, String>, MutableList<FileInventoryEntity>>()

        for (f in stage2Candidates) {
            currentCoroutineContext().ensureActive()
            onProgress(
                ScanState.Scanning(
                    currentFile = f.filename,
                    filesDiscovered = discoveredCount,
                    currentStage = "Stage 3: Full SHA-256 Hash",
                    bytesReadSoFar = stage2BytesRead + stage3BytesRead
                )
            )

            val uri = Uri.parse(f.path)

            val cachedSig2 = dao.getSignature(f.fileId, stage = 2, algorithm = "SHA256")
            val cachedSig3 = dao.getSignature(f.fileId, stage = 3, algorithm = "SHA256")
            var fullHash: String? = null

            if (cachedSig3 != null &&
                (f.lastModified == 0L || cachedSig3.computedAtMtimeEpochMs == f.lastModified) &&
                cachedSig3.computedAtSizeBytes == f.sizeBytes
            ) {
                fullHash = cachedSig3.hashValue
            } else if (cachedSig2 != null &&
                (f.lastModified == 0L || cachedSig2.computedAtMtimeEpochMs == f.lastModified) &&
                cachedSig2.computedAtSizeBytes == f.sizeBytes &&
                f.sizeBytes <= 2 * sampleWindowBytes
            ) {
                fullHash = cachedSig2.hashValue
                dao.saveSignature(
                    FileSignatureEntity(
                        signatureId = UUID.randomUUID().toString(),
                        fileId = f.fileId,
                        stage = 3,
                        algorithm = "SHA256",
                        hashValue = fullHash,
                        sampleWindowBytes = sampleWindowBytes,
                        bytesRead = 0L,
                        computedAtMtimeEpochMs = f.lastModified,
                        computedAtSizeBytes = f.sizeBytes,
                        createdAtEpochMs = System.currentTimeMillis()
                    )
                )
            } else {
                val (hashResult, bytesRead) = scanner.computeFullHash(uri)
                stage3BytesRead += bytesRead

                if (hashResult != null) {
                    fullHash = hashResult
                    dao.saveSignature(
                        FileSignatureEntity(
                            signatureId = UUID.randomUUID().toString(),
                            fileId = f.fileId,
                            stage = 3,
                            algorithm = "SHA256",
                            hashValue = fullHash,
                            sampleWindowBytes = sampleWindowBytes,
                            bytesRead = bytesRead,
                            computedAtMtimeEpochMs = f.lastModified,
                            computedAtSizeBytes = f.sizeBytes,
                            createdAtEpochMs = System.currentTimeMillis()
                        )
                    )
                } else {
                    errors.add("Stage 3 read error for ${f.filename}")
                }
            }

            if (fullHash != null) {
                val key = Pair(f.sizeBytes, fullHash)
                fullHashGroups.getOrPut(key) { mutableListOf() }.add(f)
            }
        }

        val confirmedGroups = mutableListOf<DuplicateGroupDetails>()
        var groupIdx = 1
        var totalDupFiles = 0
        var totalRecoverable = 0L

        for ((key, group) in fullHashGroups) {
            if (group.size > 1) {
                val fileSize = key.first
                val totalGroupSize = fileSize * group.size
                val retainedSize = fileSize
                val recoverable = totalGroupSize - retainedSize
                totalRecoverable += recoverable
                totalDupFiles += group.size

                confirmedGroups.add(
                    DuplicateGroupDetails(
                        groupId = "group_$groupIdx",
                        fileCount = group.size,
                        fileUris = group.map { it.path },
                        fileNames = group.map { it.filename },
                        fileSizeByes = fileSize,
                        totalGroupSizeBytes = totalGroupSize,
                        retainedCopySizeBytes = retainedSize,
                        potentialRecoverableBytes = recoverable
                    )
                )
                groupIdx++
            }
        }

        val stage3Ms = (System.currentTimeMillis() - t3Start).toDouble()
        val totalBytesRead = stage2BytesRead + stage3BytesRead
        val totalMs = (System.currentTimeMillis() - startTimeTotal).toDouble()

        val runtime = Runtime.getRuntime()
        val usedHeap = runtime.totalMemory() - runtime.freeMemory()

        return DeduplicationReport(
            totalFilesDiscovered = discoveredCount,
            totalBytesDiscovered = totalDiscoveredBytes,
            stage1Metrics = StageMetrics(allActiveFiles.size, stage1Candidates.size, 0L, stage1Ms),
            stage2Metrics = StageMetrics(stage1Candidates.size, stage2Candidates.size, stage2BytesRead, stage2Ms),
            stage3Metrics = StageMetrics(stage2Candidates.size, totalDupFiles, stage3BytesRead, stage3Ms),
            duplicateGroupsConfirmed = confirmedGroups.size,
            duplicateFilesCount = totalDupFiles,
            totalBytesRead = totalBytesRead,
            overallByteReductionPercentage = if (totalDiscoveredBytes > 0) (1.0 - (totalBytesRead.toDouble() / totalDiscoveredBytes)) * 100.0 else 0.0,
            aggregatePotentialRecoverableBytes = totalRecoverable,
            totalElapsedMs = totalMs,
            memoryMetrics = MemoryMetrics(peakHeapUsedBytes = usedHeap, maxHeapLimitBytes = runtime.maxMemory()),
            duplicateGroups = confirmedGroups,
            errors = errors
        )
    }
}
