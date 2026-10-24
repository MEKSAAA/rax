#!/usr/bin/env python3
"""Build the offline agent image (default tag rax-auto-research:dev).

Downloads the wheels in services/controller/agent_requirements.txt into a local wheelhouse,
locks them by SHA-256 and builds services/controller/agent_runtime.Dockerfile on python:3.12-slim.
Agent and bundle containers run this image without network; sessions pin its digest when they
are prepared, so rebuilding the tag never changes a prepared or running session.

    runtime/envs/host/bin/python scripts/build_agent_image.py
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.storage_root import artifact_root  # noqa: E402

BASE = 'python:3.12-slim'
REQUIREMENTS = ROOT/'services/controller/agent_requirements.txt'
DOCKERFILE = ROOT/'services/controller/agent_runtime.Dockerfile'


def resolved_wheels(wheels):
    """Select one consistent dependency set, not every wheel ever downloaded into the directory."""
    report = json.loads(subprocess.check_output([
        sys.executable, '-m', 'pip', 'install', '--dry-run', '--ignore-installed',
        '--quiet', '--no-index', '--find-links', str(wheels),
        '--python-version', '3.12', '--only-binary=:all:', '--report', '-',
        '-r', str(REQUIREMENTS)], text=True))
    selected = []
    for item in report['install']:
        url = urlparse(item['download_info']['url'])
        path = Path(unquote(url.path)).resolve()
        if url.scheme != 'file' or url.netloc or path.parent != wheels.resolve() or path.suffix != '.whl':
            raise ValueError('Resolver selected a file outside the local wheelhouse')
        selected.append(path)
    return sorted(selected)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--directory', type=Path, default=ROOT/'runtime/agent-image',
                        help='Wheelhouse and build context (default: runtime/agent-image)')
    parser.add_argument('--tag', default='rax-auto-research:dev')
    parser.add_argument('--offline', action='store_true',
                        help='Reuse previously downloaded wheels; refuse if they differ from the recorded hashes')
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to(artifact_root()):
        parser.error(f'Build artifacts must live under {artifact_root()} (set RAX_ARTIFACT_ROOT to change it)')
    if subprocess.run(['docker', 'image', 'inspect', BASE], capture_output=True).returncode:
        subprocess.run(['docker', 'pull', BASE], check=True)
    spec = json.loads(subprocess.check_output(['docker', 'image', 'inspect', BASE]))[0]
    base = next(d for d in spec['RepoDigests'] if d.startswith('python@sha256:'))

    wheels = directory/'wheels'
    wheels.mkdir(parents=True, exist_ok=True)
    if not args.offline:
        subprocess.run([sys.executable, '-m', 'pip', 'download', '--no-cache-dir', '--only-binary=:all:',
                        '--python-version', '3.12', '--index-url', 'https://pypi.org/simple',
                        '--extra-index-url', 'https://download.pytorch.org/whl/cu128',
                        '--dest', str(wheels), '-r', str(REQUIREMENTS)], check=True)
    hashes = {wheel.name: sha256(wheel) for wheel in resolved_wheels(wheels)}
    if not hashes:
        parser.error('No wheels available')
    record = directory/'build.json'
    if args.offline and hashes != json.loads(record.read_text())['wheels']:
        parser.error('Offline wheelhouse differs from the recorded hashes')
    (directory/'requirements.lock').write_text(''.join(
        f'/wheels/{name} --hash=sha256:{digest}\n' for name, digest in hashes.items()))
    shutil.copyfile(DOCKERFILE, directory/'Dockerfile')
    subprocess.run(['docker', 'build', '--pull=false', '--build-arg', f'BASE_IMAGE={base}', '-t', args.tag,
                    str(directory)], check=True, env={**os.environ, 'DOCKER_BUILDKIT': '1'})
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.tag]))[0]['Id']
    record.write_text(json.dumps({'image': image, 'tag': args.tag, 'base': base, 'wheels': hashes}, indent=2))
    print(json.dumps({'image': image, 'tag': args.tag, 'record': str(record)}))


if __name__ == '__main__':
    main()
