"""heapline-gate: run a command under heapline and check its heap against budgets."""

import argparse
import csv
import os
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from .records import read_records

UNITS = {"B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40}


def parse_size(text):
    """'1048576', '512MiB' or '1.5GiB' to a number of bytes."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(B|KiB|MiB|GiB|TiB)?\s*", text)
    if not m:
        raise ValueError("bad size %r (use bytes, KiB, MiB, GiB or TiB)" % text)
    # Fraction, not float: exact, and no OverflowError on a huge number.
    return int(Fraction(m.group(1)) * UNITS[m.group(2) or "B"])


def parse_peak(text):
    """A size, or '+5%' over the baseline: returns (bytes, None) or (None, percent)."""
    m = re.fullmatch(r"\s*\+(\d+(?:\.\d+)?)%\s*", text)
    if m:
        # Fraction keeps the limit exact: 1000 +12.5% is 1125, not 1124.
        return None, Fraction(m.group(1))
    return parse_size(text), None


def read_budgets(path):
    """Read name,max_peak[,max_live] rows; lines starting with # are comments.

    Returns (name, max_peak bytes or None, percent over the baseline or None,
    max_live bytes or None) tuples.
    """
    # utf-8-sig: spreadsheets on Windows save a byte order mark.
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = [line for line in f if not line.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    if not reader.fieldnames or not {"name", "max_peak"} <= set(reader.fieldnames):
        raise ValueError("%s: the header must contain name and max_peak" % path)
    budgets = []
    for row in reader:
        name = row["name"].strip()
        if not name:
            raise ValueError("%s: a row has no name" % path)
        if name in (b[0] for b in budgets):
            raise ValueError("%s: %s has two budgets" % (path, name))
        max_peak, percent = parse_peak(row["max_peak"] or "")
        live = (row.get("max_live") or "").strip()
        budgets.append((name, max_peak, percent, parse_size(live) if live else None))
    # With no budget, every record would go unchecked and the gate pass.
    if not budgets:
        raise ValueError("%s: no budget" % path)
    return budgets


def record_name(name):
    """The file names run gives the records of NAME: NAME.PID.csv."""
    return re.compile(re.escape(name) + r"\.\d+\.csv")


def size(n):
    """1536 to '1.5 KiB': the largest unit that keeps the number at 1 or more."""
    for unit in ("TiB", "GiB", "MiB", "KiB"):
        if n >= UNITS[unit]:
            return "%.1f %s" % (n / UNITS[unit], unit)
    return "%d B" % n


def cmd_check(args):
    try:
        budgets = read_budgets(args.budgets)
    except (OSError, ValueError) as e:
        print("heapline-gate: %s" % e, file=sys.stderr)
        return 2
    relative = [name for name, _, percent, _ in budgets if percent is not None]
    if relative and not args.baseline:
        print("heapline-gate: relative budgets need --baseline: %s" % ", ".join(relative), file=sys.stderr)
        return 2
    for directory in (args.records, args.baseline):
        if directory is not None and not Path(directory).is_dir():
            print("heapline-gate: %s is not a directory" % directory, file=sys.stderr)
            return 2
    records, errors = read_records(args.records)
    for e in errors:
        print("heapline-gate: skipped %s" % e, file=sys.stderr)
    baseline = []
    if args.baseline:
        baseline, errors = read_records(args.baseline)
        for e in errors:
            print("heapline-gate: skipped baseline %s" % e, file=sys.stderr)

    # Anything else in the directory is not ours.
    def records_of(name, among):
        pattern = record_name(name)
        return [r for r in among if pattern.fullmatch(r.path.name)]

    claimed = set()
    failed = missing = False
    checked = 0
    # With --allow-missing, a budget without a record or baseline is only noted.
    note = " (allowed)" if args.allow_missing else ""
    for name, max_peak, percent, max_live in budgets:
        mine = records_of(name, records)
        claimed.update(r.path for r in mine)
        if not mine:
            print("%-30s NO RECORD%s" % (name, note))
            missing = missing or not args.allow_missing
            continue
        relative_to = ""
        if percent is not None:
            theirs = records_of(name, baseline)
            if not theirs:
                print("%-30s NO BASELINE%s" % (name, note))
                missing = missing or not args.allow_missing
                continue
            base_peak = max(r.peak_heap_bytes for r in theirs)
            max_peak = int(base_peak * (100 + percent) / 100)
            relative_to = " (+%g%% of %s)" % (percent, size(base_peak))
        peak = max(r.peak_heap_bytes for r in mine)
        live = max(r.live_heap_at_exit_bytes for r in mine)
        # The excess, not both sizes: rounded, they may read the same.
        over = []
        if peak > max_peak:
            over.append("peak over by %s" % size(peak - max_peak))
        if max_live is not None and live > max_live:
            over.append("live at exit over by %s" % size(live - max_live))
        verdict = "FAIL: " + "; ".join(over) if over else "ok"
        print("%-30s peak %-10s budget %-10s %s%s" % (name, size(peak), size(max_peak), verdict, relative_to))
        failed = failed or bool(over)
        checked += 1

    unbudgeted = sorted(r.path.name for r in records if r.path not in claimed)
    if unbudgeted:
        print("heapline-gate: records without a budget: %s" % ", ".join(unbudgeted), file=sys.stderr)
    # A gate that saw nothing must not pass, even if every miss is allowed.
    if missing or not checked:
        return 3
    return 1 if failed else 0


def find_lib(env):
    """Ask the dynamic linker which libheapline.so a command run with ENV would preload.

    Returns (absolute path, None), or (None, why not).
    """
    env = dict(env)
    # ld.so lists the libraries it would load, in its own search order
    # (LD_LIBRARY_PATH, its cache, ...), and runs nothing.
    env["LD_TRACE_LOADED_OBJECTS"] = "1"
    env["LD_PRELOAD"] = ":".join(["libheapline.so"] + ([env["LD_PRELOAD"]] if env.get("LD_PRELOAD") else []))
    # Should heapline ever run here, it must not leave a record where check looks.
    env.pop("HEAPLINE_OUTPUT", None)
    try:
        trace = subprocess.run(
            [sys.executable, "-c", ""], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "cannot ask the dynamic linker: %s" % e
    # Only this line counts: ld.so exits 0 either way, and its stderr may
    # complain about other preloaded libraries even when it found ours.
    for line in os.fsdecode(trace.stdout).splitlines():
        line = line.lstrip()
        if line.startswith("libheapline.so => "):
            # "libheapline.so => /path/libheapline.so (0x...)"; the path may hold spaces.
            path = line[len("libheapline.so => "):].rpartition(" (0x")[0]
            if os.path.isabs(path) and os.path.isfile(path):
                return path, None
            return None, "the dynamic linker reported %r" % line
    # A static python makes ld.so print nothing at all.
    return None, os.fsdecode(trace.stderr).strip() or "the dynamic linker did not report libheapline.so"


def cmd_run(args):
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        print("heapline-gate: no command given", file=sys.stderr)
        return 2
    lib = args.lib
    if lib is None:
        lib, error = find_lib(os.environ)
        if lib is None:
            print(
                "heapline-gate: cannot find libheapline.so (%s); install the heapline package or pass --lib" % error,
                file=sys.stderr,
            )
            return 2
        print("heapline-gate: using %s" % lib, file=sys.stderr)
    # Absolute, so that a program that changes directory still loads the
    # library and writes its record where check looks.
    lib = Path(lib).resolve()
    records = Path(args.records).resolve()
    if not lib.is_file():
        print("heapline-gate: %s is not a file" % lib, file=sys.stderr)
        return 2
    # ld.so splits LD_PRELOAD on spaces and colons: the child would run
    # without heapline. Checked after resolve, which follows symlinks.
    # (A Windows drive letter only matters to the tests run there.)
    if any(c in str(lib)[len(lib.drive):] for c in " :"):
        print(
            "heapline-gate: %s holds a space or a colon, which LD_PRELOAD cannot carry; copy the library elsewhere" % lib,
            file=sys.stderr,
        )
        return 2
    name =args.name or os.path.basename(command[0])
    # heapline expands % sequences and does not create directories, so such
    # a name would never give a record that check finds.
    if not re.fullmatch(r"[\w.+-]+", name):
        print("heapline-gate: bad name %r (use letters, digits, '_', '.', '+' or '-')" % name, file=sys.stderr)
        return 2
    # check takes the highest peak of a name: an old record would stand in
    # for a run that writes none, a crash for instance, and let it pass.
    pattern = record_name(name)
    try:
        # heapline does not create the directory.
        records.mkdir(parents=True, exist_ok=True)
        old = sorted(p.name for p in records.iterdir() if pattern.fullmatch(p.name))
    except OSError as e:
        print("heapline-gate: cannot use %s: %s" % (records, e), file=sys.stderr)
        return 2
    if old:
        print(
            "heapline-gate: %s already holds records of %s (%s); remove them or use another --records directory"
            % (records, name, ", ".join(old)),
            file=sys.stderr,
        )
        return 2

    # Only the child sees these variables, never this process or its caller.
    env = dict(os.environ)
    env["LD_PRELOAD"] = ":".join([str(lib)] + ([env["LD_PRELOAD"]] if env.get("LD_PRELOAD") else []))
    env["HEAPLINE_OUTPUT"] = str(records / (name + ".%p.csv"))
    try:
        rc = subprocess.run(command, env=env).returncode
    except OSError as e:
        print("heapline-gate: cannot run %s: %s" % (command[0], e), file=sys.stderr)
        return 127
    return rc if rc >= 0 else 128 - rc


def main(argv=None):
    parser = argparse.ArgumentParser(prog="heapline-gate", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a command with heapline preloaded")
    run.add_argument(
        "--lib", help="path to libheapline.so (default: the one the dynamic linker finds, as the heapline package installs it)"
    )
    run.add_argument("--records", required=True, help="directory for the records")
    run.add_argument("--name", help="record name (default: the command's file name)")
    run.add_argument("command", nargs=argparse.REMAINDER, help="-- COMMAND [ARGS...]")
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", help="compare records with a budget file")
    check.add_argument("--budgets", required=True, help="CSV: name,max_peak[,max_live]")
    check.add_argument("--records", required=True, help="directory written by run")
    check.add_argument("--baseline", help="directory of records that +N%% budgets are relative to")
    check.add_argument(
        "--allow-missing",
        action="store_true",
        help="do not fail on a budget without a record or a baseline, as long as one budget is checked",
    )
    check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)
