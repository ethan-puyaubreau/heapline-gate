# heapline-gate

Fail a build when the heap peak of a program, measured by
[heapline](https://github.com/ethan-puyaubreau/heapline), exceeds a budget.

heapline-gate only reads the records heapline writes (format version 1) and
has no dependency beyond the Python 3.8+ standard library. Records of any other
version are refused. It does not change heapline.

## Install

```
pipx install git+https://github.com/ethan-puyaubreau/heapline-gate@v0.1.0
```

or `pip install .` from a clone. The tag fixes the version; without it you
get the head of `main`. heapline itself comes from its
[release page](https://github.com/ethan-puyaubreau/heapline/releases) or is
built from source; heapline-gate needs only the path to `libheapline.so`.

## Use

Run each program under heapline, then check the records against a budget file:

```
heapline-gate run --lib build/libheapline.so --records records --name parser -- ./build/parser_test
heapline-gate run --lib build/libheapline.so --records records --name server -- ./build/server_test
heapline-gate check --budgets budgets.csv --records records
```

`budgets.csv`:

```
name,max_peak,max_live
parser,64MiB,
server,512MiB,1MiB
```

The first line that is not a comment (`#`) is the header; `max_live` may be
left out of it. Sizes are bytes or use `B`, `KiB`, `MiB`, `GiB`, `TiB`.
`max_live` is optional and limits the heap still in use at exit; it hints at
a leak but proves nothing, since a program may keep data until it exits on
purpose.

`check` prints one line per budget:

```
parser                         peak 41.7 MiB   budget 64.0 MiB   ok
server                         peak 318.2 MiB  budget 512.0 MiB  FAIL: live at exit over by 2.4 MiB
```

`run` preloads heapline into the command only, never into your shell, and
writes `records/NAME.PID.csv`. If the command starts other programs they are
preloaded too and each writes its own record; `check` takes the highest peak
of all the records of a name. `check` reads only files named that way.

`NAME` defaults to the file name of the command and may hold letters, digits,
`_`, `.`, `+` and `-`. `run` refuses to start when the directory already holds
records of that name: an old record would stand in for a run that writes
none, and let it pass. To measure again, remove the directory or use another.

| Exit code | Meaning |
|---|---|
| 0 | every budget is respected |
| 1 | a budget is exceeded |
| 2 | nothing could be checked: no valid record, a budget without a record or without a baseline, or a bad budget file |

A gate that saw nothing never passes. Records that are skipped (unknown
version, truncated, a negative heap) are named on stderr, as are records
without a budget.

`run` returns the exit code of the command, 128 + N when a signal N ended
it, 127 when it cannot be started, and 2 when `run` itself refuses to start
it (no library, a bad name, old records).

## Budgets relative to a baseline

A fixed size drifts with the machine and the C library, and stays loose when
a program gets smaller. `max_peak` can instead be a margin over the peak of
the same name in a baseline, which is a directory of records like the one
`run` writes:

```
name,max_peak,max_live
parser,+5%,
server,512MiB,1MiB
```

```
heapline-gate check --budgets budgets.csv --records records --baseline base-records
```

A relative budget without `--baseline`, or whose name has no record in the
baseline (`NO BASELINE`), gives exit code 2. `max_live` stays a size.

The baseline is best measured in the same job, from the branch the change
goes into: both runs then share the machine and the C library. A baseline
committed to the repository works too; to update it, replace the whole
directory, since old records of a name still count.

## In GitHub Actions

On a pull request, measure the target branch, then the change:

```yaml
on:
  pull_request:

jobs:
  heap:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install heapline and heapline-gate
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release download --repo ethan-puyaubreau/heapline --pattern '*-linux-x86_64.tar.gz*'
          sha256sum -c heapline-v*-linux-x86_64.tar.gz.sha256
          tar xzf heapline-v*-linux-x86_64.tar.gz
          echo "HEAPLINE_LIB=$(echo "$PWD"/heapline-v*-linux-x86_64/lib/libheapline.so)" >> "$GITHUB_ENV"
          pipx install git+https://github.com/ethan-puyaubreau/heapline-gate@v0.1.0

      - name: Measure the target branch
        run: |
          git worktree add --detach base "origin/$GITHUB_BASE_REF"
          cd base
          ./build.sh  # your build
          heapline-gate run --lib "$HEAPLINE_LIB" --records ../base-records --name parser -- ./build/parser_test

      - name: Measure this change and check
        run: |
          ./build.sh
          heapline-gate run --lib "$HEAPLINE_LIB" --records records --name parser -- ./build/parser_test
          heapline-gate check --budgets budgets.csv --records records --baseline base-records
```

## Test

```
python -m unittest discover -s tests
HEAPLINE_LIB=/path/to/libheapline.so python -m unittest discover -s tests -v
```

With `HEAPLINE_LIB` set on Linux, the tests also run the real library and read
its real record, so a change in heapline's format fails here; a path that is
not a library fails them too, rather than skipping them. CI runs them against
the latest release of heapline and against its main branch, every week as
well as on each change.

## Limits

- Statically linked programs and programs using another allocator (jemalloc,
  tcmalloc) are not seen by heapline.
- The peak is exact for a single-threaded program; with several threads it is
  within a small fraction of a percent (see heapline's documentation), so keep
  a margin in the budget.
- A process killed by a signal writes no record, so its budget reports
  `NO RECORD` (exit code 2), not a pass.

## License

Apache-2.0. See [LICENSE](LICENSE).
