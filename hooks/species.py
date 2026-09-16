"""Publish the species list as `species.json` beside the docs.

The species page's search box reads it: one row per bird BirdNET v2.4 can
report, with the plate count the artwork tree holds for it, so the list is
built from the repo at build time rather than kept by hand. Rows are keyed
through `normalize`, so a reclassified bird is one row under its current name
with the label's spelling beside it. Plates for a bird v2.4 has no label for
are listed too, marked undetectable, and hybrids are left out since BirdNET
never emits one.
"""

import json
import re
from pathlib import Path

from fugleramme.names import BIRDS, artwork_in, canonical, normalize
from fugleramme.taxa import LABELS, is_bird

REPO = Path(__file__).resolve().parents[1]
ARTWORK = REPO / "assets" / "artwork"
OUT = "species.json"


def _plates() -> dict[str, list[str]]:
    """Species key -> shipped plates across every style, as paths under
    `assets/artwork/` so the page can link each file."""
    plates: dict[str, list[str]] = {}
    for style in ARTWORK.iterdir():
        for path in artwork_in(style / BIRDS):
            key = re.sub(r"-\d+$", "", path.stem)
            if "-x-" not in key:
                plates.setdefault(key, []).append(path.relative_to(ARTWORK).as_posix())
    # "<key>.webp" first, then -2, -3: a path sort would put the variants before it
    return {key: sorted(files, key=len) for key, files in plates.items()}


def _labels() -> list[tuple[str, str]]:
    """(scientific, common) for every bird label."""
    rows = (line.split("_", 1) for line in LABELS.read_text().splitlines() if "_" in line)
    return [(sci, common) for sci, common in rows if is_bird(sci)]


def rows() -> list[dict[str, object]]:
    plates = _plates()
    listed: dict[str, dict[str, object]] = {}
    for sci, common in _labels():
        key = normalize(sci)
        row = listed.setdefault(
            key, {"name": canonical(sci), "common": common, "plates": plates.pop(key, [])}
        )
        if sci == row["name"]:
            row["common"] = common  # the label under the current name wins
        else:  # an older spelling, or a second label since lumped into this one
            row["label"] = f"{row['label']}, {sci}" if "label" in row else sci
    for key, files in plates.items():  # art for a bird no label names
        name = key.replace("-", " ").capitalize()
        listed[key] = {"name": name, "common": "", "plates": files, "detectable": False}
    return sorted(listed.values(), key=lambda row: str(row["name"]))


def on_post_build(config, **_) -> None:
    (Path(config["site_dir"]) / OUT).write_text(json.dumps(rows(), ensure_ascii=False))
