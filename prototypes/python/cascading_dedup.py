#!/usr/bin/env python3
"""
EdgeLedger Runtime — Cascading Deduplication PoC (Phase 1)

This module implements a 3-stage cascading duplicate candidate detection algorithm:
  - Stage 1: File Size Correlation (exclude files with unique sizes within the scanned scope)
  - Stage 2: Head-Tail Partial Hash (sample fixed windows from beginning and end of candidate files)
  - Stage 3: Full Hash Streaming (hash entire content for remaining candidates)

It provides metric tracking (actual bytes read, latency, stage progression, errors),
snapshot consistency checks (mtime/size validation), CLI output, and JSON report generation.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Set, Tuple


DEFAULT_SAMPLE_WINDOW_BYTES = 8192  # 8 KiB head, 8 KiB tail
CHUNK_SIZE_BYTES = 64 * 1024        # 64 KiB read buffer for full hashing


class HashProvider:
    """Interface for hashing algorithms using Python standard library hashlib."""

    def __init__(self, algorithm_name: str = "blake2b"):
        self.algorithm_name = algorithm_name.lower()
        if self.algorithm_name not in hashlib.algorithms_available:
            # Fallback to sha256 if requested algorithm is not available
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
            # mtime in milliseconds
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
class DeduplicationReport:
    timestamp_epoch_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    configuration: Dict[str, str] = field(default_factory=dict)
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
    total_elapsed_ms: float = 0.0
    duplicate_groups: List[List[str]] = field(default_factory=list)

    def to_dict(self) -> Dict:
        data = asdict(self)
        return data


class CascadingDeduplicationPipeline:
    def __init__(
        self,
        sample_window_bytes: int = DEFAULT_SAMPLE_WINDOW_BYTES,
        hash_algorithm: str = "blake2b"
    ):
        self.sample_window_bytes = sample_window_bytes
        self.hasher = HashProvider(hash_algorithm)

    def scan_directory(self, target_dir: str) -> Tuple[List[FileSnapshot], List[Dict[str, str]]]:
        snapshots = []
        unreadable = []
        for root, _, files in os.walk(target_dir):
            for file_name in files:
                full_path = os.path.join(root, file_name)
                snap = FileSnapshot.capture(full_path)
                if snap is None:
                    unreadable.append({
                        "path": os.path.abspath(full_path),
                        "reason": "Permission denied or unreadable file stat"
                    })
                else:
                    snapshots.append(snap)
        return snapshots, unreadable

    def _read_partial_bytes(self, snap: FileSnapshot) -> Tuple[Optional[bytes], int, Optional[str]]:
        """
        Reads head and tail sample windows without reading overlapping byte ranges twice.
        Returns (sample_bytes, bytes_read_count, error_reason).
        """
        try:
            if not snap.verify_current():
                return None, 0, "File modified or changed before reading"

            size = snap.size_bytes
            window = self.sample_window_bytes

            if size == 0:
                # Empty file
                return b"", 0, None

            with open(snap.path, "rb") as f:
                if size <= 2 * window:
                    # File smaller than or equal to combined sample window: read entire file once
                    data = f.read(size)
                    if len(data) != size:
                        return None, len(data), "Incomplete read - file size changed during read"
                    return data, len(data), None
                else:
                    # Read head window
                    head = f.read(window)
                    if len(head) != window:
                        return None, len(head), "Incomplete head read"
                    # Seek and read tail window
                    f.seek(size - window, os.SEEK_SET)
                    tail = f.read(window)
                    if len(tail) != window:
                        return None, len(head) + len(tail), "Incomplete tail read"

                    return head + tail, 2 * window, None
        except (OSError, PermissionError) as e:
            return None, 0, f"I/O Error: {str(e)}"

    def _stream_full_hash(self, snap: FileSnapshot) -> Tuple[Optional[str], int, Optional[str]]:
        """
        Streams file contents in chunk buffers to compute full hash.
        Returns (hash_hex, bytes_read_count, error_reason).
        """
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

            # Final snapshot verification after stream
            if not snap.verify_current():
                return None, bytes_read, "File modified during full hash streaming"

            if bytes_read != snap.size_bytes:
                return None, bytes_read, f"Expected {snap.size_bytes} bytes, but read {bytes_read} bytes"

            return hasher.hexdigest(), bytes_read, None

        except (OSError, PermissionError) as e:
            return None, 0, f"I/O Error: {str(e)}"

    def process_files(self, snapshots: List[FileSnapshot], unreadable_files: List[Dict[str, str]] = None) -> DeduplicationReport:
        start_time_total = time.time()
        report = DeduplicationReport()
        report.configuration = {
            "hash_algorithm": self.hasher.get_hash_name(),
            "sample_window_bytes": str(self.sample_window_bytes)
        }
        if unreadable_files:
            report.unreadable_files.extend(unreadable_files)

        report.total_files_discovered = len(snapshots) + len(report.unreadable_files)
        report.total_bytes_discovered = sum(s.size_bytes for s in snapshots)

        # ------------------------------------------------------------------
        # Stage 1: File Size Filter
        # ------------------------------------------------------------------
        t1_start = time.time()
        report.stage_1_size_filter.files_entered = len(snapshots)

        size_groups: Dict[int, List[FileSnapshot]] = {}
        for snap in snapshots:
            size_groups.setdefault(snap.size_bytes, []).append(snap)

        stage1_candidates: List[FileSnapshot] = []
        for size, group in size_groups.items():
            if len(group) > 1:
                stage1_candidates.extend(group)

        report.stage_1_size_filter.candidate_files_remaining = len(stage1_candidates)
        report.stage_1_size_filter.bytes_read = 0
        report.stage_1_size_filter.elapsed_ms = (time.time() - t1_start) * 1000

        if not stage1_candidates:
            report.total_elapsed_ms = (time.time() - start_time_total) * 1000
            return report

        # ------------------------------------------------------------------
        # Stage 2: Head-Tail Partial Hash
        # ------------------------------------------------------------------
        t2_start = time.time()
        report.stage_2_partial_hash.files_entered = len(stage1_candidates)

        partial_hash_groups: Dict[Tuple[int, str], List[FileSnapshot]] = {}
        stage2_bytes_read = 0

        for snap in stage1_candidates:
            sample_data, bytes_read, err = self._read_partial_bytes(snap)
            stage2_bytes_read += bytes_read

            if err is not None:
                report.skipped_modified_files.append({
                    "path": snap.path,
                    "reason": f"Stage 2 failed: {err}"
                })
                continue

            part_hash = self.hasher.compute_bytes(sample_data)
            group_key = (snap.size_bytes, part_hash)
            partial_hash_groups.setdefault(group_key, []).append(snap)

        stage2_candidates: List[FileSnapshot] = []
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
            return report

        # ------------------------------------------------------------------
        # Stage 3: Full Hash
        # ------------------------------------------------------------------
        t3_start = time.time()
        report.stage_3_full_hash.files_entered = len(stage2_candidates)

        full_hash_groups: Dict[Tuple[int, str], List[FileSnapshot]] = {}
        stage3_bytes_read = 0

        for snap in stage2_candidates:
            full_hash, bytes_read, err = self._stream_full_hash(snap)
            stage3_bytes_read += bytes_read

            if err is not None:
                report.skipped_modified_files.append({
                    "path": snap.path,
                    "reason": f"Stage 3 failed: {err}"
                })
                continue

            group_key = (snap.size_bytes, full_hash)
            full_hash_groups.setdefault(group_key, []).append(snap)

        confirmed_duplicate_groups = []
        dup_file_count = 0

        for key, group in full_hash_groups.items():
            if len(group) > 1:
                paths = [s.path for s in group]
                confirmed_duplicate_groups.append(paths)
                dup_file_count += len(group)

        report.stage_3_full_hash.candidate_files_remaining = dup_file_count
        report.stage_3_full_hash.bytes_read = stage3_bytes_read
        report.stage_3_full_hash.elapsed_ms = (time.time() - t3_start) * 1000

        # Summary calculations
        report.duplicate_groups_confirmed = len(confirmed_duplicate_groups)
        report.duplicate_files_count = dup_file_count
        report.duplicate_groups = confirmed_duplicate_groups
        report.total_bytes_read = stage2_bytes_read + stage3_bytes_read

        if report.total_bytes_discovered > 0:
            report.overall_byte_reduction_percentage = max(
                0.0,
                (1.0 - (report.total_bytes_read / report.total_bytes_discovered)) * 100.0
            )

        report.total_elapsed_ms = (time.time() - start_time_total) * 1000
        return report


def print_cli_report(report: DeduplicationReport) -> None:
    print("=" * 65)
    print("      EdgeLedger Runtime — Cascading Deduplication Report")
    print("=" * 65)
    print(f"Algorithm Used      : {report.configuration.get('hash_algorithm')}")
    print(f"Sample Window Size  : {report.configuration.get('sample_window_bytes')} bytes (Head/Tail)")
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
    print(f"  Duplicate Groups  : {report.duplicate_groups_confirmed} groups")
    print("=" * 65)

    if report.duplicate_groups:
        print("\nConfirmed Duplicate Groups:")
        for idx, group in enumerate(report.duplicate_groups, start=1):
            print(f" Group #{idx} ({len(group)} files):")
            for path in group:
                print(f"   - {path}")
        print()


def main():
    parser = argparse.ArgumentParser(description="EdgeLedger Runtime Cascading Deduplication PoC")
    parser.add_argument("target_dir", help="Directory path to scan for duplicate files")
    parser.add_argument("--json-report", help="Output path to write JSON report")
    parser.add_argument("--sample-window", type=int, default=DEFAULT_SAMPLE_WINDOW_BYTES, help="Sample window size in bytes")
    parser.add_argument("--algorithm", default="blake2b", help="Hash algorithm name (blake2b, sha256)")

    args = parser.parse_args()

    if not os.path.isdir(args.target_dir):
        print(f"Error: Target directory '{args.target_dir}' does not exist or is not a directory.", file=sys.stderr)
        sys.exit(1)

    pipeline = CascadingDeduplicationPipeline(
        sample_window_bytes=args.sample_window,
        hash_algorithm=args.algorithm
    )

    snapshots, unreadable = pipeline.scan_directory(args.target_dir)
    report = pipeline.process_files(snapshots, unreadable)

    print_cli_report(report)

    if args.json_report:
        try:
            with open(args.json_report, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, indent=2)
            print(f"JSON report written to: {args.json_report}")
        except Exception as e:
            print(f"Error writing JSON report: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
