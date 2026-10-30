import contextlib
import io

from heapline_gate.cli import main
from heapline_gate.records import COLUMNS, MAGIC


def record_text(peak=1000, live=10, magic=MAGIC, columns=COLUMNS, values=None):
    if values is None:
        values = ["0", "node-a", "1", "", "1.000", "5", "4", str(peak), "0.5", str(live), "9999", "8888", "1"]
    return "\n".join([magic, ",".join(columns), ",".join(values)]) + "\n"


def write_record(directory, filename, **kwargs):
    path = directory / filename
    path.write_text(record_text(**kwargs), encoding="utf-8")
    return path


def run_main(*argv):
    """Run the CLI; returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()
