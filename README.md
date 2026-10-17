# heapline-gate

Fail a build when the heap peak of a program, measured by
[heapline](https://github.com/ethan-puyaubreau/heapline), exceeds a budget.

heapline-gate only reads the records heapline writes (format version 1) and
has no dependency beyond the Python 3.8+ standard library. Records of any other
version are refused. It does not change heapline.

## Use

Run each program under heapline, then check the records against a budget file:

```
heapline-gate run --lib build/libheapline.so --records records --name parser -- ./build/parser_test
heapline-gate run --lib build/libheapline.so --records records --name server -- ./build/server_test
heapline-gate check --budgets budgets.csv --records records
```

`budgets.csv`:

```
# name,max_peak[,max_live]
parser,64MiB
server,512MiB,1MiB
```

Sizes are bytes or use `B`, `KiB`, `MiB`, `GiB`, `TiB`. `max_live` is optional
and limits the heap still in use at exit; it hints at a leak but proves
nothing, since a program may keep data until it exits on purpose.

`run` preloads heapline into the command only, never into your shell, and
writes `records/NAME.PID.csv`. If the command starts other programs they are
preloaded too and each writes its own record; `check` takes the highest peak
of all the records of a name. `check` reads only files named that way.

| Exit code | Meaning |
|---|---|
| 0 | every budget is respected |
| 1 | a budget is exceeded |
| 2 | nothing could be checked: no valid record, a budget without a record, or a bad budget file |

A gate that saw nothing never passes. Records that are skipped (unknown
version, truncated) are named on stderr, as are records without a budget.

`run` returns the exit code of the command.

## Test

```
python -m unittest discover -s tests
HEAPLINE_LIB=/path/to/libheapline.so python -m unittest discover -s tests -v
```

With `HEAPLINE_LIB` set on Linux, the tests also run the real library and read
its real record, so a change in heapline's format fails here.

## Limits

- Statically linked programs and programs using another allocator (jemalloc,
  tcmalloc) are not seen by heapline.
- The peak is exact for a single-threaded program; with several threads it is
  within a small fraction of a percent (see heapline's documentation), so keep
  a margin in the budget.
- A process killed by a signal writes no record, so its budget reports
  `NO RECORD` (exit code 2), not a pass.
