package com.edgeledger.runtime

import android.content.Context
import android.net.Uri
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.edgeledger.runtime.data.AppDatabase
import com.edgeledger.runtime.domain.DeduplicationEngine
import com.edgeledger.runtime.domain.SafScanner
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File
import java.io.FileOutputStream

@RunWith(RobolectricTestRunner::class)
@Config(manifest = Config.NONE)
class AndroidDeduplicationTest {

    private lateinit var context: Context
    private lateinit var database: AppDatabase
    private lateinit var testDir: File

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        database = Room.inMemoryDatabaseBuilder(context, AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        testDir = File(context.cacheDir, "edgeledger_test_${System.currentTimeMillis()}")
        testDir.mkdirs()
    }

    @After
    fun tearDown() {
        database.close()
        testDir.deleteRecursively()
    }

    private fun createFile(name: String, content: ByteArray): File {
        val file = File(testDir, name)
        file.parentFile?.mkdirs()
        FileOutputStream(file).use { it.write(content) }
        return file
    }

    @Test
    fun testExactDuplicatesAndSameSizeDifferentContents() = runTest {
        val contentA = "EXACT_DUPLICATE_CONTENT_DATA_PACK".toByteArray()
        val contentB = "DIFFERENT_SAME_SIZE_CONTENT_PACK".toByteArray()

        createFile("dup1.bin", contentA)
        createFile("dup2.bin", contentA)
        createFile("diff.bin", contentB)

        val uriTree = Uri.fromFile(testDir)
        val engine = DeduplicationEngine(context, database)

        val report = engine.runPipeline(uriTree) {}

        assertEquals(3, report.totalFilesDiscovered)
        assertEquals(1, report.duplicateGroupsConfirmed)
        assertEquals(2, report.duplicateFilesCount)
        assertEquals(contentA.size.toLong(), report.duplicateGroups[0].fileSizeByes)
        assertEquals(contentA.size.toLong(), report.duplicateGroups[0].potentialRecoverableBytes)
    }

    @Test
    fun testPartialHashWindowCalculationAndByteAccounting() = runTest {
        val scanner = SafScanner(context)
        val content = ByteArray(5000) { 'X'.code.toByte() }
        val file = createFile("partial.bin", content)
        val uri = Uri.fromFile(file)

        val (bytes, bytesRead) = scanner.readPartialBytes(uri, content.size.toLong(), 1024)

        assertNotNull(bytes)
        assertEquals(2048, bytes?.size)
        assertEquals(2048L, bytesRead)
    }

    @Test
    fun testNullStreamFullHashReadErrorHandling() = runTest {
        val scanner = SafScanner(context)
        val invalidUri = Uri.fromFile(File(testDir, "non_existent.bin"))

        val (hash, bytesRead) = scanner.computeFullHash(invalidUri)

        assertNull(hash)
        assertEquals(0L, bytesRead)
    }

    @Test
    fun testIndexLedgerCacheReuseAcrossScans() = runTest {
        val content = "REUSABLE_CACHE_TEST_CONTENT".toByteArray()
        createFile("cache1.bin", content)
        createFile("cache2.bin", content)

        val uriTree = Uri.fromFile(testDir)
        val engine = DeduplicationEngine(context, database)

        val report1 = engine.runPipeline(uriTree) {}
        assertTrue(report1.duplicateGroupsConfirmed == 1)

        val report2 = engine.runPipeline(uriTree) {}
        assertEquals(0L, report2.stage2Metrics.bytesRead)
        assertEquals(0L, report2.stage3Metrics.bytesRead)
        assertEquals(report1.duplicateGroupsConfirmed, report2.duplicateGroupsConfirmed)
    }

    @Test
    fun testCooperativeScanCancellation() = runTest {
        val content = ByteArray(1024 * 1024) { 'Z'.code.toByte() }
        createFile("big1.bin", content)
        createFile("big2.bin", content)

        val uriTree = Uri.fromFile(testDir)
        val engine = DeduplicationEngine(context, database)

        val job = async {
            engine.runPipeline(uriTree) {}
        }

        job.cancel()

        try {
            job.await()
            fail("Expected CancellationException")
        } catch (e: Exception) {
            assertTrue(e is CancellationException)
        }
    }
}
