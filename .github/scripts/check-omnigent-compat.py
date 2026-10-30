#!/usr/bin/env python3
"""Does the plugin's compatibility layer still fit an Omnigent SDK release?

hermes_omnigent/patches.py rewrites six files of the Omnigent SDK that the plugin installs for itself. It matches exact
text anchors, and a single miss stops the install with OMNIGENT_COMPAT_ANCHOR_MISSING. This runs the same dry run against
any release, without installing it and without touching it:

    python .github/scripts/check-omnigent-compat.py --wheel omnigent-0.20.0-py3-none-any.whl
    python .github/scripts/check-omnigent-compat.py --sdk-dir .../site-packages/omnigent --label 0.19.1

In this order: every anchor matches (make_edits), every patched file compiles, and patching the result again changes
nothing. The Omnigent SDK itself is not needed to run this; the plugin's patch code imports without it.

The last line on stdout is one JSON object: {"label", "ok", "code", "file", "line"}. Exit status: 0 the layer fits,
1 it does not (code says how; file is the SDK file being patched; line is in patches.py), 2 a usage error or
Python older than 3.12 (Omnigent's own files use 3.12 syntax).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from hermes_omnigent import patches  # noqa: E402
from hermes_omnigent.runtime import Failure  # noqa: E402


def relative(path, sdk):
    try:
        return Path(path).relative_to(sdk).as_posix()
    except (TypeError, ValueError):
        return Path(path).name if path else None


def where(error):
    """(SDK file being patched, line in patches.py) for a Failure raised inside make_edits."""
    file = line = None
    trace = error.__traceback__
    while trace is not None:
        code = trace.tb_frame.f_code
        if Path(code.co_filename).name == 'patches.py':
            line = trace.tb_lineno
            if code.co_name == 'change':
                file = trace.tb_frame.f_locals.get('relative')
        trace = trace.tb_next
    return file, line


def check(sdk):
    """(code, file, line) of the first problem, or None when the layer fits this SDK."""
    try:
        edits = patches.make_edits(sdk)
    except Failure as error:
        return (error.code, *where(error))
    except FileNotFoundError as error:
        return 'FILE_MISSING', relative(error.filename, sdk), None
    except SyntaxError as error:
        return 'PATCHED_SOURCE_DOES_NOT_COMPILE', relative(error.filename, sdk), error.lineno
    for file, _before, after in edits:
        try:
            compile(after, str(file), 'exec')
        except SyntaxError as error:
            return 'PATCHED_SOURCE_DOES_NOT_COMPILE', relative(file, sdk), error.lineno
    with tempfile.TemporaryDirectory() as scratch:
        for file, _before, after in edits:
            target = Path(scratch) / Path(file).relative_to(sdk)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(after)
        try:
            again = patches.make_edits(scratch)
        except Failure as error:
            return 'NOT_IDEMPOTENT', None, where(error)[1]
        for file, before, after in again:
            if before != after:
                return 'NOT_IDEMPOTENT', relative(file, scratch), None
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--wheel', help='an omnigent wheel (unpacked into a private temporary directory)')
    source.add_argument('--sdk-dir', help='an unpacked or installed omnigent package directory (the one holding host/, runner/, ...)')
    parser.add_argument('--label', help='name for the report (default: the version in the wheel file name)')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 12):
        # Omnigent releases use Python 3.12 syntax (for example `def f[T](...)`). An older interpreter reports
        # PATCHED_SOURCE_DOES_NOT_COMPILE for a release that compiles fine where it is meant to run.
        print(f'Python {sys.version_info.major}.{sys.version_info.minor} cannot read every Omnigent release; run this with 3.12 or newer', file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as scratch:
        label = args.label
        if args.wheel:
            wheel = Path(args.wheel)
            if not wheel.is_file():
                print(f'{wheel} is not a file', file=sys.stderr)
                return 2
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(Path(scratch) / 'wheel')
            sdk = Path(scratch) / 'wheel' / 'omnigent'
            named = re.match(r'omnigent-([^-]+)-', wheel.name)
            label = label or (named.group(1) if named else wheel.name)
        else:
            sdk = Path(args.sdk_dir)
            label = label or sdk.name
        if not (sdk / 'host').is_dir():
            print(f'{sdk} does not look like the omnigent package (no host/ directory)', file=sys.stderr)
            return 2
        problem = check(sdk)
    code, file, line = problem or (None, None, None)
    if problem:
        print(f'FAIL Omnigent {label}: {code}' + (f' while patching {file}' if file else '') + (f' (patches.py:{line})' if line else ''))
    else:
        print(f'ok   Omnigent {label}: all anchors match, the patched files compile, patching again changes nothing')
    print(json.dumps({'label': label, 'ok': problem is None, 'code': code, 'file': file, 'line': line}))
    return 0 if problem is None else 1


if __name__ == '__main__':
    sys.exit(main())
