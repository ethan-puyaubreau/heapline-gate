"""Reader for heapline records, format versions 1 and 2.

The format is described in heapline's docs/format.md: a comment line naming
the version, a header line and one line of values. Version 2 adds the
complete column. Any other version is refused, as that page asks of tools.
"""

from pathlib import Path
from typing import List, NamedTuple, Tuple

MAGIC = "# heapline 2"
MAGIC_V1 = "# heapline 1"
COLUMNS_V1 = [
    "rank",
    "host",
    "pid",
    "job",
    "elapsed_s",
    "allocs",
    "frees",
    "peak_heap_bytes",
    "peak_heap_time_s",
    "live_heap_at_exit_bytes",
    "vmhwm_bytes",
    "vmrss_at_exit_bytes",
]
COLUMNS = COLUMNS_V1 + ["complete"]


class Record(NamedTuple):
    path: Path
    peak_heap_bytes: int
    live_heap_at_exit_bytes: int


def read_record(path) -> Record:
    """Parse one record file. Raises ValueError if it is not a valid one."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("# heapline"):
        raise ValueError("not a heapline record")
    magic = lines[0].strip()
    if magic not in (MAGIC, MAGIC_V1):
        raise ValueError("unsupported format version: %r" % magic)
    columns = COLUMNS if magic == MAGIC else COLUMNS_V1
    if len(lines) < 3 or lines[1].strip().split(",") != columns:
        raise ValueError("unexpected columns or missing values line")
    values = lines[2].strip().split(",")
    if len(values) != len(columns):
        raise ValueError("values line does not have %d fields" % len(columns))
    row = dict(zip(columns, values))
    # A snapshot holds the peak of a process that did not exit, killed by the
    # OOM killer for instance: the run did not end, and its peak is a lower
    # bound, which must not pass a budget.
    complete = row.get("complete", "1")
    if complete not in ("0", "1"):
        raise ValueError("complete is neither 0 nor 1")
    if complete == "0":
        raise ValueError("snapshot of a process that did not exit (killed?)")
    try:
        peak = int(row["peak_heap_bytes"])
        live = int(row["live_heap_at_exit_bytes"])
    except ValueError:
        raise ValueError("peak or live heap is not an integer") from None
    # A negative heap would pass any budget.
    if peak < 0 or live < 0:
        raise ValueError("peak or live heap is negative")
    return Record(path, peak, live)


def read_records(directory) -> Tuple[List[Record], List[str]]:
    """Read every *.csv file of a directory.

    Returns the valid records and one message per file that was skipped.
    """
    records, errors = [], []
    for path in sorted(Path(directory).glob("*.csv")):
        try:
            records.append(read_record(path))
        except (OSError, ValueError) as e:
            errors.append("%s: %s" % (path.name, e))
    return records, errors
