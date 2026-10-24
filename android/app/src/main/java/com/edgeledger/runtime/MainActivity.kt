package com.edgeledger.runtime

import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.edgeledger.runtime.domain.ScanState
import com.edgeledger.runtime.ui.MainViewModel

class MainActivity : ComponentActivity() {

    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            MaterialTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    MainScreen(viewModel)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(viewModel: MainViewModel) {
    val scanState by viewModel.scanState.collectAsState()
    val selectedUri by viewModel.selectedUri.collectAsState()

    val dirPickerLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenDocumentTree()
    ) { uri: Uri? ->
        uri?.let { viewModel.onTreeUriSelected(it) }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("EdgeLedger Runtime") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer
                )
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Button(
                onClick = { dirPickerLauncher.launch(null) },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(if (selectedUri == null) "Select Folder to Scan (SAF)" else "Change Folder")
            }

            selectedUri?.let { uri ->
                Text(
                    text = "Selected Path: ${uri.path}",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(vertical = 8.dp)
                )
            }

            Spacer(modifier = Modifier.height(16.dp))

            when (val state = scanState) {
                is ScanState.Idle -> {
                    Text("Select a folder to begin cascading duplicate detection (Read-Only).")
                }
                is ScanState.Scanning -> {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text("Scanning in progress...", style = MaterialTheme.typography.titleMedium)
                            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
                            Text("Stage: ${state.currentStage}")
                            Text("Files Discovered: ${state.filesDiscovered}")
                            Text("Current File: ${state.currentFile}")
                            Text("Bytes Read: ${state.bytesReadSoFar} bytes")
                            Spacer(modifier = Modifier.height(8.dp))
                            Button(
                                onClick = { viewModel.cancelScan() },
                                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error)
                            ) {
                                Text("Cancel Scan")
                            }
                        }
                    }
                }
                is ScanState.Cancelled -> {
                    Text("Scan was cancelled by user.")
                    Spacer(modifier = Modifier.height(8.dp))
                    Button(onClick = { viewModel.resetScan() }) {
                        Text("Reset")
                    }
                }
                is ScanState.Error -> {
                    Text("Error: ${state.message}", color = MaterialTheme.colorScheme.error)
                    Spacer(modifier = Modifier.height(8.dp))
                    Button(onClick = { viewModel.resetScan() }) {
                        Text("Reset")
                    }
                }
                is ScanState.Completed -> {
                    val report = state.report
                    Card(modifier = Modifier.fillMaxWidth().weight(1f)) {
                        LazyColumn(modifier = Modifier.padding(16.dp)) {
                            item {
                                Text("Scan Complete Report", style = MaterialTheme.typography.titleLarge)
                                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                                Text("Algorithm: ${report.hashAlgorithm}")
                                Text("Files Discovered: ${report.totalFilesDiscovered} (${report.totalBytesDiscovered} bytes)")
                                Text("Stage 1 Candidates: ${report.stage1Metrics.candidateFilesRemaining}")
                                Text("Stage 2 Candidates: ${report.stage2Metrics.candidateFilesRemaining} (${report.stage2Metrics.bytesRead} bytes read)")
                                Text("Stage 3 Duplicates: ${report.stage3Metrics.candidateFilesRemaining} (${report.stage3Metrics.bytesRead} bytes read)")
                                Text("Total Bytes Read: ${report.totalBytesRead} bytes")
                                Text("I/O Savings: ${"%.2f".format(report.overallByteReductionPercentage)}%")
                                Text("Total Elapsed: ${"%.2f".format(report.totalElapsedMs)} ms")
                                Text("Potential Recoverable Space: ${report.aggregatePotentialRecoverableBytes} bytes")
                                Text("Peak Heap Used: ${report.memoryMetrics.peakHeapUsedBytes / (1024 * 1024)} MB")
                                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                                Text("Duplicate Groups (${report.duplicateGroupsConfirmed}):", style = MaterialTheme.typography.titleMedium)
                            }

                            items(report.duplicateGroups) { group ->
                                Card(
                                    modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
                                ) {
                                    Column(modifier = Modifier.padding(8.dp)) {
                                        Text("${group.groupId}: ${group.fileCount} files (${group.fileSizeByes} B each)")
                                        Text("Total Size: ${group.totalGroupSizeBytes} B | Retained: ${group.retainedCopySizeBytes} B")
                                        Text("Potential Recoverable: ${group.potentialRecoverableBytes} B", style = MaterialTheme.typography.bodySmall)
                                        Text("Files:", style = MaterialTheme.typography.labelSmall)
                                        group.fileNames.forEach { name ->
                                            Text(" - $name", style = MaterialTheme.typography.bodySmall)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
