"""heapline-gate: run a command under heapline and check its heap against budgets."""

import argparse
import csv
import os
import re
import subprocess
import sys
from pathlib import Path

from .records import read_records

UNITS = {"B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40}


def parse_size(text):
    """'1048576', '512MiB' or '1.5GiB' to a number of bytes."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(B|KiB|MiB|GiB|TiB)?\s*", text)
    if not m:
        raise ValueError("bad size %r (use bytes, KiB, MiB, GiB or TiB)" % text)
    return int(float(m.group(1)) * UNITS[m.group(2) or "B"])


def read_budgets(path):
    """Read name,max_peak[,max_live] rows; lines starting with # are comments."""
    with open(path, newline="", encoding="utf-8") as f:
        lines = [line for line in f if not line.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    if not reader.fieldnames or not {"name", "max_peak"} <= set(reader.fieldnames):
        raise ValueError("%s: the header must contain name and max_peak" % path)
    budgets = []
    for row in reader:
        live = (row.get("max_live") or "").strip()
        budgets.append(
            (row["name"].strip(), parse_size(row["max_peak"]), parse_size(live) if live else None)
        )
    return budgets


def mib(n):
    return "%.1f MiB" % (n / 2**20)


def cmd_check(args):
    try:
        budgets = read_budgets(args.budgets)
    except (OSError, ValueError) as e:
        print("heapline-gate: %s" % e, file=sys.stderr)
        return 2
    records, errors = read_records(args.records)
    for e in errors:
        print("heapline-gate: skipped %s" % e, file=sys.stderr)

    # run writes NAME.PID.csv; anything else in the directory is not ours.
    claimed = set()
    failed = missing = False
    for name, max_peak, max_live in budgets:
        pattern = re.compile(re.escape(name) + r"\.\d+\.csv")
        mine = [r for r in records if pattern.fullmatch(r.path.name)]
        claimed.update(r.path for r in mine)
        if not mine:
            print("%-30s NO RECORD" % name)
            missing = True
            continue
        peak = max(r.peak_heap_bytes for r in mine)
        live = max(r.live_heap_at_exit_bytes for r in mine)
        over = []
        if peak > max_peak:
            over.append("peak %s > %s" % (mib(peak), mib(max_peak)))
        if max_live is not None and live > max_live:
            over.append("live at exit %s > %s" % (mib(live), mib(max_live)))
        print("%-30s peak %-12s budget %-12s %s" % (name, mib(peak), mib(max_peak), "FAIL: " + "; ".join(over) if over else "ok"))
        failed = failed or bool(over)

    unbudgeted = sorted(r.path.name for r in records if r.path not in claimed)
    if unbudgeted:
        print("heapline-gate: records without a budget: %s" % ", ".join(unbudgeted), file=sys.stderr)
    # A gate that saw nothing must not pass.
    if missing or not records:
        return 2
    return 1 if failed else 0


def cmd_run(args):
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        print("heapline-gate: no command given", file=sys.stderr)
        return 2
    lib = Path(args.lib)
    if not lib.is_file():
        print("heapline-gate: %s is not a file" % lib, file=sys.stderr)
        return 2
    name = args.name or os.path.basename(command[0])
    # heapline does not create the directory.
    Path(args.records).mkdir(parents=True, exist_ok=True)

    # Only the child sees these variables, never this process or its caller.
    env = dict(os.environ)
    env["LD_PRELOAD"] = ":".join([str(lib)] + ([env["LD_PRELOAD"]] if env.get("LD_PRELOAD") else []))
    env["HEAPLINE_OUTPUT"] = str(Path(args.records) / (name + ".%p.csv"))
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
    run.add_argument("--lib", required=True, help="path to libheapline.so")
    run.add_argument("--records", required=True, help="directory for the records")
    run.add_argument("--name", help="record name (default: the command's file name)")
    run.add_argument("command", nargs=argparse.REMAINDER, help="-- COMMAND [ARGS...]")
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", help="compare records with a budget file")
    check.add_argument("--budgets", required=True, help="CSV: name,max_peak[,max_live]")
    check.add_argument("--records", required=True, help="directory written by run")
    check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
