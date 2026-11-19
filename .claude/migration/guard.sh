#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 - <<'PY'
import csv
import re
import subprocess
from pathlib import Path

root = Path.cwd()
config = (root / '.claude/migration/RATCHETS.yaml').read_text()
failed = []
for block in config.split('  - id: ')[1:]:
    name = block.splitlines()[0]
    raw_scope = re.search(r'^    scope: (.+)$', block, re.M).group(1)
    scopes = [p.strip() for p in raw_scope.strip('[]').split(',')]
    query = re.search(r"^    query: '(.*)'$", block, re.M).group(1)
    maximum = int(re.search(r'^    maximum: (\d+)$', block, re.M).group(1))
    count = 0
    for scope in scopes:
        path = root / scope
        files = [path] if path.is_file() else [p for p in path.rglob('*') if p.suffix in {'.py', '.kt', '.java'}]
        count += sum(p.read_text().count(query) for p in files)
    print(f'{name}: {count} <= {maximum}')
    if count > maximum:
        failed.append(name)
with (root / '.claude/migration/MAP.csv').open() as stream:
    mapped = {row['target_path'] for row in csv.DictReader(stream)}
changed = set(subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], text=True).splitlines())
changed.update(subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], text=True).splitlines())
unmapped = sorted(changed - mapped)
if unmapped:
    failed.append('unmapped changes: ' + ', '.join(unmapped))
subprocess.run(['git', 'diff', '--check'], check=True)
if failed:
    raise SystemExit('\n'.join(failed))
print('Optimization guard PASS')
PY
