"""Private creation and repair for locally stored candidate data."""

import os
from pathlib import Path


def private_dir(path):
    path = Path(path)
    if not path.exists():
        private_dir(path.parent)
        path.mkdir(exist_ok=True, mode=0o700)


def private_open(path, mode="w", **kwargs):
    path = Path(path)
    private_dir(path.parent)
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if "a" in mode else os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, mode, **kwargs)


def private_file(path):
    with private_open(path, "a"):
        pass


def tighten_tree(path):
    path = Path(path)
    if not path.exists() or path.is_symlink():
        return
    if path.is_dir():
        path.chmod(0o700)
        for child in path.iterdir():
            tighten_tree(child)
    elif path.is_file():
        path.chmod(0o600)


def tighten_configured(cfg):
    for path in (cfg.materials_path, cfg.apply.audit_dir, cfg.apply.profile_dir):
        tighten_tree(cfg.resolved(path))
    for path in (cfg.apply.answers_file, cfg.database_path):
        tighten_tree(cfg.resolved(path))
    db = cfg.resolved(cfg.database_path)
    for suffix in ("-wal", "-shm"):
        tighten_tree(str(db) + suffix)
    for name in ("llm-usage.jsonl", ".llm-limits.json", ".llm-limits.tmp"):
        tighten_tree(cfg.resolved(cfg.llm_state_dir) / name)
