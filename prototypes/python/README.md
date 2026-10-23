# EdgeLedger Runtime — Python Cascading Deduplication PoC (Phase 1)

This directory contains the Python proof-of-concept (PoC) for the 3-stage cascading duplicate candidate detection algorithm defined in the EdgeLedger Runtime architecture.

## 1. Overview

The PoC demonstrates how reading storage incrementally in stages significantly reduces I/O bandwidth and file content processing compared to naive full-file hashing.

### Pipeline Stages
1. **Stage 1 — Size Correlation Filter:**
   - Discovers all candidate files and groups them by exact byte size.
   - Files with unique sizes within the scanned scope are immediately excluded (0 bytes read).
2. **Stage 2 — Head-Tail Partial Hash:**
   - Samples 8 KiB from the beginning and 8 KiB from the end of remaining same-size candidates.
   - For files $\le$ 16 KiB (combined window), reads the file content once without reading overlapping bytes twice.
   - Computes a partial signature hash to filter out same-size files with differing contents.
3. **Stage 3 — Full Hash Streaming:**
   - Streams the full content of remaining candidate files in fixed 64 KiB buffer chunks to calculate full-file signatures.
   - Groups confirmed duplicates and measures exact actual bytes read.

---

## 2. Dependencies & Requirements

* **Python Version:** Python 3.8 or higher.
* **External Dependencies:** **None** (Uses Python Standard Library only: `hashlib`, `dataclasses`, `argparse`, `json`, `unittest`).

---

## 3. How to Run

### Execute Deduplication on a Directory
To run deduplication on a directory and print the human-readable CLI report:
```bash
python3 prototypes/python/cascading_dedup.py /path/to/target_directory
```

### Export a JSON Report
To generate a machine-readable JSON report:
```bash
python3 prototypes/python/cascading_dedup.py /path/to/target_directory --json-report report.json
```

### Custom Hashing Parameters
To specify custom sample window sizes or algorithms:
```bash
python3 prototypes/python/cascading_dedup.py /path/to/target_directory --sample-window 4096 --algorithm sha256
```

---

## 4. Automated Tests

The automated test suite covers all 13 required correctness, edge-case, and snapshot consistency scenarios.

To execute the unit tests:
```bash
python3 -m unittest discover -s prototypes/python/tests -p "test_*.py"
```

---

## 5. Metric Interpretation

The report provides exact empirical metrics:
* **Total Discovered:** Count and combined byte size of all scanned files.
* **Stage Progression:** Input count, candidate count, bytes read, and stage latency (ms) for Stage 1, Stage 2, and Stage 3.
* **Total Bytes Read:** Cumulative byte count actually read across all stages.
* **I/O Reduction:** Percentage of disk I/O saved compared to performing full reads on all discovered files.
* **Duplicate Groups:** List of confirmed duplicate file path clusters.
