#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

ATTRIBUTIONS = {
    "audubon-octavo": "**Audubon (octavo edition)** - *The Birds of America, from Drawings Made in the United States and Their Territories* by **John James Audubon** (octavo edition, 1840-1844), lithographed principally by **J. T. Bowen**, from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `audubon-octavo`.",
    "martinet-planches": "**Martinet / Planches enluminées** - plates from *Planches enluminées d'histoire naturelle* associated with **Georges-Louis Leclerc de Buffon**, **Edme-Louis Daubenton**, and illustrator **François-Nicolas Martinet** (18th century), from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `martinet-planches`.",
    "cassin-illustrations": "**Cassin (western North America)** - *Illustrations of the Birds of California, Texas, Oregon, British and Russian America* by **John Cassin** (1850s), with lithographic work including **John T. Bowen**, from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `cassin-illustrations`.",
    "swainson-brazil-mexico": "**Swainson (Brazil and Mexico)** - *A Selection of the Birds of Brazil and Mexico* by **William Swainson** (1841), from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `swainson-brazil-mexico`.",
    "studer-birds-na": "**Studer (Birds of North America)** - *The Birds of North America* associated with **Jacob H. Studer**, with plates credited on the linked source record, from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `studer-birds-na`.",
    "keulemans-ibis": "**Keulemans (The Ibis)** - bird plates by **John Gerrard Keulemans** published in *The Ibis*, from public-domain scans on Wikimedia Commons. Exact file pages are linked in the manifest. Public domain. Manifest key: `keulemans-ibis`.",
    "lear-parrots": "**Lear (Parrots)** - parrot illustrations by **Edward Lear**, from his 19th-century ornithological work including *Illustrations of the Family of Psittacidae, or Parrots*, via the exact Wikimedia Commons file page linked in the manifest. Public domain. Manifest key: `lear-parrots`.",
    "knight-birds-world": "**Birds of the World for Young People** - plate from the public-domain work *Birds of the World for Young People*, with artist credits recorded on the linked Wikimedia Commons/Biodiversity Heritage Library source page. Public domain. Manifest key: `knight-birds-world`.",
    "chapman-color-key": "**Chapman / Reed (Color Key)** - *Color Key to North American Birds* by **Frank M. Chapman**, with illustrations credited in the source to **Chester A. Reed** and others (early 20th century), from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `chapman-color-key`.",
    "seligmann-recueil": "**Seligmann / Edwards (Recueil)** - plates from *Recueil de divers oiseaux étrangers et peu communs*, associated with **George Edwards** and **Johann Michael Seligmann** (18th century), from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `seligmann-recueil`.",
    "eaton-birds-ny": "**Eaton (Birds of New York)** - *Birds of New York* by **Elon Howard Eaton** (early 20th century), from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `eaton-birds-ny`.",
    "bailey-handbook": "**Bailey (western U.S.)** - *Handbook of Birds of the Western United States* by **Florence Merriam Bailey** (1902), from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `bailey-handbook`.",
    "carlsbad-cavern": "**Carlsbad Cavern survey** - bird illustration from *Animal Life of the Carlsbad Cavern* (1928), from an Internet Archive scan made available through Wikimedia Commons. Public domain. Manifest key: `carlsbad-cavern`.",
    "pretre-magasin": "**Prêtre (Magasin de Zoologie)** - 19th-century bird plate by **Jean-Gabriel Prêtre** from *Magasin de Zoologie*, via the exact Wikimedia Commons file page linked in the manifest. Public domain. Manifest key: `pretre-magasin`.",
    "smit-nga": "**Smit (National Gallery of Art)** - bird illustration by **Joseph Smit**, released through the **National Gallery of Art Open Access** program and supplied on Wikimedia Commons under **CC0**. Exact file page is linked in the manifest. Manifest key: `smit-nga`.",
    "gosse-jamaica": "**Gosse (Jamaica)** - *Illustrations of the Birds of Jamaica* by **Philip Henry Gosse** (1849), from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `gosse-jamaica`.",
    "miller-second-book": "**Miller (Second Book of Birds)** - *The Second Book of Birds* by **Harriet Mann Miller** (Olive Thorne Miller), from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `miller-second-book`.",
    "pearson-scott-foresman": "**Pearson Scott Foresman** - educational bird illustration from the **Pearson Scott Foresman** archive donated to Wikimedia Commons and marked public domain there. Exact file page is linked in the manifest. Manifest key: `pearson-scott-foresman`.",
    "gronvold-south-america": "**Grønvold (Birds of South America)** - plate by **Henrik Grønvold** from *The Birds of South America*, from Biodiversity Heritage Library scans on Wikimedia Commons. Public domain. Manifest key: `gronvold-south-america`.",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--approved", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.resolve()
    inputs = args.inputs.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    import sys
    sys.path[:0] = [str(repo), str(repo / "src")]
    from fugleramme.names import normalize
    from tools.add_bird import prepare, write_plate

    approved = json.loads(args.approved.read_text())
    ledgers: dict[int, dict[int, dict]] = {}
    for part in range(4):
        rows = json.loads((inputs / str(part) / "ledger.json").read_text())
        ledgers[part] = {int(row["rank"]): row for row in rows}

    manifest_path = repo / "assets/artwork/classic/manifest.json"
    attr_path = repo / "assets/artwork/classic/ATTRIBUTION.md"
    birds = repo / "assets/artwork/classic/birds"
    manifest = json.loads(manifest_path.read_text())
    attribution = attr_path.read_text()
    shipped: list[dict] = []
    skipped: list[dict] = []

    for item in approved:
        row = ledgers[int(item["part"])][int(item["rank"])]
        assert row["key"] == item["key"], (row["key"], item["key"])
        assert normalize(row["scientific"]) == row["key"]
        assert row["license"] in {"Public domain", "CC0"}, row["license"]
        key = row["key"]
        if any((birds / f"{key}{suffix}").exists() for suffix in [".webp", "-2.webp", ".png", ".jpg", ".jpeg"]):
            skipped.append({"key": key, "reason": "already covered on current main"})
            continue

        source = inputs / str(item["part"]) / row["preview"]
        with Image.open(source) as image:
            assert image.mode == "RGBA"
            lo, hi = image.getchannel("A").getextrema()
            assert lo == 0 and hi == 255, (key, (lo, hi))
        prepared = prepare(source)
        dest = birds / f"{key}.webp"
        write_plate(prepared, dest)
        manifest[f"birds/{dest.name}"] = {
            "source": item["source_key"],
            "url": row["source_page"],
        }
        shipped.append({
            "rank": row["rank"], "common": row["common"], "scientific": row["scientific"],
            "key": key, "asset": dest.name, "source_key": item["source_key"],
            "source_page": row["source_page"], "source_title": row["source_title"],
            "artist": row["artist"], "credit": row["credit"], "license": row["license"],
            "preview_sha256": row["preview_sha256"], "review_status": "visually approved",
        })

    used = {row["source_key"] for row in shipped}
    for source_key in sorted(used):
        marker = f"Manifest key: `{source_key}`"
        if marker in attribution:
            continue
        paragraph = ATTRIBUTIONS.get(source_key)
        if paragraph is None:
            raise RuntimeError(f"No attribution text for new source key {source_key}")
        attribution = attribution.rstrip() + "\n\n" + paragraph + "\n"

    manifest_path.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n")
    attr_path.write_text(attribution)
    (output / "asset-provenance.json").write_text(json.dumps(shipped, indent=2) + "\n")
    (output / "summary.json").write_text(json.dumps({
        "approved_requested": len(approved),
        "shipped": len(shipped),
        "skipped_existing": len(skipped),
        "skipped": skipped,
    }, indent=2) + "\n")
    print((output / "summary.json").read_text())
    if not shipped:
        raise RuntimeError("No new approved assets remained after current-main deduplication")


if __name__ == "__main__":
    main()
