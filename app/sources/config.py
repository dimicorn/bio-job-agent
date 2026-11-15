from pathlib import Path

import yaml

from app.models.posting import SourceConfig


def load_sources(path: Path) -> list[SourceConfig]:
    sources = [SourceConfig.model_validate(x) for x in yaml.safe_load(path.read_text())["sources"]]
    if len({x.id for x in sources}) != len(sources):
        raise ValueError("Duplicate source IDs")
    return sources
