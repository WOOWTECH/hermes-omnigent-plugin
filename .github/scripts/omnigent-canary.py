#!/usr/bin/env python3
"""Weekly canary: does the plugin as shipped still work with the Omnigent releases published after its pin?

Two questions per release, for the pinned SDK (hermes_omnigent/runtime.py installs omnigent==X.Y.Z) and every stable
release newer than it (the newest few):

  server contract  tests/server-contract/run.py: the pinned SDK's adapter and managed host against a real local server of
                   that release. Red means the plugin as shipped stops working against servers of that release.
  anchors          .github/scripts/check-omnigent-compat.py: can patches.py still patch that release's SDK files. Red does
                   not affect the plugin while it stays on its pin; it is what an SDK upgrade has to fix first.

    omnigent-canary.py run --work DIR --out results.json    (the check job; exits 1 if a server contract is not green)
    omnigent-canary.py report                               (reads $RESULTS; keeps one standing issue up to date)

The issue is created on the first run and then only edited. It is commented on (and reopened if somebody closed it) when a
result changes or a new release appears, never otherwise. Nothing here changes the plugin's behaviour.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = 'https://pypi.org/pypi/omnigent/json'
TITLE = 'Omnigent compatibility canary'
LABEL = 'omnigent-compat'
VERSION = re.compile(r'\d+\.\d+\.\d+')
STATE = re.compile(r'<!-- omnigent-canary-state (\{.*?\}) -->')
STATUSES = ('pass', 'fail', 'error')
ICON = {'pass': '✅', 'fail': '❌', 'error': '⚠️'}


# --- pure parts (tests/test_canary.py) ---------------------------------------------------------------------

def pinned(runtime_source):
    """The Omnigent version the plugin installs for itself, read from the one place that installs it."""
    found = set(re.findall(r'omnigent==(\d+\.\d+\.\d+)', runtime_source))
    if len(found) != 1:
        raise SystemExit(f'expected exactly one omnigent==X.Y.Z pin in hermes_omnigent/runtime.py, found {sorted(found)}')
    return found.pop()


def key(version):
    return tuple(int(part) for part in version.split('.'))


def newer_releases(index, pin, limit):
    """The newest `limit` stable, not fully yanked releases above the pin, oldest first."""
    found = sorted((version for version, files in index.get('releases', {}).items()
                    if VERSION.fullmatch(version) and key(version) > key(pin) and files and not all(f.get('yanked') for f in files)), key=key)
    return found[-limit:] if limit > 0 else []


def last_json(text):
    """The last stdout line that is a JSON object (what run.py and check-omnigent-compat.py end with), else None."""
    for line in reversed(text.strip().splitlines()):
        if line.startswith('{'):
            try:
                value = json.loads(line)
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


def plain(text, limit=80):
    """Free text from a log or a package, reduced to something safe to put in a Markdown table."""
    return re.sub(r'[^A-Za-z0-9_./ :(),-]', '', str(text or ''))[:limit].strip()


def contract_result(code, output):
    """Outcome of one run.py run: 'pass', 'fail' (names the step) or 'error' (it could not run at all)."""
    summary = last_json(output)
    if code == 0 and summary and summary.get('ok') is True:
        return {'status': 'pass', 'step': None}
    if summary and summary.get('ok') is False:
        step = next((s.get('step') for s in summary.get('steps', []) if s.get('ok') is False), None)
        return {'status': 'fail', 'step': plain(step)}
    return {'status': 'error', 'step': f'run.py exit {code}'}


def anchors_result(code, output):
    summary = last_json(output)
    if summary is None or code not in (0, 1):
        return {'status': 'error', 'code': f'check exit {code}', 'file': None}
    if summary.get('ok') is True:
        return {'status': 'pass', 'code': None, 'file': None}
    return {'status': 'fail', 'code': plain(summary.get('code')), 'file': plain(summary.get('file'))}


def cell(result, detail):
    text = ICON[result['status']]
    return f'{text} {plain(detail)}' if result['status'] != 'pass' and plain(detail) else text


def table(results):
    rows = ['| Omnigent | Server contract | Compatibility-layer anchors |', '|---|---|---|']
    for row in results['rows']:
        name = f'`{row["version"]}`' + (' (pinned SDK)' if row['version'] == results['pin'] else '')
        rows.append(f'| {name} | {cell(row["contract"], row["contract"].get("step"))} | {cell(row["anchors"], row["anchors"].get("file"))} |')
    return '\n'.join(rows)


def state_of(results):
    return {'pin': results['pin'], 'rows': {row['version']: {'contract': row['contract']['status'], 'anchors': row['anchors']['status']}
                                            for row in results['rows']}}


def render(results, run_url, today):
    state = json.dumps(state_of(results), separators=(',', ':'))
    return '\n'.join([
        f'Weekly check of the plugin as shipped (Omnigent SDK `{results["pin"]}`, the version `hermes_omnigent/runtime.py` installs) '
        'against that release and the stable releases published after it.',
        '',
        'Maintained by `.github/workflows/omnigent-canary.yml`: edited every run, commented on (and reopened) only when a result '
        'changes or a new release appears. Close it to acknowledge a change.',
        '',
        table(results),
        '',
        '- **Server contract**: the pinned SDK (adapter and managed host) against a real local server of that release: connect, host '
        'online, status and catalog, reconnect, restart, disconnect (`tests/server-contract/run.py`). ❌ means the plugin as shipped '
        'stops working against servers of that release.',
        '- **Anchors**: whether `hermes_omnigent/patches.py` can still patch that release\'s SDK files '
        '(`.github/scripts/check-omnigent-compat.py`; the file named is the one that no longer matches). ❌ does not affect the plugin '
        f'while it stays on `{results["pin"]}`; it is what an SDK upgrade has to fix first.',
        '- ⚠️ means the check could not run (install or setup failed), not that the release is incompatible.',
        '',
        f'Last run: {run_url} ({today})',
        f'<!-- omnigent-canary-state {state} -->',
    ])


def read_state(body):
    found = STATE.search(body or '')
    if not found:
        return None
    try:
        value = json.loads(found.group(1))
    except ValueError:
        return None
    return value if isinstance(value, dict) and isinstance(value.get('rows'), dict) else None


def changes(old, new):
    """What differs between the previous issue state and this run, as sentences. [] for a first run or no change."""
    if not old:
        return []
    if old.get('pin') != new['pin']:
        return [f'The pinned SDK moved from `{old.get("pin")}` to `{new["pin"]}`; the table was recomputed.']
    found = []
    for version, now in new['rows'].items():
        before = old['rows'].get(version)
        if not isinstance(before, dict):
            found.append(f'New release `{version}`: server contract {now["contract"]}, anchors {now["anchors"]}.')
            continue
        for kind, name in (('contract', 'server contract'), ('anchors', 'anchors')):
            if before.get(kind) != now[kind]:
                found.append(f'`{version}` {name}: {before.get(kind)} → {now[kind]}.')
    return found


def valid(results):
    """Results come from an earlier job's output; check the shape before anything is built from it."""
    try:
        return (VERSION.fullmatch(results['pin']) is not None and bool(results['rows']) and all(
            VERSION.fullmatch(row['version']) and all(row[kind]['status'] in STATUSES for kind in ('contract', 'anchors'))
            for row in results['rows']))
    except (KeyError, TypeError):
        return False


