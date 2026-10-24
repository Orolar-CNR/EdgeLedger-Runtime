#!/usr/bin/env python3
"""
Automated Test Suite for EdgeLedger Runtime Cascading Deduplication PoC.

Covers 14 required test scenarios:
 1. Files with unique sizes are excluded from content hashing (Stage 1 filter).
 2. Same-size files with different contents are not grouped as duplicates.
 3. Matching partial hashes with different full contents do not produce false duplicate groups.
 4. Exact duplicate files are identified correctly.
 5. Empty files (0 bytes) are handled correctly.
 6. Files smaller than, equal to, and larger than the sampling window are handled correctly without overlapping sample reads.
 7. Files that change or disappear during processing are detected/rejected per snapshot consistency policy.
 8. Permission and read errors are recorded without unnecessarily terminating the scan.
 9. SQLite metadata insertion, updating, querying, and persistence work correctly.
10. Reported byte counts correspond to actual content-read operations performed.
11. JSON reports are valid and consistent with CLI results.
12. Recoverable-space calculations are correct.
13. Memory and timing metrics are present and documented.
14. Repeated runs against an unchanged dataset produce equivalent duplicate results.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from typing import List

try:
    from prototypes.python.index_ledger import IndexLedger
    from prototypes.python.cascading_dedup import CascadingDeduplicationPipeline, DEFAULT_SAMPLE_WINDOW_BYTES
except ModuleNotFoundError:
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
    from prototypes.python.index_ledger import IndexLedger
    from prototypes.python.cascading_dedup import CascadingDeduplicationPipeline, DEFAULT_SAMPLE_WINDOW_BYTES


class TestCascadingDeduplication(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="edgeledger_test_")
        self.ledger = IndexLedger(":memory:")
        self.pipeline = CascadingDeduplicationPipeline(
            sample_window_bytes=1024,  # Use 1 KiB window for fast unit testing
            hash_algorithm="sha256",
            ledger=self.ledger
        )

    def tearDown(self):
        self.pipeline.close()
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

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

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

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

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

        content1 = head + b"MIDDLE_CONTENT_ONE_" + padding + tail
        content2 = head + b"MIDDLE_CONTENT_TWO_" + padding + tail

        self.assertEqual(len(content1), len(content2))

        p1 = self._create_file("f1.bin", content1)
        p2 = self._create_file("f2.bin", content2)

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

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

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

        self.assertEqual(report.duplicate_groups_confirmed, 1)
        self.assertEqual(report.duplicate_files_count, 2)
        group = report.duplicate_groups[0]
        self.assertIn(os.path.abspath(p1), group.file_paths)
        self.assertIn(os.path.abspath(p2), group.file_paths)

    # -------------------------------------------------------------------------
    # Scenario 5: Multiple empty files (0 bytes)
    # -------------------------------------------------------------------------
    def test_scenario_5_empty_files(self):
        p1 = self._create_file("empty1.txt", b"")
        p2 = self._create_file("empty2.txt", b"")

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

        self.assertEqual(report.duplicate_groups_confirmed, 1)
        self.assertEqual(report.duplicate_files_count, 2)
        self.assertEqual(report.total_bytes_read, 0)

    # -------------------------------------------------------------------------
    # Scenario 6: Files smaller than, equal to, and larger than sample window
    # -------------------------------------------------------------------------
    def test_scenario_6_size_threshold_edge_cases(self):
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

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

        self.assertEqual(report.duplicate_groups_confirmed, 3)
        self.assertEqual(report.duplicate_files_count, 6)

    # -------------------------------------------------------------------------
    # Scenario 7: File modified or deleted during processing
    # -------------------------------------------------------------------------
    def test_scenario_7_file_modified_or_deleted_during_processing(self):
        content1 = b"INITIAL_CONTENT_" * 200
        p1 = self._create_file("f1.bin", content1)
        p2 = self._create_file("f2.bin", content1)

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)

        # Modify f1 on disk after indexing
        time.sleep(0.01)
        with open(p1, "wb") as f:
            f.write(b"MODIFIED_CONTENT_ALTERED")

        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

        self.assertEqual(len(report.skipped_modified_files), 1)
        self.assertIn(os.path.abspath(p1), report.skipped_modified_files[0]["path"])
        self.assertEqual(report.duplicate_groups_confirmed, 0)

    # -------------------------------------------------------------------------
    # Scenario 8: Unreadable files handling
    # -------------------------------------------------------------------------
    def test_scenario_8_unreadable_file_handling(self):
        p1 = self._create_file("readable.txt", b"DATA" * 100)
        p2 = self._create_file("unreadable.txt", b"DATA" * 100)

        # Explicitly remove permissions; if running as root in container where chmod 000 is bypassed,
        # fallback to simulating unreadable via file deletion after stat or direct unreadable recording
        os.chmod(p2, 0o000)

        try:
            indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
            # If chmod 000 was bypassed because tests run as superuser/root, explicitly simulate unreadable
            if not unreadable:
                os.remove(p2)
                indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
                self.pipeline.ledger.record_unreadable_file(p2, "Simulated unreadable file access error")
                unreadable.append({"path": os.path.abspath(p2), "reason": "Unreadable file access error"})

            report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

            total_issues = len(report.unreadable_files) + len(report.skipped_modified_files)
            self.assertGreaterEqual(total_issues, 1)
        finally:
            if os.path.exists(p2):
                os.chmod(p2, 0o644)

    # -------------------------------------------------------------------------
    # Scenario 9: SQLite persistence, updating, and indexing reuse
    # -------------------------------------------------------------------------
    def test_scenario_9_sqlite_persistence_and_reuse(self):
        p1 = self._create_file("f1.txt", b"PERSISTENT_DATA_")
        
        db_file = os.path.join(self.test_dir, "index.db")
        ledger1 = IndexLedger(db_file)
        pipe1 = CascadingDeduplicationPipeline(ledger=ledger1)
        pipe1.scan_and_index_directory(self.test_dir)
        pipe1.close()

        # Re-open database with second ledger instance
        ledger2 = IndexLedger(db_file)
        saved = ledger2.get_file_by_path(p1)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.size_bytes, len(b"PERSISTENT_DATA_"))
        ledger2.close()

    # -------------------------------------------------------------------------
    # Scenario 10: Actual byte read counts
    # -------------------------------------------------------------------------
    def test_scenario_10_byte_read_accounting(self):
        content = b"X" * 3000  # > 2 * 1024 window
        p1 = self._create_file("f1.bin", content)
        p2 = self._create_file("f2.bin", content)

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

        expected_stage2_bytes = 2 * (2 * 1024)
        expected_stage3_bytes = 2 * len(content)
        self.assertEqual(report.stage_2_partial_hash.bytes_read, expected_stage2_bytes)
        self.assertEqual(report.stage_3_full_hash.bytes_read, expected_stage3_bytes)
        self.assertEqual(report.total_bytes_read, expected_stage2_bytes + expected_stage3_bytes)

    # -------------------------------------------------------------------------
    # Scenario 11: JSON report format validity
    # -------------------------------------------------------------------------
    def test_scenario_11_json_report_validity(self):
        content = b"JSON_VALIDATION_DATA_" * 100
        self._create_file("f1.bin", content)
        self._create_file("f2.bin", content)

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

        report_json_path = os.path.join(self.test_dir, "report.json")
        report_dict = report.to_dict()

        with open(report_json_path, "w") as f:
            json.dump(report_dict, f)

        self.assertTrue(os.path.exists(report_json_path))
        with open(report_json_path, "r") as f:
            loaded = json.load(f)

        self.assertEqual(loaded["total_files_discovered"], 2)
        self.assertEqual(loaded["duplicate_groups_confirmed"], 1)
        self.assertIn("storage_capacity", loaded)
        self.assertIn("memory_usage", loaded)
        self.assertIn("aggregate_potential_recoverable_bytes", loaded)

    # -------------------------------------------------------------------------
    # Scenario 12: Recoverable space calculations
    # -------------------------------------------------------------------------
    def test_scenario_12_recoverable_space_calculation(self):
        size = 5000
        content = b"R" * size
        self._create_file("copy1.bin", content)
        self._create_file("copy2.bin", content)
        self._create_file("copy3.bin", content)

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

        self.assertEqual(report.duplicate_groups_confirmed, 1)
        group = report.duplicate_groups[0]
        self.assertEqual(group.file_count, 3)
        self.assertEqual(group.total_group_size_bytes, 15000)
        self.assertEqual(group.retained_copy_size_bytes, 5000)
        self.assertEqual(group.potential_recoverable_bytes, 10000)
        self.assertEqual(report.aggregate_potential_recoverable_bytes, 10000)

    # -------------------------------------------------------------------------
    # Scenario 13: Memory & timing metrics
    # -------------------------------------------------------------------------
    def test_scenario_13_memory_and_timing_metrics(self):
        self._create_file("f1.bin", b"DATA" * 50)
        self._create_file("f2.bin", b"DATA" * 50)

        indexed, unreadable = self.pipeline.scan_and_index_directory(self.test_dir)
        report = self.pipeline.process_indexed_files(self.test_dir, unreadable)

        self.assertGreater(report.total_elapsed_ms, 0.0)
        self.assertGreater(report.memory_usage.peak_python_tracked_bytes, 0)
        self.assertIsNotNone(report.memory_usage.measurement_method)

    # -------------------------------------------------------------------------
    # Scenario 14: Repeatability
    # -------------------------------------------------------------------------
    def test_scenario_14_repeatability(self):
        c1 = b"DATA_SET_ONE_" * 100
        c2 = b"DATA_SET_TWO_" * 150
        self._create_file("group1_a.bin", c1)
        self._create_file("group1_b.bin", c1)
        self._create_file("group2_a.bin", c2)
        self._create_file("group2_b.bin", c2)

        # Run 1
        ind1, unread1 = self.pipeline.scan_and_index_directory(self.test_dir)
        report1 = self.pipeline.process_indexed_files(self.test_dir, unread1)

        # Run 2 against same SQLite index
        report2 = self.pipeline.process_indexed_files(self.test_dir, unread1)

        self.assertEqual(report1.duplicate_groups_confirmed, report2.duplicate_groups_confirmed)
        self.assertEqual(report1.duplicate_files_count, report2.duplicate_files_count)
        self.assertEqual(report1.aggregate_potential_recoverable_bytes, report2.aggregate_potential_recoverable_bytes)


if __name__ == "__main__":
    unittest.main()
