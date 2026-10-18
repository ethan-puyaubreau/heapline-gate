import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from heapline_gate.cli import find_lib, parse_size, read_budgets, size
from helpers import run_main, write_record

MIB = 2**20


class ParseSizeTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_size("1000"), 1000)
        self.assertEqual(parse_size("512MiB"), 512 * MIB)
        self.assertEqual(parse_size("1.5 GiB"), 3 * 2**29)
        self.assertEqual(parse_size("0.1KiB"), 102)
        self.assertEqual(parse_size("1" + "0" * 400), 10**400)

    def test_size_picks_a_readable_unit(self):
        self.assertEqual(size(0), "0 B")
        self.assertEqual(size(1023), "1023 B")
        self.assertEqual(size(1536), "1.5 KiB")
        self.assertEqual(size(512 * MIB), "512.0 MiB")
        self.assertEqual(size(3 * 2**30), "3.0 GiB")

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

    def test_byte_order_mark_and_windows_line_ends(self):
        write_record(self.records, "app.100.csv")
        self.assertEqual(self.check("﻿name,max_peak\r\napp,20MiB\r\n")[0], 0)

    def test_budget_without_record_is_an_error_not_a_pass(self):
        write_record(self.records, "app.100.csv")
        code, out, _ = self.check("name,max_peak\napp,20MiB\nother,20MiB\n")
        self.assertEqual(code, 2)
        self.assertIn("NO RECORD", out)

    def test_empty_directory_is_an_error(self):
        self.assertEqual(self.check("name,max_peak\napp,20MiB\n")[0], 2)

    def test_records_must_be_a_directory(self):
        for path in (self.dir / "absent", self.budgets("name,max_peak\napp,1MiB\n")):
            with self.subTest(path=path):
                code, _, err = run_main("check", "--budgets", self.budgets("name,max_peak\napp,1MiB\n"), "--records", str(path))
                self.assertEqual(code, 2)
                self.assertIn("is not a directory", err)

    def test_baseline_must_be_a_directory(self):
        write_record(self.records, "app.100.csv")
        code, _, err = run_main(
            "check", "--budgets", self.budgets("name,max_peak\napp,+5%\n"), "--records", str(self.records),
            "--baseline", str(self.dir / "absent"),
        )
        self.assertEqual(code, 2)
        self.assertIn("is not a directory", err)

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
        for text in (
            "nom,max\napp,1\n",
            "name,max_peak\napp,lots\n",
            "name,max_peak\napp,1MiB\napp,2MiB\n",
            "name,max_peak\n,1MiB\n",
            "name,max_peak\n",
        ):
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
        code, out, _ = self.check("name,max_peak\napp,+5%\n")
        self.assertEqual(code, 1)
        self.assertIn("peak over by 1 B", out)

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


