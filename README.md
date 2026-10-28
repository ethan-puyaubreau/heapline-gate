# heapline-gate

Fail a build when the heap peak of a program, measured by
[heapline](https://github.com/ethan-puyaubreau/heapline), exceeds a budget.

heapline-gate only reads the records heapline writes (format version 1) and
has no dependency beyond the Python 3.8+ standard library. Records of any other
version are refused. It does not change heapline.

## Install

heapline comes as a package from its
[release page](https://github.com/ethan-puyaubreau/heapline/releases), for
x86_64 and aarch64. On Debian or Ubuntu:

```
arch=$(dpkg --print-architecture)
gh release download --repo ethan-puyaubreau/heapline --pattern "heapline_*_${arch}.deb*"
sha256sum -c heapline_*_${arch}.deb.sha256
sudo apt-get install -y ./heapline_*_${arch}.deb
```

On RHEL, Rocky or Alma 8 and 9, the `.el8` package fits both:

```
gh release download --repo ethan-puyaubreau/heapline --pattern "heapline-*.el8.$(uname -m).rpm*"
sha256sum -c heapline-*.el8.$(uname -m).rpm.sha256
sudo dnf install -y ./heapline-*.el8.$(uname -m).rpm
```

Outside a GitHub runner, these blocks need the GitHub CLI `gh`, logged in
with `gh auth login`: `gh release download` refuses to run without a login,
even on this public repository. RHEL, Rocky and Alma do not ship `gh`; add
GitHub's own repository from <https://cli.github.com>, or download the two
files from the release page by hand and go on from `sha256sum -c`.

Elsewhere, or without root, use the release archive or build heapline from
source, and give `run` the path of `libheapline.so` with `--lib`.

Then heapline-gate itself:

```
pipx install git+https://github.com/ethan-puyaubreau/heapline-gate@v0.2.0
```

Outside a GitHub runner, install `pipx` and `git` first and run
`pipx ensurepath`. The `pipx` of Debian 12 and Ubuntu 22.04 or later takes
this line; Ubuntu 20.04's is too old for it and Debian 11 has none, so use
pip there: `sudo apt-get install -y python3-pip git` then
`python3 -m pip install --user git+https://github.com/ethan-puyaubreau/heapline-gate@v0.2.0`.
That puts `heapline-gate` in `~/.local/bin`, which is on `PATH` only in a
login shell started after the directory exists; run
`export PATH="$HOME/.local/bin:$PATH"` or log in again.
RHEL 8 and 9 have no `pipx` outside EPEL, so use pip
there too. On RHEL 9, use `sudo dnf install -y python3-pip git` then
`python3 -m pip install --user git+https://github.com/ethan-puyaubreau/heapline-gate@v0.2.0`.
On RHEL 8, whose `python3` is 3.6, use
`sudo dnf install -y python3.11-pip git` then
`python3.11 -m pip install --user git+https://github.com/ethan-puyaubreau/heapline-gate@v0.2.0`.
The tag fixes the version; without it you get the head of `main`.

## Use

Run each program under heapline, then check the records against a budget file:

```
heapline-gate run --records records --name parser -- ./build/parser_test
heapline-gate run --records records --name server -- ./build/server_test
heapline-gate check --budgets budgets.csv --records records
```

Without `--lib`, `run` asks the dynamic linker which `libheapline.so` it
would preload, the one the package installed, and prints its path. When the
linker finds none, `run` exits with code 2 before starting the command.
`--lib PATH` uses another copy, such as a build or the release archive.

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

`--format json` prints the same results as one JSON object (one entry per
budget with its status, sizes and excess in bytes, the exit code, and the
records without a budget), for scripts. `--format markdown` prints a table;
in GitHub Actions, append it to the job summary:

```
heapline-gate check --budgets budgets.csv --records records --format markdown >> "$GITHUB_STEP_SUMMARY"
```

Messages about skipped records stay on stderr in every format, and the exit
codes do not depend on the format.

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
it (no `libheapline.so` found, a bad name, old records).

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

On a pull request, measure the target branch, then the change. The package
is chosen by architecture, so the same job runs on `ubuntu-24.04-arm`:

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
          arch=$(dpkg --print-architecture)
          gh release download --repo ethan-puyaubreau/heapline --pattern "heapline_*_${arch}.deb*"
          sha256sum -c heapline_*_${arch}.deb.sha256
          sudo apt-get install -y ./heapline_*_${arch}.deb
          pipx install git+https://github.com/ethan-puyaubreau/heapline-gate@v0.2.0

      - name: Measure the target branch
        run: |
          git worktree add --detach base "origin/$GITHUB_BASE_REF"
          cd base
          ./build.sh  # your build
          heapline-gate run --records ../base-records --name parser -- ./build/parser_test

      - name: Measure this change and check
        run: |
          ./build.sh
          heapline-gate run --records records --name parser -- ./build/parser_test
          heapline-gate check --budgets budgets.csv --records records --baseline base-records
```

## Test

```
python -m unittest discover -s tests
HEAPLINE_LIB=/path/to/libheapline.so python3 -m unittest discover -s tests -v
HEAPLINE_INSTALLED=1 python3 -m unittest discover -s tests -v
```

With `HEAPLINE_LIB` set on Linux, the tests also run the real library and read
its real record, so a change in heapline's format fails here; a path that is
not a library fails them too, rather than skipping them. `HEAPLINE_INSTALLED=1`
runs them without `--lib`, against the installed package; on RHEL 8, use
`python3.11` (from `sudo dnf install -y python3.11`), as its `python3` is 3.6. CI runs them
against the latest release of heapline, its main branch, and its Debian and
RPM packages on x86_64 and aarch64, every week as well as on each change.

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
