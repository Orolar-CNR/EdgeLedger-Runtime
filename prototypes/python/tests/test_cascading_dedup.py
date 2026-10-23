#!/usr/bin/env python3
"""
Automated Test Suite for EdgeLedger Runtime Cascading Deduplication PoC.

Covers 13 comprehensive test scenarios:
 1. Unique file sizes are excluded from further hashing (Stage 1 filter).
 2. Same-size files with different contents are not incorrectly grouped as duplicates.
 3. Matching partial hashes but different full contents do not produce a false duplicate group.
 4. Exact duplicates are grouped correctly.
 5. A file modified during processing is detected/rejected per snapshot consistency policy.
 6. Unreadable files are handled safely with errors reported.
 7. Files smaller than sampling window are handled without duplicate reads.
 8. Empty files (0 bytes), including multiple empty files.
 9. Identical files with sizes smaller than, equal to, and larger than combined sampling window.
10. Files that become unavailable or change size while being processed.
11. Hash/read errors and malformed or inaccessible input paths.
12. JSON report validity and consistency between reported metrics and actual operations.
13. Repeatability: identical dataset and configuration produce equivalent results.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from typing import List

from prototypes.python.cascading_dedup import (
    CascadingDeduplicationPipeline,
    FileSnapshot,
    DEFAULT_SAMPLE_WINDOW_BYTES
)


class TestCascadingDeduplication(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="edgeledger_test_")
        self.pipeline = CascadingDeduplicationPipeline(
            sample_window_bytes=1024,  # Use 1 KiB window for fast unit testing
            hash_algorithm="blake2b"
        )

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_file(self, rel_path: str, content: bytes) -> str:
        full_path = os.path.join(self.test_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)
        return full_path

    # -------------------------------------------------------------------------
    # Scenario 1: Unique file sizes excluded from Stage 2/3 hashing
    # -------------------------------------------------------------------------
    def test_scenario_1_unique_sizes_excluded(self):
        self._create_file("f1.txt", b"A" * 100)
        self._create_file("f2.txt", b"B" * 200)
        self._create_file("f3.txt", b"C" * 300)

        snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)
        report = self.pipeline.process_files(snapshots, unreadable)

        self.assertEqual(report.stage_1_size_filter.files_entered, 3)
        self.assertEqual(report.stage_1_size_filter.candidate_files_remaining, 0)
        self.assertEqual(report.stage_2_partial_hash.files_entered, 0)
        self.assertEqual(report.total_bytes_read, 0)
        self.assertEqual(report.duplicate_groups_confirmed, 0)

    # -------------------------------------------------------------------------
    # Scenario 2: Same-size files with different contents
    # -------------------------------------------------------------------------
    def test_scenario_2_same_size_different_content(self):
        self._create_file("f1.txt", b"ALPHA_" + b"X" * 1000)
        self._create_file("f2.txt", b"BETA__" + b"Y" * 1000)

        snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)
        report = self.pipeline.process_files(snapshots, unreadable)

        self.assertEqual(report.stage_1_size_filter.candidate_files_remaining, 2)
        self.assertEqual(report.stage_2_partial_hash.candidate_files_remaining, 0)
        self.assertEqual(report.stage_3_full_hash.files_entered, 0)
        self.assertEqual(report.duplicate_groups_confirmed, 0)

    # -------------------------------------------------------------------------
    # Scenario 3: Same partial hash (same head/tail) but different middle
    # -------------------------------------------------------------------------
    def test_scenario_3_same_partial_different_middle(self):
        window = 1024
        head = b"H" * window
        tail = b"T" * window
        padding = b"X" * 3000

        # File 1: Head + Middle_1 + Tail
        content1 = head + b"MIDDLE_CONTENT_ONE_" + padding + tail
        # File 2: Head + Middle_2 + Tail (Same size, same 1 KiB head/tail)
        content2 = head + b"MIDDLE_CONTENT_TWO_" + padding + tail

        self.assertEqual(len(content1), len(content2))

        p1 = self._create_file("f1.bin", content1)
        p2 = self._create_file("f2.bin", content2)

        snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)
        report = self.pipeline.process_files(snapshots, unreadable)

        self.assertEqual(report.stage_1_size_filter.candidate_files_remaining, 2)
        self.assertEqual(report.stage_2_partial_hash.candidate_files_remaining, 2)
        self.assertEqual(report.stage_3_full_hash.files_entered, 2)
        self.assertEqual(report.duplicate_groups_confirmed, 0)

    # -------------------------------------------------------------------------
    # Scenario 4: Exact duplicates grouped correctly
    # -------------------------------------------------------------------------
    def test_scenario_4_exact_duplicates_grouped(self):
        content = b"EXACT_MATCH_CONTENT_" * 500
        p1 = self._create_file("f1.bin", content)
        p2 = self._create_file("sub/f2.bin", content)

        snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)
        report = self.pipeline.process_files(snapshots, unreadable)

        self.assertEqual(report.duplicate_groups_confirmed, 1)
        self.assertEqual(report.duplicate_files_count, 2)
        group = report.duplicate_groups[0]
        self.assertIn(os.path.abspath(p1), group)
        self.assertIn(os.path.abspath(p2), group)

    # -------------------------------------------------------------------------
    # Scenario 5: File modified during read rejected via snapshot check
    # -------------------------------------------------------------------------
    def test_scenario_5_file_modified_during_processing(self):
        content1 = b"INITIAL_CONTENT_" * 200
        p1 = self._create_file("f1.bin", content1)
        p2 = self._create_file("f2.bin", content1)

        snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)

        # Modify f1 on disk after snapshot capture
        time.sleep(0.01)
        with open(p1, "wb") as f:
            f.write(b"MODIFIED_CONTENT_ALTERED")

        report = self.pipeline.process_files(snapshots, unreadable)

        self.assertEqual(len(report.skipped_modified_files), 1)
        self.assertIn(os.path.abspath(p1), report.skipped_modified_files[0]["path"])
        self.assertEqual(report.duplicate_groups_confirmed, 0)

    # -------------------------------------------------------------------------
    # Scenario 6: Unreadable files handled safely
    # -------------------------------------------------------------------------
    def test_scenario_6_unreadable_file_handling(self):
        p1 = self._create_file("readable.txt", b"DATA" * 100)
        p2 = self._create_file("unreadable.txt", b"DATA" * 100)

        # Remove read permissions on unreadable.txt
        os.chmod(p2, 0o000)

        try:
            snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)
            report = self.pipeline.process_files(snapshots, unreadable)

            # Either caught during scan_directory or during stage processing
            total_issues = len(report.unreadable_files) + len(report.skipped_modified_files)
            self.assertGreaterEqual(total_issues, 1)
        finally:
            # Restore permissions for cleanup
            os.chmod(p2, 0o644)

    # -------------------------------------------------------------------------
    # Scenario 7: Files smaller than sample window read without duplicate ranges
    # -------------------------------------------------------------------------
    def test_scenario_7_small_files_no_duplicate_reads(self):
        # Window size is 1024 bytes. File size is 500 bytes (< combined 2048 window).
        content = b"SMALL_FILE_" * 40
        p1 = self._create_file("f1.bin", content)
        p2 = self._create_file("f2.bin", content)

        snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)
        report = self.pipeline.process_files(snapshots, unreadable)

        self.assertEqual(report.duplicate_groups_confirmed, 1)
        # In Stage 2, small files <= 2*window are read completely once (500 bytes each = 1000 bytes)
        self.assertEqual(report.stage_2_partial_hash.bytes_read, len(content) * 2)

    # -------------------------------------------------------------------------
    # Scenario 8: Multiple empty files (0 bytes)
    # -------------------------------------------------------------------------
    def test_scenario_8_empty_files(self):
        p1 = self._create_file("empty1.txt", b"")
        p2 = self._create_file("empty2.txt", b"")

        snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)
        report = self.pipeline.process_files(snapshots, unreadable)

        self.assertEqual(report.duplicate_groups_confirmed, 1)
        self.assertEqual(report.duplicate_files_count, 2)
        self.assertEqual(report.total_bytes_read, 0)

    # -------------------------------------------------------------------------
    # Scenario 9: Identical files smaller, equal, and larger than window
    # -------------------------------------------------------------------------
    def test_scenario_9_size_threshold_edge_cases(self):
        window = 1024
        # Case A: smaller than window (500 bytes)
        c_small = b"S" * 500
        self._create_file("small1.bin", c_small)
        self._create_file("small2.bin", c_small)

        # Case B: equal to combined window (2048 bytes)
        c_equal = b"E" * (2 * window)
        self._create_file("equal1.bin", c_equal)
        self._create_file("equal2.bin", c_equal)

        # Case C: larger than combined window (5000 bytes)
        c_large = b"L" * 5000
        self._create_file("large1.bin", c_large)
        self._create_file("large2.bin", c_large)

        snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)
        report = self.pipeline.process_files(snapshots, unreadable)

        self.assertEqual(report.duplicate_groups_confirmed, 3)
        self.assertEqual(report.duplicate_files_count, 6)

    # -------------------------------------------------------------------------
    # Scenario 10: File deleted after initial scan before hashing
    # -------------------------------------------------------------------------
    def test_scenario_10_file_deleted_during_processing(self):
        content = b"WILL_BE_DELETED_" * 100
        p1 = self._create_file("f1.bin", content)
        p2 = self._create_file("f2.bin", content)

        snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)

        # Remove f1 before pipeline execution
        os.remove(p1)

        report = self.pipeline.process_files(snapshots, unreadable)

        self.assertEqual(len(report.skipped_modified_files), 1)
        self.assertEqual(report.duplicate_groups_confirmed, 0)

    # -------------------------------------------------------------------------
    # Scenario 11: Non-existent or invalid directory path
    # -------------------------------------------------------------------------
    def test_scenario_11_invalid_directory_path(self):
        invalid_path = os.path.join(self.test_dir, "non_existent_folder")
        snapshots, unreadable = self.pipeline.scan_directory(invalid_path)
        self.assertEqual(len(snapshots), 0)
        self.assertEqual(len(unreadable), 0)

    # -------------------------------------------------------------------------
    # Scenario 12: JSON report format validity & consistency
    # -------------------------------------------------------------------------
    def test_scenario_12_json_report_validity(self):
        content = b"JSON_VALIDATION_DATA_" * 100
        self._create_file("f1.bin", content)
        self._create_file("f2.bin", content)

        snapshots, unreadable = self.pipeline.scan_directory(self.test_dir)
        report = self.pipeline.process_files(snapshots, unreadable)

        report_json_path = os.path.join(self.test_dir, "report.json")
        report_dict = report.to_dict()

        with open(report_json_path, "w") as f:
            json.dump(report_dict, f)

        self.assertTrue(os.path.exists(report_json_path))
        with open(report_json_path, "r") as f:
            loaded = json.load(f)

        self.assertEqual(loaded["total_files_discovered"], 2)
        self.assertEqual(loaded["duplicate_groups_confirmed"], 1)
        self.assertIn("stage_1_size_filter", loaded)
        self.assertIn("stage_2_partial_hash", loaded)
        self.assertIn("stage_3_full_hash", loaded)

    # -------------------------------------------------------------------------
    # Scenario 13: Repeatability of duplicate detection results
    # -------------------------------------------------------------------------
    def test_scenario_13_repeatability(self):
        c1 = b"DATA_SET_ONE_" * 100
        c2 = b"DATA_SET_TWO_" * 150
        self._create_file("group1_a.bin", c1)
        self._create_file("group1_b.bin", c1)
        self._create_file("group2_a.bin", c2)
        self._create_file("group2_b.bin", c2)

        # Run 1
        snaps1, unread1 = self.pipeline.scan_directory(self.test_dir)
        report1 = self.pipeline.process_files(snaps1, unread1)

        # Run 2
        snaps2, unread2 = self.pipeline.scan_directory(self.test_dir)
        report2 = self.pipeline.process_files(snaps2, unread2)

        self.assertEqual(report1.duplicate_groups_confirmed, report2.duplicate_groups_confirmed)
        self.assertEqual(report1.total_bytes_read, report2.total_bytes_read)
        self.assertEqual(report1.duplicate_files_count, report2.duplicate_files_count)


if __name__ == "__main__":
    unittest.main()
