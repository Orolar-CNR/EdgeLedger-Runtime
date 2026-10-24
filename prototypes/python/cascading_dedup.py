#!/usr/bin/env python3
"""
EdgeLedger Runtime — Cascading Deduplication PoC (Phase 1)

This module implements a 3-stage cascading duplicate candidate detection algorithm:
  - Stage 1: File Size Correlation (exclude files with unique sizes in local SQLite index)
  - Stage 2: Head-Tail Partial Hash (sample fixed windows from beginning and end of candidate files)
  - Stage 3: Full Hash Streaming (hash entire content using SHA-256 by default)

It uses a local SQLite database (IndexLedger) for metadata persistence, tracks actual bytes read,
measures peak Python memory usage via tracemalloc, discovers host storage capacity via shutil.disk_usage(),
and calculates recoverable space metrics for CLI and JSON reports.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Set, Tuple

try:
    import resource
except ImportError:
    resource = None

# Support running directly as script or as package
try:
    from prototypes.python.index_ledger import IndexLedger, IndexedFile
except ModuleNotFoundError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
    from prototypes.python.index_ledger import IndexLedger, IndexedFile


DEFAULT_SAMPLE_WINDOW_BYTES = 8192  # 8 KiB head, 8 KiB tail
CHUNK_SIZE_BYTES = 64 * 1024        # 64 KiB read buffer for full hashing


class HashProvider:
    """Interface for hashing algorithms using Python standard library hashlib."""

    def __init__(self, algorithm_name: str = "sha256"):
        self.algorithm_name = algorithm_name.lower()
        if self.algorithm_name not in hashlib.algorithms_available:
            self.algorithm_name = "sha256"

    def get_hash_name(self) -> str:
        return self.algorithm_name.upper()

    def compute_bytes(self, data: bytes) -> str:
        h = hashlib.new(self.algorithm_name)
        h.update(data)
        return h.hexdigest()

    def create_hasher(self):
        return hashlib.new(self.algorithm_name)


@dataclass
class FileSnapshot:
    path: str
    size_bytes: int
    mtime_epoch_ms: int

    @staticmethod
    def capture(path: str) -> Optional['FileSnapshot']:
        try:
            stat = os.stat(path)
            mtime_ms = int(stat.st_mtime * 1000)
            return FileSnapshot(
                path=os.path.abspath(path),
                size_bytes=stat.st_size,
                mtime_epoch_ms=mtime_ms
            )
        except (OSError, PermissionError):
            return None

    def verify_current(self) -> bool:
        current = FileSnapshot.capture(self.path)
        if current is None:
            return False
        return (current.size_bytes == self.size_bytes and
                current.mtime_epoch_ms == self.mtime_epoch_ms)


@dataclass
class StageMetrics:
    files_entered: int = 0
    candidate_files_remaining: int = 0
    bytes_read: int = 0
    elapsed_ms: float = 0.0


@dataclass
class StorageCapacity:
    target_path: str = ""
    total_capacity_bytes: Optional[int] = None
    used_bytes: Optional[int] = None
    available_bytes: Optional[int] = None
    status: str = "AVAILABLE"  # 'AVAILABLE' or 'UNAVAILABLE'


@dataclass
class DuplicateGroupDetails:
    group_id: str
    file_count: int
    file_paths: List[str]
    file_size_bytes: int
    total_group_size_bytes: int
    retained_copy_size_bytes: int
    potential_recoverable_bytes: int


@dataclass
class MemoryUsageReport:
    peak_python_tracked_bytes: int = 0
    measurement_method: str = "tracemalloc"
    process_max_rss_bytes: Optional[int] = None
    notes: str = "tracemalloc measures Python allocation peak. process_max_rss_bytes measures OS peak RSS if available."


@dataclass
class DeduplicationReport:
    timestamp_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    configuration: Dict[str, str] = field(default_factory=dict)
    storage_capacity: StorageCapacity = field(default_factory=StorageCapacity)
    total_files_discovered: int = 0
    total_bytes_discovered: int = 0
    unreadable_files: List[Dict[str, str]] = field(default_factory=list)
    skipped_modified_files: List[Dict[str, str]] = field(default_factory=list)
    stage_1_size_filter: StageMetrics = field(default_factory=StageMetrics)
    stage_2_partial_hash: StageMetrics = field(default_factory=StageMetrics)
    stage_3_full_hash: StageMetrics = field(default_factory=StageMetrics)
    duplicate_groups_confirmed: int = 0
    duplicate_files_count: int = 0
    total_bytes_read: int = 0
    overall_byte_reduction_percentage: float = 0.0
    aggregate_potential_recoverable_bytes: int = 0
    total_elapsed_ms: float = 0.0
    memory_usage: MemoryUsageReport = field(default_factory=MemoryUsageReport)
    duplicate_groups: List[DuplicateGroupDetails] = field(default_factory=list)

    def to_dict(self) -> Dict:
        data = asdict(self)
        return data


class CascadingDeduplicationPipeline:
    def __init__(
        self,
        sample_window_bytes: int = DEFAULT_SAMPLE_WINDOW_BYTES,
        hash_algorithm: str = "sha256",
        ledger: Optional[IndexLedger] = None
    ):
        self.sample_window_bytes = sample_window_bytes
        self.hasher = HashProvider(hash_algorithm)
        self.ledger = ledger if ledger is not None else IndexLedger(":memory:")
        self._owns_ledger = ledger is None

    def close(self):
        if self._owns_ledger and self.ledger:
            self.ledger.close()

    def discover_storage_capacity(self, target_dir: str) -> StorageCapacity:
        cap = StorageCapacity(target_path=os.path.abspath(target_dir))
        try:
            usage = shutil.disk_usage(target_dir)
            cap.total_capacity_bytes = usage.total
            cap.used_bytes = usage.used
            cap.available_bytes = usage.free
            cap.status = "AVAILABLE"
        except (OSError, PermissionError):
            cap.status = "UNAVAILABLE"
        return cap

    def scan_and_index_directory(self, target_dir: str) -> Tuple[List[IndexedFile], List[Dict[str, str]]]:
        indexed_files = []
        unreadable = []
        for root, _, files in os.walk(target_dir):
            for file_name in files:
                full_path = os.path.join(root, file_name)
                snap = FileSnapshot.capture(full_path)
                if snap is None:
                    self.ledger.record_unreadable_file(full_path, "Permission denied or unreadable file stat")
                    unreadable.append({
                        "path": os.path.abspath(full_path),
                        "reason": "Permission denied or unreadable file stat"
                    })
                else:
                    idx_file = self.ledger.upsert_file_metadata(
                        path=snap.path,
                        size_bytes=snap.size_bytes,
                        last_modified=snap.mtime_epoch_ms,
                        scan_status="INDEXED"
                    )
                    indexed_files.append(idx_file)
        return indexed_files, unreadable

    def _read_partial_bytes(self, snap: FileSnapshot) -> Tuple[Optional[bytes], int, Optional[str]]:
        try:
            if not snap.verify_current():
                return None, 0, "File modified or changed before reading"

            size = snap.size_bytes
            window = self.sample_window_bytes

            if size == 0:
                return b"", 0, None

            with open(snap.path, "rb") as f:
                if size <= 2 * window:
                    data = f.read(size)
                    if len(data) != size:
                        return None, len(data), "Incomplete read - file size changed during read"
                    return data, len(data), None
                else:
                    head = f.read(window)
                    if len(head) != window:
                        return None, len(head), "Incomplete head read"
                    f.seek(size - window, os.SEEK_SET)
                    tail = f.read(window)
                    if len(tail) != window:
                        return None, len(head) + len(tail), "Incomplete tail read"

                    return head + tail, 2 * window, None
        except (OSError, PermissionError) as e:
            return None, 0, f"I/O Error: {str(e)}"

    def _stream_full_hash(self, snap: FileSnapshot) -> Tuple[Optional[str], int, Optional[str]]:
        try:
            if not snap.verify_current():
                return None, 0, "File modified or changed before reading"

            hasher = self.hasher.create_hasher()
            bytes_read = 0

            with open(snap.path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE_BYTES)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    bytes_read += len(chunk)

            if not snap.verify_current():
                return None, bytes_read, "File modified during full hash streaming"

            if bytes_read != snap.size_bytes:
                return None, bytes_read, f"Expected {snap.size_bytes} bytes, but read {bytes_read} bytes"

            return hasher.hexdigest(), bytes_read, None

        except (OSError, PermissionError) as e:
            return None, 0, f"I/O Error: {str(e)}"

    def process_indexed_files(self, target_dir: str, unreadable_files: List[Dict[str, str]] = None) -> DeduplicationReport:
        tracemalloc.start()
        start_time_total = time.time()

        report = DeduplicationReport()
        report.configuration = {
            "hash_algorithm": self.hasher.get_hash_name(),
            "sample_window_bytes": str(self.sample_window_bytes)
        }
        report.storage_capacity = self.discover_storage_capacity(target_dir)

        if unreadable_files:
            report.unreadable_files.extend(unreadable_files)

        all_indexed = self.ledger.get_all_active_files()
        report.total_files_discovered = len(all_indexed) + len(report.unreadable_files)
        report.total_bytes_discovered = sum(f.size_bytes for f in all_indexed)

        # ------------------------------------------------------------------
        # Stage 1: File Size Correlation Filter (from SQLite Index Ledger)
        # ------------------------------------------------------------------
        t1_start = time.time()
        report.stage_1_size_filter.files_entered = len(all_indexed)

        candidate_sizes = self.ledger.get_stage1_candidate_sizes()
        stage1_candidates: List[IndexedFile] = []
        for sz in candidate_sizes:
            stage1_candidates.extend(self.ledger.get_files_by_size(sz))

        report.stage_1_size_filter.candidate_files_remaining = len(stage1_candidates)
        report.stage_1_size_filter.bytes_read = 0
        report.stage_1_size_filter.elapsed_ms = (time.time() - t1_start) * 1000

        if not stage1_candidates:
            report.total_elapsed_ms = (time.time() - start_time_total) * 1000
            self._finalize_memory_report(report)
            return report

        # ------------------------------------------------------------------
        # Stage 2: Head-Tail Partial Hash
        # ------------------------------------------------------------------
        t2_start = time.time()
        report.stage_2_partial_hash.files_entered = len(stage1_candidates)

        partial_hash_groups: Dict[Tuple[int, str], List[IndexedFile]] = {}
        stage2_bytes_read = 0

        for f_idx in stage1_candidates:
            snap = FileSnapshot(path=f_idx.path, size_bytes=f_idx.size_bytes, mtime_epoch_ms=f_idx.last_modified)
            
            cached_sig = self.ledger.get_valid_signature(
                file_id=f_idx.file_id,
                stage=2,
                algorithm=self.hasher.get_hash_name(),
                current_mtime_epoch_ms=f_idx.last_modified,
                current_size_bytes=f_idx.size_bytes
            )

            if cached_sig:
                part_hash = cached_sig["hash_value"]
            else:
                sample_data, bytes_read, err = self._read_partial_bytes(snap)
                stage2_bytes_read += bytes_read

                if err is not None:
                    report.skipped_modified_files.append({
                        "path": f_idx.path,
                        "reason": f"Stage 2 failed: {err}"
                    })
                    continue

                part_hash = self.hasher.compute_bytes(sample_data)
                self.ledger.save_signature(
                    file_id=f_idx.file_id,
                    stage=2,
                    algorithm=self.hasher.get_hash_name(),
                    hash_value=part_hash,
                    sample_window_bytes=self.sample_window_bytes,
                    bytes_read=bytes_read,
                    computed_at_mtime_epoch_ms=f_idx.last_modified,
                    computed_at_size_bytes=f_idx.size_bytes
                )

            group_key = (f_idx.size_bytes, part_hash)
            partial_hash_groups.setdefault(group_key, []).append(f_idx)

        stage2_candidates: List[IndexedFile] = []
        for key, group in partial_hash_groups.items():
            if len(group) > 1:
                stage2_candidates.extend(group)

        report.stage_2_partial_hash.candidate_files_remaining = len(stage2_candidates)
        report.stage_2_partial_hash.bytes_read = stage2_bytes_read
        report.stage_2_partial_hash.elapsed_ms = (time.time() - t2_start) * 1000

        if not stage2_candidates:
            report.total_bytes_read = stage2_bytes_read
            if report.total_bytes_discovered > 0:
                report.overall_byte_reduction_percentage = (
                    (1.0 - (report.total_bytes_read / report.total_bytes_discovered)) * 100.0
                )
            report.total_elapsed_ms = (time.time() - start_time_total) * 1000
            self._finalize_memory_report(report)
            return report

        # ------------------------------------------------------------------
        # Stage 3: Full Hash
        # ------------------------------------------------------------------
        t3_start = time.time()
        report.stage_3_full_hash.files_entered = len(stage2_candidates)

        full_hash_groups: Dict[Tuple[int, str], List[IndexedFile]] = {}
        stage3_bytes_read = 0

        for f_idx in stage2_candidates:
            snap = FileSnapshot(path=f_idx.path, size_bytes=f_idx.size_bytes, mtime_epoch_ms=f_idx.last_modified)

            cached_sig = self.ledger.get_valid_signature(
                file_id=f_idx.file_id,
                stage=3,
                algorithm=self.hasher.get_hash_name(),
                current_mtime_epoch_ms=f_idx.last_modified,
                current_size_bytes=f_idx.size_bytes
            )

            if cached_sig:
                full_hash = cached_sig["hash_value"]
            else:
                full_hash, bytes_read, err = self._stream_full_hash(snap)
                stage3_bytes_read += bytes_read

                if err is not None:
                    report.skipped_modified_files.append({
                        "path": f_idx.path,
                        "reason": f"Stage 3 failed: {err}"
                    })
                    continue

                self.ledger.save_signature(
                    file_id=f_idx.file_id,
                    stage=3,
                    algorithm=self.hasher.get_hash_name(),
                    hash_value=full_hash,
                    sample_window_bytes=self.sample_window_bytes,
                    bytes_read=bytes_read,
                    computed_at_mtime_epoch_ms=f_idx.last_modified,
                    computed_at_size_bytes=f_idx.size_bytes
                )

            group_key = (f_idx.size_bytes, full_hash)
            full_hash_groups.setdefault(group_key, []).append(f_idx)

        dup_file_count = 0
        group_counter = 1
        total_aggregate_recoverable = 0

        for key, group in full_hash_groups.items():
            if len(group) > 1:
                paths = [f.path for f in group]
                file_size = key[0]
                total_group_size = file_size * len(group)
                retained_copy_size = file_size
                recoverable = total_group_size - retained_copy_size
                total_aggregate_recoverable += recoverable

                group_detail = DuplicateGroupDetails(
                    group_id=f"group_{group_counter}",
                    file_count=len(group),
                    file_paths=paths,
                    file_size_bytes=file_size,
                    total_group_size_bytes=total_group_size,
                    retained_copy_size_bytes=retained_copy_size,
                    potential_recoverable_bytes=recoverable
                )
                report.duplicate_groups.append(group_detail)
                dup_file_count += len(group)
                group_counter += 1

        report.stage_3_full_hash.candidate_files_remaining = dup_file_count
        report.stage_3_full_hash.bytes_read = stage3_bytes_read
        report.stage_3_full_hash.elapsed_ms = (time.time() - t3_start) * 1000

        # Summary calculations
        report.duplicate_groups_confirmed = len(report.duplicate_groups)
        report.duplicate_files_count = dup_file_count
        report.aggregate_potential_recoverable_bytes = total_aggregate_recoverable
        report.total_bytes_read = stage2_bytes_read + stage3_bytes_read

        if report.total_bytes_discovered > 0:
            report.overall_byte_reduction_percentage = max(
                0.0,
                (1.0 - (report.total_bytes_read / report.total_bytes_discovered)) * 100.0
            )

        report.total_elapsed_ms = (time.time() - start_time_total) * 1000
        self._finalize_memory_report(report)
        return report

    def _finalize_memory_report(self, report: DeduplicationReport):
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        mem_report = MemoryUsageReport(
            peak_python_tracked_bytes=peak_mem,
            measurement_method="tracemalloc (peak Python-tracked allocations)"
        )

        if resource is not None:
            try:
                rusage = resource.getrusage(resource.RUSAGE_SELF)
                if sys.platform.startswith("darwin"):
                    mem_report.process_max_rss_bytes = rusage.ru_maxrss
                else:
                    mem_report.process_max_rss_bytes = rusage.ru_maxrss * 1024
            except Exception:
                pass

        report.memory_usage = mem_report


def print_cli_report(report: DeduplicationReport) -> None:
    print("=" * 65)
    print("      EdgeLedger Runtime — Cascading Deduplication Report")
    print("=" * 65)
    print(f"Algorithm Used      : {report.configuration.get('hash_algorithm')}")
    print(f"Sample Window Size  : {report.configuration.get('sample_window_bytes')} bytes (Head/Tail)")
    
    cap = report.storage_capacity
    if cap.status == "AVAILABLE" and cap.total_capacity_bytes is not None:
        print(f"Storage Capacity    : Total {cap.total_capacity_bytes:,} B | Used {cap.used_bytes:,} B | Free {cap.available_bytes:,} B")
    else:
        print("Storage Capacity    : Unavailable / Unsupported for target path")

    print(f"Total Discovered    : {report.total_files_discovered} files ({report.total_bytes_discovered:,} bytes)")
    if report.unreadable_files:
        print(f"Unreadable Files    : {len(report.unreadable_files)} files")
    if report.skipped_modified_files:
        print(f"Skipped/Modified    : {len(report.skipped_modified_files)} files during read")
    print("-" * 65)
    print("Stage 1 — Size Correlation Filter:")
    print(f"  Files Entered     : {report.stage_1_size_filter.files_entered}")
    print(f"  Candidates Left   : {report.stage_1_size_filter.candidate_files_remaining}")
    print(f"  Bytes Read        : {report.stage_1_size_filter.bytes_read} bytes")
    print(f"  Stage Latency     : {report.stage_1_size_filter.elapsed_ms:.2f} ms")
    print("-" * 65)
    print("Stage 2 — Head-Tail Partial Hash:")
    print(f"  Files Entered     : {report.stage_2_partial_hash.files_entered}")
    print(f"  Candidates Left   : {report.stage_2_partial_hash.candidate_files_remaining}")
    print(f"  Bytes Read        : {report.stage_2_partial_hash.bytes_read:,} bytes")
    print(f"  Stage Latency     : {report.stage_2_partial_hash.elapsed_ms:.2f} ms")
    print("-" * 65)
    print("Stage 3 — Full Hash Streaming:")
    print(f"  Files Entered     : {report.stage_3_full_hash.files_entered}")
    print(f"  Duplicates Found  : {report.stage_3_full_hash.candidate_files_remaining} files")
    print(f"  Bytes Read        : {report.stage_3_full_hash.bytes_read:,} bytes")
    print(f"  Stage Latency     : {report.stage_3_full_hash.elapsed_ms:.2f} ms")
    print("=" * 65)
    print("Overall Performance Metrics:")
    print(f"  Total Bytes Read  : {report.total_bytes_read:,} bytes")
    print(f"  I/O Reduction     : {report.overall_byte_reduction_percentage:.2f}% bytes saved from reading")
    print(f"  Total Elapsed Time: {report.total_elapsed_ms:.2f} ms")
    print(f"  Peak Python Memory: {report.memory_usage.peak_python_tracked_bytes:,} bytes ({report.memory_usage.peak_python_tracked_bytes / (1024*1024):.2f} MB)")
    if report.memory_usage.process_max_rss_bytes:
        print(f"  Process Max RSS   : {report.memory_usage.process_max_rss_bytes:,} bytes ({report.memory_usage.process_max_rss_bytes / (1024*1024):.2f} MB)")
    print(f"  Duplicate Groups  : {report.duplicate_groups_confirmed} groups")
    print(f"  Recoverable Space : {report.aggregate_potential_recoverable_bytes:,} bytes (potential)")
    print("=" * 65)

    if report.duplicate_groups:
        print("\nConfirmed Duplicate Groups (Read-Only Candidate Report):")
        for group in report.duplicate_groups:
            print(f" Group {group.group_id} ({group.file_count} files, {group.file_size_bytes:,} B each):")
            print(f"   - Total Group Size   : {group.total_group_size_bytes:,} bytes")
            print(f"   - Retained Copy Size : {group.retained_copy_size_bytes:,} bytes")
            print(f"   - Potential Recoverable: {group.potential_recoverable_bytes:,} bytes")
            print("   - Files:")
            for path in group.file_paths:
                print(f"       * {path}")
        print()


def main():
    parser = argparse.ArgumentParser(description="EdgeLedger Runtime Cascading Deduplication PoC")
    parser.add_argument("target_dir", help="Directory path to scan for duplicate files")
    parser.add_argument("--json-report", help="Output path to write JSON report")
    parser.add_argument("--db-path", default=":memory:", help="Path to SQLite index database file (default: :memory:)")
    parser.add_argument("--sample-window", type=int, default=DEFAULT_SAMPLE_WINDOW_BYTES, help="Sample window size in bytes")
    parser.add_argument("--algorithm", default="sha256", help="Hash algorithm name (sha256, blake2b)")

    args = parser.parse_args()

    if not os.path.isdir(args.target_dir):
        print(f"Error: Target directory '{args.target_dir}' does not exist or is not a directory.", file=sys.stderr)
        sys.exit(1)

    ledger = IndexLedger(db_path=args.db_path)

    pipeline = CascadingDeduplicationPipeline(
        sample_window_bytes=args.sample_window,
        hash_algorithm=args.algorithm,
        ledger=ledger
    )

    try:
        indexed_files, unreadable = pipeline.scan_and_index_directory(args.target_dir)
        report = pipeline.process_indexed_files(args.target_dir, unreadable)

        print_cli_report(report)

        if args.json_report:
            try:
                with open(args.json_report, "w", encoding="utf-8") as f:
                    json.dump(report.to_dict(), f, indent=2)
                print(f"JSON report written to: {args.json_report}")
            except Exception as e:
                print(f"Error writing JSON report: {e}", file=sys.stderr)
    finally:
        pipeline.close()


if __name__ == "__main__":
    main()
