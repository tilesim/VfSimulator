"""Generate the wheel's namespaced modules from the root Python implementation."""
from pathlib import Path
import argparse
import re

ROOT = Path(__file__).resolve().parents[1]


def expected_files():
    for directory in ('api', 'core', 'configs'):
        for source in (ROOT / directory).rglob('*'):
            if not source.is_file() or 'native' in source.parts or source.suffix not in ('.py', '.json', '.md'):
                continue
            content = source.read_text(encoding='utf-8')
            if source.suffix == '.py':
                content = re.sub(r'(?m)^(\s*from )(api|core)(?=[. ])', r'\1vfsimulator.\2', content)
                if re.search(r'(?m)^\s*import (api|core)(?:[. ,]|$)', content):
                    raise ValueError(f'Unsupported absolute import in {source}; use from imports')
            yield ROOT / 'vfsimulator' / source.relative_to(ROOT), content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = dict(expected_files())
    existing = {p for d in ('api', 'core', 'configs') for p in (ROOT/'vfsimulator'/d).rglob('*')
                if p.is_file() and p.suffix in ('.py', '.json', '.md')}
    differences = []
    for path in existing - expected.keys():
        differences.append(str(path.relative_to(ROOT)))
        if not args.check:
            path.unlink()
    for path, content in expected.items():
        if not path.exists() or path.read_text(encoding='utf-8') != content:
            differences.append(str(path.relative_to(ROOT)))
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')
    if args.check and differences:
        parser.exit(1, 'Package is out of sync:\n' + '\n'.join(differences) + '\n')
    print(f'Package {"checked" if args.check else "synchronized"}: {len(expected)} files')


if __name__ == '__main__':
    main()
