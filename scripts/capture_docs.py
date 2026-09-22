"""Render current documentation from saved records, then capture it; no Docker operations."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from report import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New output directory; never overwrite historical captures')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists():
        parser.error('Output exists; choose a new directory')
    runtime = root / '.runtime' / 'docs-captures'
    runtime.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='current-', dir=runtime))
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    inputs = {}
    for relative in filter(None, tracked):
        if relative.startswith('docs/evidence/') or relative == 'README.md' or (
            relative.startswith('docs/') and relative.endswith('.md')
        ):
            source = root / relative
            if not source.is_file():
                continue
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if relative.startswith('docs/evidence/') and relative.endswith('.json'):
                inputs[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    report = generate(stage, root / '.runtime')
    subprocess.run(['node', str(root / 'scripts/capture_docs.cjs'), str(report), str(output)], check=True)
    (output / 'inputs.json').write_text(json.dumps({
        'scope': 'Historical inputs rendered with current code; no new operations',
        'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(),
        'inputs': inputs,
    }, indent=2) + '\n', encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