class ReadmeTest(TmpDirTestCase):
    def test_budget_examples_are_valid(self):
        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
        examples = re.findall(r"```\n(name,max_peak.*?)```", readme, re.S)
        self.assertEqual(len(examples), 2)
        for text in examples:
            with self.subTest(text=text):
                self.assertTrue(read_budgets(self.budgets(text)))


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

    def child_env(self, *options):
        """Run a child that writes LD_PRELOAD and HEAPLINE_OUTPUT to a file."""
        (self.dir / "lib.so").write_bytes(b"")
        out = self.dir / "env.txt"
        script = "import os, sys; open(sys.argv[1], 'w').write(os.environ['LD_PRELOAD'] + '\\n' + os.environ['HEAPLINE_OUTPUT'])"
        code, _, err = run_main("run", *options, "--", sys.executable, "-c", script, str(out))
        self.assertEqual(code, 0, err)
        return out.read_text().splitlines()

    def test_paths_given_to_the_child_are_absolute(self):
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(str(self.dir))
        preload, output = self.child_env("--lib", "lib.so", "--records", "rel", "--name", "app")
        self.assertEqual(Path(preload), (self.dir / "lib.so").resolve())
        self.assertEqual(Path(output), (self.dir / "rel").resolve() / "app.%p.csv")

    def test_keeps_an_existing_preload_after_heapline(self):
        os.environ["LD_PRELOAD"] = "/opt/other.so"
        self.addCleanup(os.environ.pop, "LD_PRELOAD")
        preload, _ = self.child_env("--lib", str(self.dir / "lib.so"), "--records", str(self.records), "--name", "app")
        self.assertEqual(preload, "%s:/opt/other.so" % (self.dir / "lib.so").resolve())

    def test_bad_names(self):
        lib = self.dir / "lib.so"
        lib.write_bytes(b"")
        for name in ("suite/parser", "bench%p", "a b", "x\\y"):
            with self.subTest(name=name):
                code, _, err = run_main("run", "--lib", str(lib), "--records", str(self.records), "--name", name, "--", "true")
                self.assertEqual(code, 2)
                self.assertIn("bad name", err)

    def test_records_path_is_a_file(self):
        lib = self.dir / "lib.so"
        lib.write_bytes(b"")
        code, _, err = run_main("run", "--lib", str(lib), "--records", str(lib), "--name", "app", "--", "true")
        self.assertEqual(code, 2)
        self.assertIn("cannot use", err)

    def test_refuses_records_left_from_an_earlier_run(self):
        lib = self.dir / "lib.so"
        lib.write_bytes(b"")
        write_record(self.records, "app.100.csv")
        write_record(self.records, "app.extra.101.csv")
        code, _, err = run_main("run", "--lib", str(lib), "--records", str(self.records), "--name", "app", "--", "true")
        self.assertEqual(code, 2)
        self.assertIn("app.100.csv", err)
        self.assertNotIn("app.extra", err)

    @unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux")
    def test_unknown_command(self):
        lib = self.dir / "lib.so"
        lib.write_bytes(b"")
        code, _, err = run_main("run", "--lib", str(lib), "--records", str(self.records), "--", "/no/such/program")
        self.assertEqual(code, 127)
        self.assertIn("cannot run", err)

    def test_explicit_lib_does_not_ask_the_linker(self):
        with mock.patch("heapline_gate.cli.find_lib") as find:
            preload, _ = self.child_env("--lib", str(self.dir / "lib.so"), "--records", str(self.records), "--name", "app")
        find.assert_not_called()
        self.assertEqual(Path(preload), (self.dir / "lib.so").resolve())

    def test_without_lib_preloads_what_the_linker_found(self):
        lib = self.dir / "lib.so"
        lib.write_bytes(b"")
        out = self.dir / "env.txt"
        script = "import os, sys; open(sys.argv[1], 'w').write(os.environ['LD_PRELOAD'])"
        with mock.patch("heapline_gate.cli.find_lib", return_value=(str(lib), None)):
            code, _, err = run_main("run", "--records", str(self.records), "--name", "app", "--", sys.executable, "-c", script, str(out))
        self.assertEqual(code, 0, err)
        self.assertIn("heapline-gate: using %s" % lib, err)
        preload = out.read_text()
        self.assertTrue(os.path.isabs(preload), preload)
        self.assertTrue(os.path.samefile(preload, str(lib)))

    def test_without_lib_and_none_found_the_command_does_not_run(self):
        ran = self.dir / "ran"
        script = "import sys; open(sys.argv[1], 'w')"
        with mock.patch("heapline_gate.cli.find_lib", return_value=(None, "the dynamic linker did not report libheapline.so")):
            code, _, err = run_main("run", "--records", str(self.records), "--name", "app", "--", sys.executable, "-c", script, str(ran))
        self.assertEqual(code, 2)
        self.assertFalse(ran.exists())
        self.assertIn("libheapline.so", err)
        self.assertIn("install the heapline package or pass --lib", err)