# --- the check job -------------------------------------------------------------------------------------------

def sh(command, timeout, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, **kwargs)


def check_anchors(version, work):
    wheels = work / 'wheels'
    done = sh([sys.executable, '-m', 'pip', 'download', '--no-deps', '--only-binary=:all:', '--disable-pip-version-check',
               '-q', '-d', str(wheels), f'omnigent=={version}'], 300)
    wheel = next(wheels.glob(f'omnigent-{version}-*.whl'), None)
    if done.returncode != 0 or wheel is None:
        return {'status': 'error', 'code': 'download failed', 'file': None}
    try:
        done = sh([sys.executable, str(ROOT / '.github' / 'scripts' / 'check-omnigent-compat.py'), '--wheel', str(wheel), '--label', version], 300)
        print(done.stdout.strip().splitlines()[0] if done.stdout.strip() else f'no output from the anchor check (exit {done.returncode})')
        return anchors_result(done.returncode, done.stdout)
    finally:
        wheel.unlink(missing_ok=True)


def check_contract(version, pin, work, logs):
    command = [sys.executable, str(ROOT / 'tests' / 'server-contract' / 'run.py'),
               '--expect-sdk-version', pin, '--expect-server-version', version, '--logs', str(logs / version)]
    server = work / f'server-{version}'
    try:
        if version != pin:
            # The server under test gets its own installation; the adapter and the managed host keep the pinned SDK.
            made = sh([sys.executable, '-m', 'venv', str(server)], 120)
            installed = made if made.returncode else sh(
                [str(server / 'bin' / 'python'), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-q', f'omnigent=={version}'], 900)
            if installed.returncode != 0:
                (logs / version).mkdir(parents=True, exist_ok=True)
                (logs / version / 'install.log').write_text((installed.stdout + installed.stderr)[-4000:])
                return {'status': 'error', 'step': 'install the server'}
            command += ['--server-omnigent', str(server / 'bin' / 'omnigent')]
        done = sh(command, 900)
        for line in (done.stdout + done.stderr).splitlines():
            if line.startswith(('ok ', 'FAIL ', 'setup failed')):
                print(f'  {line}')
        return contract_result(done.returncode, done.stdout)
    except subprocess.TimeoutExpired:
        return {'status': 'error', 'step': 'timed out'}
    finally:
        shutil.rmtree(server, ignore_errors=True)


def run(args):
    pin = pinned((ROOT / 'hermes_omnigent' / 'runtime.py').read_text())
    with urllib.request.urlopen(INDEX, timeout=60) as reply:
        index = json.load(reply)
    versions = [pin, *newer_releases(index, pin, args.limit)]
    work = Path(args.work)
    logs = work / 'logs'
    work.mkdir(parents=True, exist_ok=True)
    installed = sh([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-q', f'omnigent=={pin}'], 900)
    if installed.returncode != 0:
        print(f'cannot install the pinned omnigent=={pin}:\n{installed.stdout}{installed.stderr}'[-3000:], file=sys.stderr)
        return 1
    rows = []
    for version in versions:
        print(f'::group::Omnigent {version}')
        rows.append({'version': version, 'anchors': check_anchors(version, work), 'contract': check_contract(version, pin, work, logs)})
        print('::endgroup::')
    results = {'pin': pin, 'rows': rows}
    Path(args.out).write_text(json.dumps(results, separators=(',', ':')))
    print(table(results))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(f'### Omnigent canary (pinned SDK {pin})\n\n{table(results)}\n')
    return 0 if all(row['contract']['status'] == 'pass' for row in rows) else 1


# --- the report job ----------------------------------------------------------------------------------------

def gh(*argv, text=None):
    done = sh(['gh', *argv], 60, input=text)
    if done.returncode != 0:
        raise SystemExit(f'gh {" ".join(argv[:2])} failed: {done.stderr.strip()[:300]}')
    return done.stdout


def report(_args):
    try:
        results = json.loads(os.environ.get('RESULTS') or 'null')
    except ValueError:
        results = None
    if not valid(results):
        print('::error::the check job produced no usable results; the issue was left as it was')
        return 1
    run_url, today = os.environ['RUN_URL'], os.environ['TODAY']
    body = render(results, run_url, today)
    gh('label', 'create', LABEL, '--force', '--color', '5319e7', '--description', 'Omnigent compatibility canary')
    issues = json.loads(gh('issue', 'list', '--label', LABEL, '--state', 'all', '--limit', '20', '--json', 'number,state,title,body'))
    issue = next((i for i in issues if i['title'] == TITLE), None)
    if issue is None:
        print(gh('issue', 'create', '--title', TITLE, '--label', LABEL, '--body-file', '-', text=body).strip())
        return 0
    moved = changes(read_state(issue['body']), state_of(results))
    if body != issue['body']:
        gh('issue', 'edit', str(issue['number']), '--body-file', '-', text=body)
    if moved:
        if issue['state'] == 'CLOSED':
            gh('issue', 'reopen', str(issue['number']))
        gh('issue', 'comment', str(issue['number']), '--body-file', '-', text='\n'.join(['Changed since the last run:', '', *[f'- {line}' for line in moved], '', run_url]))
    print(f'issue #{issue["number"]}: ' + ('; '.join(moved) if moved else 'no change'))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    runner = commands.add_parser('run', help='check the pin and the newest releases above it')
    runner.add_argument('--work', required=True, help='scratch directory for wheels, server installations and logs')
    runner.add_argument('--out', required=True, help='where to write the results JSON')
    runner.add_argument('--limit', type=int, default=4, help='how many releases newer than the pin to check (default 4)')
    runner.set_defaults(handler=run)
    commands.add_parser('report', help='update the standing issue from $RESULTS').set_defaults(handler=report)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == '__main__':
    sys.exit(main())
