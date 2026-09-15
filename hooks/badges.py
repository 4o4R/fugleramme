"""Publish the artwork counts as shields.io endpoint badges beside the docs.

The README's badge row reads `badges/<name>.json` off the docs site, so the
numbers are counted from the tree at build time rather than kept by hand.
Schema: https://shields.io/badges/endpoint-badge
"""

import json
import re
from pathlib import Path

from fugleramme.names import BIRDS, SUFFIXES

REPO = Path(__file__).resolve().parents[1]
ARTWORK = REPO / "assets" / "artwork"


def _plates() -> list[str]:
    return [
        path.stem
        for style in ARTWORK.iterdir()
        if (style / BIRDS).is_dir()
        for suffix in SUFFIXES
        for path in (style / BIRDS).glob(f"*{suffix}")
    ]


def counts() -> dict[str, str]:
    plates = _plates()
    species = {re.sub(r"-\d+$", "", stem) for stem in plates}
    return {"artwork": str(len(plates)), "species": str(len(species))}


def on_post_build(config, **_) -> None:
    out = Path(config["site_dir"]) / "badges"
    out.mkdir(exist_ok=True)
    for label, message in counts().items():
        badge = {"schemaVersion": 1, "label": label, "message": message, "color": "teal"}
        (out / f"{label}.json").write_text(json.dumps(badge))
