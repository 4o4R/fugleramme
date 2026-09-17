#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageFilter

PAPER = (240, 236, 229)
ITEMS = [
    {
        "common": "Neotropic Cormorant",
        "scientific": "Nannopterum brasilianum",
        "key": "nannopterum-brasilianum",
        "pageid": 65657903,
        "box": [350, 260, 1080, 1300],
        "sha256": "47566a7aa1226f6b0c74a8fc1d20b9a5b9f608a72f1bb48826e23b499da291af",
        "source_key": "loc-popular-graphic-arts",
        "url": "https://commons.wikimedia.org/wiki/File:Phalacrocorax_brasilianus_(GM)_LCCN2003662380.jpg",
    },
    {
        "common": "Green Jay",
        "scientific": "Cyanocorax yncas",
        "key": "cyanocorax-yncas",
        "pageid": 43508741,
        "box": [300, 300, 950, 1450],
        "sha256": "7a53d5ab79ec97b1aab4323a44b91b1dba6a13e61ce85b65d10816155daa2f39",
        "source_key": "cassin-illustrations",
        "url": "https://commons.wikimedia.org/wiki/File:Illustrations_of_the_birds_of_California,_Texas,_Oregon,_British_and_Russian_America_(Plate_1)_(6306529250).jpg",
    },
    {
        "common": "Shiny Cowbird",
        "scientific": "Molothrus bonariensis",
        "key": "molothrus-bonariensis",
        "pageid": 54987186,
        "box": [330, 300, 1050, 1450],
        "sha256": "777cbb7bf0120ab5438c3c87361ababdd5263dd0553adab232c14a09feb5bc22",
        "source_key": "iconographia-zoologica",
        "url": "https://commons.wikimedia.org/wiki/File:Molothrus_bonariensis_-_1700-1880_-_Print_-_Iconographia_Zoologica_-_Special_Collections_University_of_Amsterdam_-_UBA01_IZ15800297.tif",
    },
    {
        "common": "Curve-billed Thrasher",
        "scientific": "Toxostoma curvirostre",
        "key": "toxostoma-curvirostre",
        "pageid": 56710832,
        "box": [380, 220, 1050, 1500],
        "sha256": "4602384d2c480d3eb023e6be96405530eb40bd9ec03842c3f2cc38998c07a681",
        "source_key": "iconographia-zoologica",
        "url": "https://commons.wikimedia.org/wiki/File:Mimus_curvirostris_-_1700-1880_-_Print_-_Iconographia_Zoologica_-_Special_Collections_University_of_Amsterdam_-_UBA01_IZ16300341.tif",
    },
]


