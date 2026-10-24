package com.edgeledger.runtime.ui

import android.app.Application
import android.content.Intent
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.edgeledger.runtime.data.AppDatabase
import com.edgeledger.runtime.domain.DeduplicationEngine
import com.edgeledger.runtime.domain.ScanState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val _scanState = MutableStateFlow<ScanState>(ScanState.Idle)
    val scanState: StateFlow<ScanState> = _scanState

    private val _selectedUri = MutableStateFlow<Uri?>(null)
    val selectedUri: StateFlow<Uri?> = _selectedUri

    private val database = AppDatabase.getInstance(application)
    private val engine = DeduplicationEngine(application, database)

    private var scanJob: Job? = null

    fun onTreeUriSelected(uri: Uri) {
        val context = getApplication<Application>()
        try {
            val takeFlags = Intent.FLAG_GRANT_READ_URI_PERMISSION
            context.contentResolver.takePersistableUriPermission(uri, takeFlags)
        } catch (e: Exception) {
            // Permission grant error fallback
        }
        _selectedUri.value = uri
        startScan(uri)
    }

    fun startScan(uri: Uri) {
        scanJob?.cancel()
        scanJob = viewModelScope.launch(Dispatchers.IO) {
            try {
                _scanState.value = ScanState.Scanning("Starting...", 0, "Initializing", 0L)
                val report = engine.runPipeline(uri) { progress ->
                    _scanState.value = progress
                }
                _scanState.value = ScanState.Completed(report)
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) {
                    _scanState.value = ScanState.Cancelled
                } else {
                    _scanState.value = ScanState.Error(e.message ?: "Unknown error occurred")
                }
            }
        }
    }

    fun cancelScan() {
        scanJob?.cancel()
        _scanState.value = ScanState.Cancelled
    }

    fun resetScan() {
        scanJob?.cancel()
        _scanState.value = ScanState.Idle
    }
}
