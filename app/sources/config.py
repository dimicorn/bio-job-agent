from pathlib import Path

import yaml

from app.models.posting import SourceConfig


def load_sources(path: Path) -> list[SourceConfig]:
    data = yaml.safe_load(path.read_text())
    sources = [SourceConfig.model_validate(x) for x in data["sources"]]
    if len({x.id for x in sources}) != len(sources):
        raise ValueError("Duplicate source IDs")
    if data.get("watchlist"):
        from app.sources.firms import load_firms

        # Explicit entries retain local enable/disable settings for existing boards.
        configured = {(s.adapter, s.board) for s in sources}
        for firm in load_firms(path.parent / data["watchlist"]):
            source = firm.source()
            if (source.adapter, source.board) not in configured:
                sources.append(source)
    if len({x.id for x in sources}) != len(sources):
        raise ValueError("Duplicate source IDs after loading watchlist")
    return sources
