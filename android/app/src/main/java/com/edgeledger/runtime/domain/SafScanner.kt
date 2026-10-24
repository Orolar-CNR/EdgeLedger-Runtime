package com.edgeledger.runtime.domain

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import java.io.File
import java.io.InputStream
import java.security.MessageDigest
import kotlin.coroutines.cancellation.CancellationException

class SafScanner(private val context: Context) {

    fun scanDirectory(treeUri: Uri): Flow<DocumentMetadata> = flow {
        if (treeUri.scheme == "file") {
            val rootFile = File(treeUri.path ?: return@flow)
            val stack = ArrayDeque<File>()
            stack.addLast(rootFile)

            while (stack.isNotEmpty()) {
                currentCoroutineContext().ensureActive()
                val dir = stack.removeLast()
                val files = dir.listFiles() ?: continue
                for (file in files) {
                    currentCoroutineContext().ensureActive()
                    if (file.isDirectory) {
                        stack.addLast(file)
                    } else if (file.isFile) {
                        emit(
                            DocumentMetadata(
                                uri = Uri.fromFile(file),
                                displayName = file.name,
                                sizeBytes = file.length(),
                                lastModifiedEpochMs = file.lastModified(),
                                mimeType = null,
                                isDirectory = false
                            )
                        )
                    }
                }
            }
        } else {
            val root = DocumentFile.fromTreeUri(context, treeUri) ?: return@flow
            val stack = ArrayDeque<DocumentFile>()
            stack.addLast(root)

            while (stack.isNotEmpty()) {
                currentCoroutineContext().ensureActive()
                val dir = stack.removeLast()
                val files = dir.listFiles()
                for (file in files) {
                    currentCoroutineContext().ensureActive()
                    if (file.isDirectory) {
                        stack.addLast(file)
                    } else if (file.isFile) {
                        emit(
                            DocumentMetadata(
                                uri = file.uri,
                                displayName = file.name ?: "Unknown",
                                sizeBytes = file.length(),
                                lastModifiedEpochMs = file.lastModified(),
                                mimeType = file.type,
                                isDirectory = false
                            )
                        )
                    }
                }
            }
        }
    }

    suspend fun readPartialBytes(uri: Uri, sizeBytes: Long, sampleWindowBytes: Int): Pair<ByteArray?, Long> {
        if (sizeBytes == 0L) return Pair(ByteArray(0), 0L)
        return try {
            currentCoroutineContext().ensureActive()
            val inputStream = context.contentResolver.openInputStream(uri) ?: return Pair(null, 0L)
            inputStream.use { stream ->
                if (sizeBytes <= 2 * sampleWindowBytes) {
                    val buffer = ByteArray(sizeBytes.toInt())
                    var totalRead = 0
                    while (totalRead < sizeBytes) {
                        currentCoroutineContext().ensureActive()
                        val read = stream.read(buffer, totalRead, (sizeBytes - totalRead).toInt())
                        if (read == -1) break
                        totalRead += read
                    }
                    if (totalRead != sizeBytes.toInt()) {
                        Pair(null, totalRead.toLong())
                    } else {
                        Pair(buffer, totalRead.toLong())
                    }
                } else {
                    val head = ByteArray(sampleWindowBytes)
                    var headRead = 0
                    while (headRead < sampleWindowBytes) {
                        currentCoroutineContext().ensureActive()
                        val r = stream.read(head, headRead, sampleWindowBytes - headRead)
                        if (r == -1) break
                        headRead += r
                    }
                    if (headRead != sampleWindowBytes) {
                        return Pair(null, headRead.toLong())
                    }

                    val skipBytes = sizeBytes - 2 * sampleWindowBytes
                    var skipped = 0L
                    while (skipped < skipBytes) {
                        currentCoroutineContext().ensureActive()
                        val s = stream.skip(skipBytes - skipped)
                        if (s <= 0) break
                        skipped += s
                    }

                    if (skipped != skipBytes) {
                        return Pair(null, (headRead + skipped).toLong())
                    }

                    val tail = ByteArray(sampleWindowBytes)
                    var tailRead = 0
                    while (tailRead < sampleWindowBytes) {
                        currentCoroutineContext().ensureActive()
                        val r = stream.read(tail, tailRead, sampleWindowBytes - tailRead)
                        if (r == -1) break
                        tailRead += r
                    }
                    if (tailRead != sampleWindowBytes) {
                        return Pair(null, (headRead + tailRead).toLong())
                    }

                    val combined = head + tail
                    Pair(combined, (headRead + tailRead).toLong())
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Pair(null, 0L)
        }
    }

    suspend fun computeFullHash(uri: Uri, bufferSize: Int = 64 * 1024): Pair<String?, Long> {
        return try {
            currentCoroutineContext().ensureActive()
            val inputStream = context.contentResolver.openInputStream(uri) ?: return Pair(null, 0L)
            val digest = MessageDigest.getInstance("SHA-256")
            var bytesRead = 0L
            inputStream.use { stream ->
                val buffer = ByteArray(bufferSize)
                while (true) {
                    currentCoroutineContext().ensureActive()
                    val read = stream.read(buffer)
                    if (read == -1) break
                    digest.update(buffer, 0, read)
                    bytesRead += read
                }
            }
            val hex = digest.digest().joinToString("") { "%02x".format(it) }
            Pair(hex, bytesRead)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Pair(null, 0L)
        }
    }
}
