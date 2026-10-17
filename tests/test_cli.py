import os
import sys
import tempfile
import unittest
from pathlib import Path

from heapline_gate.cli import parse_size
from helpers import run_main, write_record

MIB = 2**20


class ParseSizeTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_size("1000"), 1000)
        self.assertEqual(parse_size("512MiB"), 512 * MIB)
        self.assertEqual(parse_size("1.5 GiB"), 3 * 2**29)

    def test_rejects_garbage(self):
        for text in ("", "MiB", "12 MB", "-1", "1e3"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_size(text)


class TmpDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.records = self.dir / "records"
        self.records.mkdir()

    def budgets(self, text):
        path = self.dir / "budgets.csv"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def check(self, budgets_text):
        return run_main("check", "--budgets", self.budgets(budgets_text), "--records", str(self.records))


class CheckTest(TmpDirTestCase):
    def test_within_budget(self):
        write_record(self.records, "app.100.csv", peak=10 * MIB)
        code, out, _ = self.check("name,max_peak\napp,20MiB\n")
        self.assertEqual(code, 0)
        self.assertIn("ok", out)

    def test_over_budget(self):
        write_record(self.records, "app.100.csv", peak=30 * MIB)
        code, out, _ = self.check("name,max_peak\napp,20MiB\n")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)

    def test_budget_is_exact_boundary(self):
        write_record(self.records, "app.100.csv", peak=20 * MIB)
        self.assertEqual(self.check("name,max_peak\napp,20MiB\n")[0], 0)

    def test_highest_process_counts(self):
        write_record(self.records, "app.100.csv", peak=1 * MIB)
        write_record(self.records, "app.101.csv", peak=30 * MIB)
        self.assertEqual(self.check("name,max_peak\napp,20MiB\n")[0], 1)

    def test_name_is_not_a_prefix_match(self):
        write_record(self.records, "app.100.csv", peak=1 * MIB)
        write_record(self.records, "app.extra.100.csv", peak=999 * MIB)
        self.assertEqual(self.check("name,max_peak\napp,20MiB\n")[0], 0)

    def test_live_budget(self):
        write_record(self.records, "app.100.csv", peak=1 * MIB, live=5 * MIB)
        code, out, _ = self.check("name,max_peak,max_live\napp,20MiB,1MiB\n")
        self.assertEqual(code, 1)
        self.assertIn("live at exit", out)

    def test_live_budget_is_optional_per_row(self):
        write_record(self.records, "a.100.csv", peak=1 * MIB, live=500 * MIB)
        write_record(self.records, "b.101.csv", peak=1 * MIB, live=1)
        code, _, _ = self.check("name,max_peak,max_live\na,20MiB,\nb,20MiB,1MiB\n")
        self.assertEqual(code, 0)

    def test_comments_in_budgets(self):
        write_record(self.records, "app.100.csv")
        self.assertEqual(self.check("# memory budgets\nname,max_peak\napp,20MiB\n")[0], 0)

    def test_budget_without_record_is_an_error_not_a_pass(self):
        write_record(self.records, "app.100.csv")
        code, out, _ = self.check("name,max_peak\napp,20MiB\nother,20MiB\n")
        self.assertEqual(code, 2)
        self.assertIn("NO RECORD", out)

    def test_empty_directory_is_an_error(self):
        self.assertEqual(self.check("name,max_peak\napp,20MiB\n")[0], 2)

    def test_unreadable_record_does_not_count(self):
        write_record(self.records, "app.100.csv", magic="# heapline 2")
        code, _, err = self.check("name,max_peak\napp,20MiB\n")
        self.assertEqual(code, 2)
        self.assertIn("unsupported format version", err)

    def test_records_without_budget_are_listed(self):
        write_record(self.records, "app.100.csv")
        write_record(self.records, "stray.101.csv")
        code, _, err = self.check("name,max_peak\napp,20MiB\n")
        self.assertEqual(code, 0)
        self.assertIn("stray.101.csv", err)

    def test_bad_budget_file(self):
        write_record(self.records, "app.100.csv")
        for text in ("nom,max\napp,1\n", "name,max_peak\napp,lots\n"):
            with self.subTest(text=text):
                self.assertEqual(self.check(text)[0], 2)


