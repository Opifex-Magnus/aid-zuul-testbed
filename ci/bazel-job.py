#!/usr/bin/env python3
"""Run Bazel and export bounded reports; raw action diagnostics/OCI inputs never enter fetch-output."""
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys


class BudgetExceeded(Exception):
    pass


class LimitedWriter:
    def __init__(self, stream, limit):
        self.stream, self.limit, self.written = stream, limit, 0

    def write(self, data):
        if self.written + len(data) > self.limit:
            raise BudgetExceeded()
        self.written += len(data)
        return self.stream.write(data)

    def flush(self):
        self.stream.flush()


def collect(workspace, home, budget):
    total = budget["totalBytes"]
    reserve = budget["manifestBytes"]
    # The flake exporter writes a sibling of bazel/, fetched by the same executor.
    # Reserve its maximum size even when it has not been written yet: fetch order
    # must not determine whether the combined output fits the executor's budget.
    summary_reserve = budget["perFileBytes"]
    if not 1024 <= reserve < total or not 0 < budget["previewBytes"] <= budget["perFileBytes"] <= total - reserve - summary_reserve:
        raise ValueError("invalid Bazel fetch budget")
    destination = home / "zuul-output/logs/bazel"
    destination.mkdir(parents=True, exist_ok=False)
    used, omitted = 0, 0
    records = []

    def export(source, relative, compress=False):
        nonlocal used, omitted
        if len(records) >= budget["maxEntries"]:
            omitted += 1
            return
        size = source.stat().st_size
        record = {"source": str(relative)[:200], "originalBytes": size}
        limit = min(budget["perFileBytes"], total - reserve - summary_reserve - used)
        if limit <= 0:
            records.append(dict(record, omitted="total budget"))
            return
        # A fixed-depth tree also bounds directory overhead; the manifest retains the original target path.
        output = Path("tests") / (hashlib.sha256(str(relative).encode()).hexdigest() + "-" + source.name) if relative.parts[0] == "tests" else relative
        target = destination / output
        record["path"] = str(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if compress:
                if size > budget["compressInputBytes"]:
                    raise BudgetExceeded()
                with source.open("rb") as inp, target.open("wb") as out:
                    with gzip.GzipFile(filename="", fileobj=LimitedWriter(out, limit), mode="wb", mtime=0) as zipped:
                        shutil.copyfileobj(inp, zipped, 65536)
            else:
                if size > limit:
                    raise BudgetExceeded()
                with source.open("rb") as inp, target.open("wb") as out:
                    shutil.copyfileobj(inp, LimitedWriter(out, limit), 65536)
            used += target.stat().st_size
            record["fetchedBytes"] = target.stat().st_size
        except BudgetExceeded:
            target.unlink(missing_ok=True)
            # Keep an explicitly marked, bounded tail/head of text logs, never invalid truncated XML/protobuf.
            if source.name in ("test.log", "execution.json", "build-events.jsonl"):
                amount = min(budget["previewBytes"], limit)
                with source.open("rb") as inp:
                    head = inp.read(amount // 2)
                    inp.seek(max(0, size - amount // 2))
                    tail = inp.read(amount // 2)
                target = target.with_name(target.name + ".preview.txt")
                record["path"] = str(target.relative_to(destination))
                data = b"TRUNCATED diagnostic preview\n" + head + b"\n...\n" + tail
                data = data[:limit]
                target.write_bytes(data)
                used += len(data)
                record.update(fetchedBytes=len(data), preview=True)
            else:
                record["omitted"] = "file budget"
        records.append(record)

    test_root = workspace / "bazel-testlogs"
    if test_root.is_dir():
        # Resolve the root output symlink, but never descend into runfiles, undeclared outputs or directory links.
        root = test_root.resolve()
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name != "test.outputs" and not name.endswith(".runfiles") and not (Path(directory) / name).is_symlink())
            # Only the Node runner's JSON evidence enters fetch-output. Cores, arbitrary
            # undeclared payloads and symlinks remain excluded, under the existing budgets.
            crash = Path(directory) / "test.outputs" / "node-crash"
            outputs = Path(directory) / "test.outputs"
            if not outputs.is_symlink() and not crash.is_symlink() and crash.is_dir():
                for source in sorted(crash.iterdir()):
                    if not source.is_symlink() and source.is_file() and re.fullmatch(r"(?:before|after|report\.[\w.-]+)\.json", source.name):
                        export(source, Path("tests") / source.relative_to(root))
            for name in sorted(files):
                if name in ("test.xml", "test.log"):
                    source = Path(directory) / name
                    export(source, Path("tests") / source.relative_to(root))
    # Keep JSON support for older/ad-hoc callers of this general wrapper.
    for name in ("remote-rpc.pb", "execution.json", "build-events.jsonl"):
        source = home / "zuul-bazel-diagnostics" / name
        if source.is_file():
            export(source, Path("diagnostics") / (name + ".gz"), compress=True)
    # Bazel's compact execution log is already zstd-compressed. Keep it intact
    # within the same per-file/total budget; never publish a truncated binary.
    compact = home / "zuul-bazel-diagnostics/execution.log"
    if compact.is_file():
        export(compact, Path("diagnostics/execution.log"))
    report = {"budgetBytes": total, "summaryReservedBytes": summary_reserve, "fetchedBytes": used, "omittedEntries": omitted, "files": records}
    encoded = json.dumps(report, indent=2).encode()
    while len(encoded) > reserve and records:
        records.pop()
        report["omittedEntries"] += 1
        encoded = json.dumps(report, indent=2).encode()
    if len(encoded) > reserve:
        raise ValueError("Bazel manifest exceeds reserved budget")
    (destination / "manifest.json").write_bytes(encoded)
    print(f"Bazel fetched reports: {used + len(encoded)} bytes + {summary_reserve} bytes reserved for flake summary / {total} byte budget", flush=True)


if __name__ == "__main__":
    home = Path.home()
    (home / "zuul-bazel-diagnostics").mkdir(parents=True, exist_ok=True)
    result = 127
    try:
        result = subprocess.run(sys.argv[1:], check=False).returncode
    finally:
        collect(Path.cwd(), home, json.loads(Path(__file__).with_name("bazel-log-budget.json").read_text()))
    sys.exit(result if result >= 0 else 128 - result)
