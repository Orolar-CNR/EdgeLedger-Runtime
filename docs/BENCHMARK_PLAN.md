# Benchmark Plan Specification

## 1. Overview & Evaluation Goals

The objective of the **Benchmark Plan** is to provide an empirical methodology for evaluating the performance, I/O efficiency, resource usage, and correctness of EdgeLedger Runtime across both Python reference prototypes and physical Android target devices.

### Primary Metrics
1. **I/O Efficiency:** Actual bytes read vs. total dataset size.
2. **Stage Progression & Filter Ratios:** Number of candidate files passing Stage 1 (Size), Stage 2 (Partial Hash), and Stage 3 (Full Hash).
3. **Execution Latency:** Time spent per stage and total processing time (ms).
4. **Memory Footprint:** Peak RSS / Heap usage during batch operations.
5. **Database Overhead:** SQLite Write-Ahead Logging (WAL) write volume, transaction duration, and query latency.
6. **Hardware Response (Android Devices):** CPU utilization, thermal throttling indicators, and battery impact over extended indexing runs.

---

## 2. Test Datasets & Synthetic Generation

Benchmarks must be conducted using controlled, reproducible datasets containing specified edge cases:

| Dataset Category | Composition Description | Target Validation Goal |
|---|---|---|
| **Synthetic Baseline** | 10,000 files: 8,000 unique sizes; 1,500 same size with different contents; 500 exact duplicates. | Verify Stage 1 filter efficiency and byte reduction. |
| **Partial Match Stress** | 1,000 files with identical size and identical 8 KiB head/tail windows, but differing middle contents. | Verify Stage 2 filtering prevents false positive duplicate groups. |
| **Small File Set** | 2,000 files smaller than the 16 KiB combined sample window (e.g. 1 KB - 8 KB). | Verify overlapping byte range avoidance logic. |
| **Real-World Media Proxy** | 5,000 mixed media files (JPEGs, MP4s, PDFs, audio) ranging from 50 KB to 500 MB. | Measure actual throughput and memory consumption. |

---

## 3. Benchmark Execution Methodology

### 3.1 Python Prototype Benchmark Protocol
Run `prototypes/python/cascading_dedup.py` with `--json-report` output against generated test directories:
```bash
python3 prototypes/python/cascading_dedup.py /path/to/dataset --json-report benchmark_results.json
```

### 3.2 Android On-Device Benchmark Protocol (Phase 5 Target Profile)
* **Target Hardware Profile:** Android 16, Snapdragon 8s Gen 4, 8 GB Physical RAM, 256 GB UFS storage.
* **Test Isolation:**
  - Device battery charged above 80% and connected to constant ambient temperature environment.
  - Background apps closed; thermal state logged prior to run (`dumpsys thermalservice`).
* **WorkManager Constraints:** Batch size limited to 100 files per transaction; memory chunk buffer capped at 64 KiB per stream.

---

## 4. Benchmark Result Schema

JSON benchmark reports must strictly adhere to the following schema:

```json
{
  "benchmark_metadata": {
    "timestamp_epoch_ms": 1700000000000,
    "environment": "Python 3.10 / Linux | Android 16 - Snapdragon 8s Gen 4",
    "hash_algorithm": "BLAKE2b-128",
    "sample_window_bytes": 8192
  },
  "dataset_summary": {
    "total_files_discovered": 10000,
    "total_bytes_discovered": 52428800000,
    "unreadable_files_count": 0
  },
  "stage_metrics": {
    "stage_1_size_filter": {
      "files_entered": 10000,
      "files_excluded_unique_size": 8000,
      "candidate_files_remaining": 2000,
      "bytes_read": 0,
      "elapsed_ms": 45
    },
    "stage_2_partial_hash": {
      "files_entered": 2000,
      "files_filtered_out": 1500,
      "candidate_files_remaining": 500,
      "bytes_read": 32768000,
      "elapsed_ms": 230
    },
    "stage_3_full_hash": {
      "files_entered": 500,
      "duplicate_groups_confirmed": 200,
      "duplicate_files_count": 500,
      "bytes_read": 2621440000,
      "elapsed_ms": 1850
    }
  },
  "performance_summary": {
    "total_bytes_read": 2654208000,
    "overall_byte_reduction_percentage": 94.94,
    "total_elapsed_ms": 2125,
    "peak_memory_rss_bytes": 45123840
  }
}
```