class FindLibTest(TmpDirTestCase):
    """find_lib reads ld.so's trace: anything but a clear answer is a miss."""

    def find(self, stdout=b"", stderr=b"", env=None):
        trace = subprocess.CompletedProcess([], 0, stdout=stdout, stderr=stderr)
        with mock.patch("heapline_gate.cli.subprocess.run", return_value=trace) as run:
            return find_lib(env or {}), run

    def lib(self, directory="lib"):
        path = self.dir / directory / "libheapline.so"
        path.parent.mkdir()
        path.write_bytes(b"")
        return str(path)

    def trace_line(self, path):
        return ("\tlibheapline.so => %s (0x00007f3a1c2d4000)\n" % path).encode()

    def test_found(self):
        lib = self.lib()
        stdout = b"\tlinux-vdso.so.1 (0x00007ffd)\n" + self.trace_line(lib) + b"\tlibc.so.6 => /lib/libc.so.6 (0x00007f3a)\n"
        # ld.so's complaint about another preloaded library is not a miss.
        stderr = b"ERROR: ld.so: object 'other.so' from LD_PRELOAD cannot be preloaded: ignored.\n"
        self.assertEqual(self.find(stdout, stderr)[0], (lib, None))

    def test_path_with_spaces(self):
        lib = self.lib("my libs (0x1)")
        self.assertEqual(self.find(self.trace_line(lib))[0], (lib, None))

    def test_absolute_preload_line_is_not_the_answer(self):
        # ld.so prints LD_PRELOAD entries given by path without "=>".
        lib = self.lib()
        (path, error), _ = self.find(("\t%s (0x00007f3a)\n" % lib).encode())
        self.assertIsNone(path)
        self.assertEqual(error, "the dynamic linker did not report libheapline.so")

    def test_missing_library_quotes_ld_so(self):
        stderr = b"ERROR: ld.so: object 'libheapline.so' from LD_PRELOAD cannot be preloaded (cannot open shared object file): ignored.\n"
        (path, error), _ = self.find(b"\tlibc.so.6 => /lib/libc.so.6 (0x00007f3a)\n", stderr)
        self.assertIsNone(path)
        self.assertIn("cannot open shared object file", error)

    def test_silent_linker(self):
        # A static python: ld.so traces nothing and says nothing.
        self.assertEqual(self.find()[0], (None, "the dynamic linker did not report libheapline.so"))

    def test_rejects_what_is_not_an_absolute_file(self):
        for path in ("libheapline.so", "not found", str(self.dir / "absent.so"), str(self.dir)):
            with self.subTest(path=path):
                found, error = self.find(self.trace_line(path))[0]
                self.assertIsNone(found)
                self.assertIn("reported", error)

    def test_linker_cannot_run(self):
        for exc in (OSError("no such file"), subprocess.TimeoutExpired("python", 60)):
            with self.subTest(exc=exc):
                with mock.patch("heapline_gate.cli.subprocess.run", side_effect=exc):
                    path, error = find_lib({})
                self.assertIsNone(path)
                self.assertIn(str(exc), error)

    def test_trace_environment(self):
        env = {"LD_PRELOAD": "/opt/other.so", "LD_LIBRARY_PATH": "/opt/lib", "HEAPLINE_OUTPUT": "/tmp/x.%p.csv"}
        _, run = self.find(env=env)
        (argv,), kwargs = run.call_args
        self.assertEqual(argv, [sys.executable, "-c", ""])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(
            kwargs["env"],
            {"LD_TRACE_LOADED_OBJECTS": "1", "LD_PRELOAD": "libheapline.so:/opt/other.so", "LD_LIBRARY_PATH": "/opt/lib"},
        )
        self.assertIn("HEAPLINE_OUTPUT", env, "the caller's dict must not change")
        _, run = self.find()
        self.assertEqual(run.call_args[1]["env"]["LD_PRELOAD"], "libheapline.so")


# Skipped only when HEAPLINE_LIB is unset: a wrong path must fail, not skip.
@unittest.skipUnless(
    sys.platform.startswith("linux") and "HEAPLINE_LIB" in os.environ,
    "set HEAPLINE_LIB to a built libheapline.so (Linux only)",
)
class ContractTest(TmpDirTestCase):
    """Runs the real library and reads its real record: guards the format."""

    def run_app(self, records="sub"):
        code, _, _ = run_main(
            "run", "--lib", os.environ["HEAPLINE_LIB"], "--records", str(self.records / records),
            "--name", "alloc", "--", sys.executable, "-c", "x = bytes(20_000_000)",
        )
        self.assertEqual(code, 0)

    def test_same_program_within_its_baseline(self):
        self.run_app("base")
        self.run_app()
        code, out, err = run_main(
            "check", "--budgets", self.budgets("name,max_peak\nalloc,+5%\n"), "--records", str(self.records / "sub"),
            "--baseline", str(self.records / "base"),
        )
        self.assertEqual((code, err), (0, ""), out)

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