class BaselineTest(TmpDirTestCase):
    def setUp(self):
        super().setUp()
        self.base = self.dir / "base"
        self.base.mkdir()

    def check(self, budgets_text, *extra):
        return run_main(
            "check", "--budgets", self.budgets(budgets_text), "--records", str(self.records),
            "--baseline", str(self.base), *extra,
        )

    def test_within_tolerance(self):
        write_record(self.base, "app.1.csv", peak=10 * MIB)
        write_record(self.records, "app.2.csv", peak=10 * MIB + MIB // 2)
        code, out, _ = self.check("name,max_peak\napp,+5%\n")
        self.assertEqual(code, 0, out)
        self.assertIn("+5% of 10.0 MiB", out)

    def test_over_tolerance(self):
        write_record(self.base, "app.1.csv", peak=10 * MIB)
        write_record(self.records, "app.2.csv", peak=10 * MIB + MIB // 2 + 1)
        self.assertEqual(self.check("name,max_peak\napp,+5%\n")[0], 1)

    def test_tolerance_is_exact_boundary(self):
        write_record(self.base, "app.1.csv", peak=1000)
        write_record(self.records, "app.2.csv", peak=1125)
        code, out, _ = self.check("name,max_peak\napp,+12.5%\n")
        self.assertEqual(code, 0)
        self.assertIn("+12.5% of", out)
        write_record(self.records, "app.3.csv", peak=1126)
        self.assertEqual(self.check("name,max_peak\napp,+12.5%\n")[0], 1)

    def test_highest_baseline_process_counts(self):
        write_record(self.base, "app.1.csv", peak=1 * MIB)
        write_record(self.base, "app.2.csv", peak=10 * MIB)
        write_record(self.records, "app.3.csv", peak=10 * MIB)
        self.assertEqual(self.check("name,max_peak\napp,+0%\n")[0], 0)

    def test_name_missing_from_baseline_is_an_error(self):
        write_record(self.records, "app.2.csv")
        code, out, _ = self.check("name,max_peak\napp,+5%\n")
        self.assertEqual(code, 2)
        self.assertIn("NO BASELINE", out)

    def test_relative_budget_needs_a_baseline(self):
        write_record(self.records, "app.2.csv")
        code, _, err = TmpDirTestCase.check(self, "name,max_peak\napp,+5%\n")
        self.assertEqual(code, 2)
        self.assertIn("--baseline", err)

    def test_absolute_and_relative_together(self):
        write_record(self.base, "a.1.csv", peak=10 * MIB)
        write_record(self.records, "a.2.csv", peak=10 * MIB)
        write_record(self.records, "b.3.csv", peak=30 * MIB)
        code, out, _ = self.check("name,max_peak\na,+5%\nb,20MiB\n")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"a .* ok")

    def test_unreadable_baseline_record_is_reported(self):
        write_record(self.base, "app.1.csv", magic="# heapline 2")
        write_record(self.records, "app.2.csv")
        code, _, err = self.check("name,max_peak\napp,+5%\n")
        self.assertEqual(code, 2)
        self.assertIn("baseline", err)

    def test_bad_percentages(self):
        write_record(self.records, "app.2.csv")
        for text in ("+%", "+abc%", "-5%", "5%", "+5"):
            with self.subTest(text=text):
                self.assertEqual(self.check("name,max_peak\napp,%s\n" % text)[0], 2)

    def test_row_without_max_peak(self):
        write_record(self.records, "app.2.csv")
        self.assertEqual(self.check("name,max_peak\napp\n")[0], 2)


class RunTest(TmpDirTestCase):
    def test_missing_library(self):
        code, _, err = run_main("run", "--lib", str(self.dir / "nope.so"), "--records", str(self.records), "--", "true")
        self.assertEqual(code, 2)
        self.assertIn("is not a file", err)

    def test_no_command(self):
        lib = self.dir / "lib.so"
        lib.write_bytes(b"")
        code, _, err = run_main("run", "--lib", str(lib), "--records", str(self.records), "--")
        self.assertEqual(code, 2)
        self.assertIn("no command", err)

    @unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux")
    def test_unknown_command(self):
        lib = self.dir / "lib.so"
        lib.write_bytes(b"")
        code, _, err = run_main("run", "--lib", str(lib), "--records", str(self.records), "--", "/no/such/program")
        self.assertEqual(code, 127)
        self.assertIn("cannot run", err)


# Skipped only when HEAPLINE_LIB is unset: a wrong path must fail, not skip.
@unittest.skipUnless(
    sys.platform.startswith("linux") and "HEAPLINE_LIB" in os.environ,
    "set HEAPLINE_LIB to a built libheapline.so (Linux only)",
)
class ContractTest(TmpDirTestCase):
    """Runs the real library and reads its real record: guards the format."""

    def run_app(self):
        code, _, _ = run_main(
            "run", "--lib", os.environ["HEAPLINE_LIB"], "--records", str(self.records / "sub"),
            "--name", "alloc", "--", sys.executable, "-c", "x = bytes(20_000_000)",
        )
        self.assertEqual(code, 0)

    def test_budget_exceeded(self):
        self.run_app()
        code, out, err = run_main("check", "--budgets", self.budgets("name,max_peak\nalloc,1MiB\n"), "--records", str(self.records / "sub"))
        self.assertEqual((code, err), (1, ""), out)

    def test_budget_respected(self):
        self.run_app()
        code, out, err = run_main("check", "--budgets", self.budgets("name,max_peak\nalloc,512MiB\n"), "--records", str(self.records / "sub"))
        self.assertEqual((code, err), (0, ""), out)

    def test_run_leaves_the_callers_environment_alone(self):
        self.run_app()
        self.assertNotIn("HEAPLINE_OUTPUT", os.environ)


if __name__ == "__main__":
    unittest.main()
