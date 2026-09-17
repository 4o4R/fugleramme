#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--approved", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()

    repo = a.repo.resolve()
    inputs = a.inputs.resolve()
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    import sys
    sys.path[:0] = [str(repo), str(repo / "src")]
    from fugleramme.names import normalize
    from tools.add_bird import prepare, write_plate

    approved = json.loads(a.approved.read_text())
    ledgers: dict[int, dict[tuple[int, int], dict]] = {}
    for part in range(4):
        rows = json.loads((inputs / str(part) / "ledger.json").read_text())
        ledgers[part] = {(int(r["rank"]), int(r["variant"])): r for r in rows}

    birds = repo / "assets/artwork/classic/birds"
    manifest_path = repo / "assets/artwork/classic/manifest.json"
    attr_path = repo / "assets/artwork/classic/ATTRIBUTION.md"
    manifest = json.loads(manifest_path.read_text())
    attribution = attr_path.read_text()
    existing = {re.sub(r"-\d+$", "", p.stem) for p in birds.glob("*.webp")}

    shipped, skipped = [], []
    for item in approved:
        part, rank, variant = int(item["part"]), int(item["rank"]), int(item["variant"])
        row = ledgers[part][(rank, variant)]
        key = row["key"]
        assert key == item["key"], (item, row["key"])
        assert normalize(row["scientific"]) == key, (row["scientific"], key)
        assert row["license"] in {"Public domain", "CC0"}, row["license"]
        marker = f"Manifest key: `{item['source_key']}`"
        assert marker in attribution, f"Missing attribution marker for {item['source_key']}"
        if key in existing:
            skipped.append({"key": key, "reason": "already covered on current main"})
            continue

        src = inputs / str(part) / row["preview"]
        with Image.open(src) as im:
            assert im.mode == "RGBA", (key, im.mode)
            lo, hi = im.getchannel("A").getextrema()
            assert lo == 0 and hi == 255, (key, lo, hi)
        dest = birds / f"{key}.webp"
        write_plate(prepare(src), dest)
        manifest[f"birds/{dest.name}"] = {
            "source": item["source_key"],
            "url": row["source_page"],
        }
        existing.add(key)
        shipped.append({
            "part": part,
            "rank": rank,
            "variant": variant,
            "common": row["common"],
            "scientific": row["scientific"],
            "key": key,
            "asset": dest.name,
            "source_key": item["source_key"],
            "source_page": row["source_page"],
            "source_title": row["source_title"],
            "artist": row.get("artist", ""),
            "credit": row.get("credit", ""),
            "license": row["license"],
            "review_status": "visually approved refined candidate",
        })

    manifest_path.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n")
    (out / "asset-provenance.json").write_text(json.dumps(shipped, indent=2) + "\n")
    (out / "summary.json").write_text(json.dumps({
        "approved_requested": len(approved),
        "shipped": len(shipped),
        "skipped_existing": len(skipped),
        "skipped": skipped,
    }, indent=2) + "\n")
    print((out / "summary.json").read_text())
    if not shipped:
        raise RuntimeError("No new approved refined assets remained after current-main deduplication")


if __name__ == "__main__":
    main()
