#!/usr/bin/env python3
"""Expose approved SHA-256 downloads to --distdir without copying their bytes.

The source must be the node's read-only shared cache, never a PR's private cache.
Bazel verifies each candidate's digest before using it. Missing seeds fall through
to the registry; jobs never publish downloads to the shared cache.
"""
from pathlib import Path
import re
import sys


def prepare(cache, destination):
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in sorted((cache / 'content_addressable/sha256').glob('*')):
        if not re.fullmatch('[0-9a-f]{64}', entry.name) or not (entry / 'file').is_file():
            continue
        # rules_oci URLs end in sha256:<hex>; its output blob paths end in <hex>.
        # Bazel's distdir searches URL/output basenames, then checks the SHA-256.
        for name in (entry.name, 'sha256:' + entry.name):
            target = destination / name
            if target.is_symlink():
                target.unlink()
            target.symlink_to((entry / 'file').resolve())
        count += 1
    print(f'Shared download distdir: {count} shared SHA-256 downloads indexed (no payload copies)')


if __name__ == '__main__':
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
