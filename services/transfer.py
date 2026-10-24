"""What an agent carries into its next stage (agent formal copies).

A transfer package is a small, host-owned copy of selected workspace folders plus,
unless the context is 'clear', the conversation transcript. The full frame history
stays once in the source trial's published/ tree; it is never copied or mounted
downstream, so historical paths in a resumed conversation may no longer resolve.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat

from services.controller.storage import runtime_digest

CONTEXTS = ('resume', 'compact', 'clear')
# Workspace folders a package may carry; absent ones are simply skipped. The handoff
# folder is optional: the agent writes it when its instructions ask for one.
PARTS = {'memory': 'memory', 'code': 'code', 'handoff': 'handoff'}
# Packing order: a part that would exceed the cap is left out whole, never truncated.
PRIORITY = ('handoff', 'memory', 'code')
MAX_FILES = 20000
DEFAULT_MB = 100


def measure(root):
    """(files, bytes) of the regular files a copy would carry; links are not followed."""
    files = size = 0
    for directory, names, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            info = os.lstat(os.path.join(directory, name))
            if stat.S_ISREG(info.st_mode):
                files, size = files + 1, size + info.st_size
    return files, size


def transfer_size(workspace, parts):
    """Current size of each selected part, for the agent's status during exploration."""
    result = {}
    for part in parts:
        source = Path(workspace)/PARTS[part]
        result[part] = measure(source)[1] if source.is_dir() and not source.is_symlink() else 0
    return result


def validate_parts(parts):
    if (not isinstance(parts, tuple) or len(set(parts)) != len(parts)
            or any(part not in PARTS for part in parts)):
        raise ValueError(f'transfer_parts must be distinct names from {sorted(PARTS)}')


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def copy_regular(source, destination, *, budget):
    """Copy regular files only (never following links); report what was skipped.

    `budget` is a dict with remaining 'files' and 'bytes', shared across calls.
    """
    copied, skipped = {}, []
    source, destination = Path(source), Path(destination)

    def visit(fd, output, prefix):
        for name in sorted(os.listdir(fd)):
            relative = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if relative.count('/') > 24:
                    skipped.append((relative, 'too deep'))
                    continue
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    (output/name).mkdir(mode=0o755)
                    visit(child, output/name, relative + '/')
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                if budget['files'] <= 0 or info.st_size > budget['bytes']:
                    skipped.append((relative, 'transfer size limit'))
                    continue
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(child, 'rb') as src, (output/name).open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                budget['files'] -= 1
                budget['bytes'] -= info.st_size
                copied[relative] = info.st_size
            else:
                skipped.append((relative, 'link or special file'))

    destination.mkdir(parents=True, mode=0o755)
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        visit(fd, destination, '')
    finally:
        os.close(fd)
    return copied, skipped


def pack(destination, *, workspace, parts, max_mb=DEFAULT_MB, home=None, session_files=(), home_files=(),
         metadata=None):
    """Write a package directory and return its bundle-compatible manifest.

    Only `max_mb` of transfer folders are carried (the conversation transcript is
    separate). `session_files` are paths relative to `home` (the CLI's transcript files);
    `home_files` are the launcher-written CLI settings every new home needs.
    """
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    budget = {'files': MAX_FILES, 'bytes': max_mb * 1024**2}
    record = {'parts': {}, 'excluded': {}, 'skipped': [], 'max_mb': max_mb,
              'session_files': [str(p) for p in session_files],
              'home_files': [str(p) for p in home_files if (Path(home)/p).is_file()], **(metadata or {})}
    for part in sorted(parts, key=PRIORITY.index):
        source = Path(workspace)/PARTS[part]
        if not source.is_dir() or source.is_symlink():
            record['parts'][part] = None  # Not present in the source workspace.
            continue
        files, size = measure(source)
        if files > budget['files'] or size > budget['bytes']:
            record['parts'][part] = None
            record['excluded'][part] = (f'{size / 1024**2:.1f} MB in {files} files exceeds the remaining '
                                        f'{budget["bytes"] / 1024**2:.1f} MB of the {max_mb} MB limit on carried folders')
            continue
        copied, skipped = copy_regular(source, destination/'transfer'/PARTS[part], budget=budget)
        record['parts'][part] = {'files': len(copied), 'bytes': sum(copied.values())}
        record['skipped'] += [f'{PARTS[part]}/{name}: {why}' for name, why in skipped]
    for relative in [*session_files, *record['home_files']]:
        relative = PurePosixPath(relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid session file path')
        source = Path(home)/relative
        if source.is_symlink() or not source.is_file():
            raise ValueError(f'Session file missing: {relative}')
        target = destination/'session'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (destination/'package.json').write_text(json.dumps(record, indent=2) + '\n')
    files = {path.relative_to(destination).as_posix(): _digest(path)
             for path in sorted(destination.rglob('*')) if path.is_file()}
    for path in destination.rglob('*'):
        if path.is_file():
            path.chmod(0o444)
    manifest = {'kind': 'agent_checkpoint', 'files': files, 'bytes': sum(
        (destination/name).stat().st_size for name in files), 'runtime_sha256': runtime_digest(),
        # A canonical placeholder: agent checkpoints have no controller project.
        'workspace_path': 'code/agent-checkpoint'}
    manifest['sha256'] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return manifest, record


def unpack(package, *, workspace, home=None, context='resume'):
    """Place a package's transfer folders into a new workspace and, unless clearing,
    its transcript into a new agent home. Returns the transferred folder names."""
    package = Path(package)
    record = json.loads((package/'package.json').read_text())
    present = []
    for part, info in record['parts'].items():
        if info is None:
            continue
        target = Path(workspace)/PARTS[part]
        shutil.copytree(package/'transfer'/PARTS[part], target, dirs_exist_ok=True)
        for path in [target, *target.rglob('*')]:
            path.chmod(0o755 if path.is_dir() else 0o644)  # A private, writable copy.
        present.append(PARTS[part])
    for relative in record['home_files'] + (record['session_files'] if context != 'clear' else []):
        target = Path(home)/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package/'session'/relative, target)
        target.chmod(0o600)
    return present
