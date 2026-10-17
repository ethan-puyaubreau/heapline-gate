"""Reader for heapline records, format version 1.

The format is described in heapline's docs/format.md: a comment line naming
the version, a header line and one line of values. Any other version is
refused, as that page asks of tools.
"""

from pathlib import Path
from typing import List, NamedTuple, Tuple

MAGIC = "# heapline 1"
COLUMNS = [
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
    if lines[0].strip() != MAGIC:
        raise ValueError("unsupported format version: %r" % lines[0].strip())
    if len(lines) < 3 or lines[1].strip().split(",") != COLUMNS:
        raise ValueError("unexpected columns or missing values line")
    values = lines[2].strip().split(",")
    if len(values) != len(COLUMNS):
        raise ValueError("values line does not have %d fields" % len(COLUMNS))
    row = dict(zip(COLUMNS, values))
    try:
        return Record(
            path,
            int(row["peak_heap_bytes"]),
            int(row["live_heap_at_exit_bytes"]),
        )
    except ValueError:
        raise ValueError("peak or live heap is not an integer") from None


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
