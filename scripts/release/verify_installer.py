#!/usr/bin/env python3
"""Verify a downloaded release installer before running its side-effect-free help."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def verify(directory: Path, tag: str) -> None:
    manifest = json.loads((directory / 'neurocade-release.json').read_text())
    expected = {'filename': 'neurocade-install.sh', 'sha256_filename': 'neurocade-install.sh.sha256'}
    if manifest.get('tag') != tag or manifest.get('installer') != expected:
        raise ValueError('Downloaded release installer metadata does not match the release')
    installer = directory / expected['filename']
    checksum = (directory / expected['sha256_filename']).read_text().split()
    if checksum != [hashlib.sha256(installer.read_bytes()).hexdigest(), installer.name]:
        raise ValueError('Downloaded installer checksum does not match')
    if f'INSTALLER_RELEASE="{tag}" # Immutable default release.' not in installer.read_text():
        raise ValueError('Downloaded installer is not bound to this release')
    subprocess.run(['bash', '-n', str(installer)], check=True)
    subprocess.run(['bash', str(installer), '--help'], check=True, stdout=subprocess.DEVNULL, timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('tag')
    args = parser.parse_args()
    verify(args.directory, args.tag)
