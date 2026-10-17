import tempfile
import unittest
from pathlib import Path

from heapline_gate.records import COLUMNS, read_record, read_records
from helpers import record_text, write_record


class ReadRecordTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_valid(self):
        path = write_record(self.dir, "a.1.csv", peak=123456, live=42)
        record = read_record(path)
        self.assertEqual(record.peak_heap_bytes, 123456)
        self.assertEqual(record.live_heap_at_exit_bytes, 42)

    def test_refuses_other_version(self):
        path = write_record(self.dir, "a.1.csv", magic="# heapline 2")
        with self.assertRaisesRegex(ValueError, "unsupported format version"):
            read_record(path)

    def test_refuses_foreign_csv(self):
        path = self.dir / "other.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not a heapline record"):
            read_record(path)

    def test_refuses_empty_file(self):
        path = self.dir / "empty.csv"
        path.write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_record(path)

    def test_refuses_added_column(self):
        path = write_record(self.dir, "a.1.csv", columns=COLUMNS + ["extra"])
        with self.assertRaisesRegex(ValueError, "unexpected columns"):
            read_record(path)

    def test_refuses_truncated_values(self):
        path = self.dir / "a.1.csv"
        text = record_text()
        path.write_text(text.rsplit("\n", 2)[0] + "\n0,node-a,1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fields"):
            read_record(path)

    def test_refuses_header_only(self):
        path = self.dir / "a.1.csv"
        path.write_text("\n".join(record_text().splitlines()[:2]) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_record(path)

    def test_refuses_non_integer_peak(self):
        values = ["0", "h", "1", "", "1.0", "5", "4", "lots", "0.5", "10", "1", "1"]
        path = write_record(self.dir, "a.1.csv", values=values)
        with self.assertRaisesRegex(ValueError, "not an integer"):
            read_record(path)

    def test_refuses_negative_heap(self):
        for peak, live in ((-1, 10), (10, -1)):
            with self.subTest(peak=peak, live=live):
                path = write_record(self.dir, "a.1.csv", peak=peak, live=live)
                with self.assertRaisesRegex(ValueError, "negative"):
                    read_record(path)

    def test_directory_skips_bad_files_and_reports_them(self):
        write_record(self.dir, "good.1.csv")
        write_record(self.dir, "bad.2.csv", magic="# heapline 9")
        (self.dir / "notes.txt").write_text("ignored", encoding="utf-8")
        records, errors = read_records(self.dir)
        self.assertEqual([r.path.name for r in records], ["good.1.csv"])
        self.assertEqual(len(errors), 1)
        self.assertIn("bad.2.csv", errors[0])

    def test_missing_directory_gives_nothing(self):
        records, errors = read_records(self.dir / "absent")
        self.assertEqual((records, errors), ([], []))


if __name__ == "__main__":
    unittest.main()