def cleanup(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return mask.astype(bool)
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest = int(np.argmax(areas)) + 1
    keep = {largest}
    largest_area = int(stats[largest, cv2.CC_STAT_AREA])
    x, y, w, h = stats[largest, :4]
    margin = max(12, int(max(w, h) * 0.18))
    near = (x - margin, y - margin, x + w + margin, y + h + margin)
    for idx in range(1, n):
        if idx == largest:
            continue
        cx = stats[idx, cv2.CC_STAT_LEFT] + stats[idx, cv2.CC_STAT_WIDTH] / 2
        cy = stats[idx, cv2.CC_STAT_TOP] + stats[idx, cv2.CC_STAT_HEIGHT] / 2
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area >= max(10, int(largest_area * 0.0015)) and near[0] <= cx <= near[2] and near[1] <= cy <= near[3]:
            keep.add(idx)
    return np.isin(labels, list(keep))


def cut(source: Image.Image, mask: np.ndarray) -> Image.Image:
    mask = cleanup(mask)
    alpha = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.45))
    bbox = alpha.getbbox()
    if bbox is None:
        raise RuntimeError("empty segmentation mask")
    rgba = source.crop(bbox).convert("RGBA")
    rgba.putalpha(alpha.crop(bbox))
    radius = max(5, round(max(rgba.size) * 0.014))
    pad = radius + 4
    padded = Image.new("RGBA", (rgba.width + 2 * pad, rgba.height + 2 * pad), (*PAPER, 0))
    padded.paste(rgba, (pad, pad))
    halo = Image.new("RGBA", padded.size, (*PAPER, 0))
    halo.putalpha(padded.getchannel("A").filter(ImageFilter.MaxFilter(radius * 2 + 1)))
    final = Image.alpha_composite(halo, padded)
    final.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    return final


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--mask-tools", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    repo, sources, tools, out = map(Path.resolve, (a.repo, a.sources, a.mask_tools, a.output))
    out.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(tools))
    from mobile_sam import SamPredictor, sam_model_registry

    sys.path[:0] = [str(repo), str(repo / "src")]
    from fugleramme.names import normalize
    from tools.add_bird import prepare, write_plate

    predictor = SamPredictor(sam_model_registry["vit_t"](checkpoint=str(tools / "mobile_sam.pt")).eval())
    torch.set_num_threads(4)
    cv2.setNumThreads(1)

    birds = repo / "assets/artwork/classic/birds"
    existing = {re.sub(r"-\d+$", "", p.stem) for p in birds.glob("*.webp")}
    manifest_path = repo / "assets/artwork/classic/manifest.json"
    attr_path = repo / "assets/artwork/classic/ATTRIBUTION.md"
    manifest = json.loads(manifest_path.read_text())
    attribution = attr_path.read_text()
    shipped, skipped = [], []

    additions = {
        "loc-popular-graphic-arts": "**Library of Congress Popular Graphic Arts** - historical bird print from the Library of Congress Popular Graphic Arts collection, via the exact Wikimedia Commons record linked in the manifest. Public domain. Manifest key: `loc-popular-graphic-arts`.",
        "iconographia-zoologica": "**Iconographia Zoologica (University of Amsterdam)** - historical zoological bird prints from the University of Amsterdam Special Collections *Iconographia Zoologica*, via the exact Wikimedia Commons records linked in the manifest. Public domain. Manifest key: `iconographia-zoologica`.",
    }

    for item in ITEMS:
        assert normalize(item["scientific"]) == item["key"], item
        key = item["key"]
        if key in existing:
            skipped.append({"key": key, "reason": "already covered on current main"})
            continue
        src_path = sources / f"sources-{item['pageid'] % 4}" / f"{item['pageid']}.png"
        data = src_path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"], key
        source = Image.open(src_path).convert("RGB")
        predictor.set_image(np.asarray(source))
        with torch.inference_mode():
            masks, scores, _ = predictor.predict(box=np.array(item["box"], dtype=float), multimask_output=True)
        idx = int(np.argmax(scores))
        candidate = cut(source, masks[idx])
        preview = out / f"{key}.png"
        candidate.save(preview)
        dest = birds / f"{key}.webp"
        write_plate(prepare(preview), dest)
        manifest[f"birds/{dest.name}"] = {"source": item["source_key"], "url": item["url"]}
        existing.add(key)
        shipped.append({**item, "sam_score": float(scores[idx]), "asset": dest.name, "license": "Public domain", "review_status": "visually approved exact-box segmentation"})

    for source_key in {r["source_key"] for r in shipped}:
        marker = f"Manifest key: `{source_key}`"
        if marker not in attribution:
            attribution = attribution.rstrip() + "\n\n" + additions[source_key] + "\n"

    manifest_path.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n")
    attr_path.write_text(attribution)
    (out / "asset-provenance.json").write_text(json.dumps(shipped, indent=2) + "\n")
    (out / "summary.json").write_text(json.dumps({"requested": len(ITEMS), "shipped": len(shipped), "skipped_existing": len(skipped), "skipped": skipped}, indent=2) + "\n")
    print((out / "summary.json").read_text())
    if not shipped:
        raise RuntimeError("no new source-gap artwork to publish")


if __name__ == "__main__":
    main()
