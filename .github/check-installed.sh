#!/bin/sh
# Checks heapline-gate against the heapline package installed on this machine,
# then removes the package and checks that run refuses to start without it.
# Run from the root of the repository.
# Usage: .github/check-installed.sh PYTHON REMOVE-COMMAND...
set -eu
py=$1
shift
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "== tests, contract tests on the installed library"
HEAPLINE_INSTALLED=1 "$py" -m unittest discover -s tests -v

# Not skippable, unlike the tests: run finds the library itself, and check
# sees the 20 MB that heapline measured.
echo "== run without --lib, then check: 512MiB must pass, 1MiB must fail"
"$py" -m heapline_gate run --records "$tmp/r" --name app -- "$py" -c "x = bytes(20_000_000)"
printf 'name,max_peak\napp,512MiB\n' > "$tmp/loose.csv"
printf 'name,max_peak\napp,1MiB\n' > "$tmp/tight.csv"
# set -e would stop at the first non-zero code: collect them instead.
rc=0
"$py" -m heapline_gate check --budgets "$tmp/loose.csv" --records "$tmp/r" || rc=$?
if [ "$rc" != 0 ]; then
    echo "FAIL: check under 512MiB exited $rc, expected 0"
    exit 1
fi
rc=0
"$py" -m heapline_gate check --budgets "$tmp/tight.csv" --records "$tmp/r" || rc=$?
if [ "$rc" != 1 ]; then
    echo "FAIL: check under 1MiB exited $rc, expected 1"
    exit 1
fi

echo "== package removed: run without --lib must exit 2 before running the command"
"$@"
rc=0
"$py" -m heapline_gate run --records "$tmp/after" --name app -- touch "$tmp/ran" 2> "$tmp/err" || rc=$?
cat "$tmp/err"
if [ -e "$tmp/ran" ]; then
    echo "FAIL: the command ran"
    exit 1
fi
if [ "$rc" != 2 ]; then
    echo "FAIL: run exited $rc, expected 2"
    exit 1
fi
# argparse exits 2 too: insist on the message of run.
if ! grep -q "libheapline.so" "$tmp/err"; then
    echo "FAIL: the message does not name libheapline.so"
    exit 1
fi
echo "== ok"
