#!/usr/bin/env python3
"""Build a standalone installer bound to the original release tag."""
import argparse
import hashlib
import re
from pathlib import Path


def build(source: Path, target: Path, tag: str) -> None:
    if not re.fullmatch(r'v[0-9][A-Za-z0-9._-]*', tag):
        raise ValueError('Invalid release tag')
    script = source.read_text()
    marker = 'INSTALLER_RELEASE="" # Set by scripts/release/build_installer.py; empty only in source checkouts.'
    if script.count(marker) != 1:
        raise ValueError('Installer release marker is missing or duplicated')
    target.write_text(script.replace(marker, f'INSTALLER_RELEASE="{tag}" # Immutable default release.'))
    target.chmod(0o755)
    target.with_name(target.name + '.sha256').write_text(f'{hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--source', type=Path, default=Path('scripts/install.sh'))
    parser.add_argument('--output', type=Path, default=Path('neurocade-install.sh'))
    args = parser.parse_args()
    build(args.source, args.output, args.tag)
