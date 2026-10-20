"""Map local backend sources to the collector's established deployment layout."""
from pathlib import Path


def runtime_files(root):
    root = Path(root)
    backend = root / 'backend' if (root / 'backend').is_dir() else root
    return ([root / 'requirements.txt']
            + sorted((backend / 'scripts').glob('*.py'))
            + sorted((backend / 'server').glob('*.py'))
            + sorted((root / 'deploy/collector').glob('*.py'))
            + sorted((root / 'deploy/collector').glob('*.sh')))


def archive_name(path, root):
    relative = Path(path).relative_to(root)
    if relative.parts[0] == 'backend':
        relative = Path(*relative.parts[1:])
    return relative.as_posix()
