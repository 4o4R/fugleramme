#!/usr/bin/env python3
# .texas-curation/collect.py
# Fork-only curation: discover sources and make REVIEW drafts, never shipping assets.
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import time
import unicodedata
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.texas-curation'
OUT = WORK / 'output'
CACHE = WORK / 'cache'
BASE = '2688e4133d3039b7e9fe713e2830147a5e9e3366'
RAW = 'https://raw.githubusercontent.com/'
CATALOG = RAW + 'nathanbuchar/audubon-bird-plates/master/data.json'
PAPER = (240, 236, 229)

# This is a source-discovery crosswalk, not approval to depict a modern taxon.
# Ambiguous historical names (e.g. Traill's Flycatcher) are deliberately absent.
PLATES = {
    'Northern Cardinal': 159, 'Northern Mockingbird': 21,
    'Mourning Dove': 17, 'Carolina Chickadee': 160,
    'Carolina Wren': 78, "Bewick's Wren": 18, 'Tufted Titmouse': 39,
    'Common Grackle': 7, 'Brown-headed Cowbird': 99,
    'Red-winged Blackbird': 67, 'Cedar Waxwing': 43,
    'Eastern Bluebird': 113, 'Black Vulture': 106, 'Turkey Vulture': 151,
    'Eastern Screech-Owl': 97, 'Chimney Swift': 158,
    'Eastern Kingbird': 79, 'Indigo Bunting': 74, 'Painted Bunting': 53,
    'Summer Tanager': 44, 'Scarlet Tanager': 354,
    'Eastern Towhee': 29, 'Brown Thrasher': 116,
    'Northern Flicker': 37, 'Belted Kingfisher': 77,
    'Bald Eagle': 31, 'Peregrine Falcon': 16,
    'American Kestrel': 142, 'Osprey': 81,
    'Gray Catbird': 128, 'Northern Parula': 15,
    'Yellow-breasted Chat': 137, 'Blue-winged Warbler': 20,
    'Black-and-white Warbler': 90, 'Eastern Phoebe': 120,
    'Northern Bobwhite': 76, 'Northern Harrier': 356,
    'Snowy Egret': 242, 'Green Heron': 333,
    'Yellow-crowned Night Heron': 336,
}

# Preserve the actual label first. Only use these explicit spelling/common-name
# alternatives when exact matching fails. Never fuzzy-match a species identity.
ALTERNATIVES = {
    'Graylag Goose': ['Graylag Goose', 'Greylag Goose'],
    "Lilian's Lovebird": ["Lilian's Lovebird", "Lilian's Lovebird (Nyasa)"],
    'Red-lored Amazon': ['Red-lored Parrot'],
    'Cattle Egret': ['Cattle Egret', 'Western Cattle Egret'],
    'Blue-throated Mountain-gem': ['Blue-throated Mountain-gem', 'Blue-throated Hummingbird'],
    "McCown's Longspur": ['Thick-billed Longspur'],
}
# A split cannot be repaired by silently mapping a bird to the wrong sibling.
AMBIGUOUS = {'Western Flycatcher': 'Resolve Empidonax difficilis/occidentalis model taxonomy before shipping.',
             'Vega Gull': 'Resolve Larus vegae versus older Herring Gull label before shipping.',
             'Mew Gull': 'Historical broad name; do not silently substitute Common Gull for Short-billed Gull.',
             "Thayer's Gull": 'Historical split/lump; require an explicit taxonomy decision.'}
REJECT = {
    'zenaida-asiatica.webp': 'Wrong subject: Heilmann fig140 is a multi-species anatomy diagram.',
    'myioborus-pictus.webp': 'Mixed-species Bird-Lore plate is not an isolated Painted Redstart.',
    'zenaida-macroura.webp': 'Initial segmentation lost most of the plate and is not an acceptable bird cutout.',
}


def norm(text: str) -> str:
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', text.lower())


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or ['status'])
        writer.writeheader()
        writer.writerows(rows)


class FetchFailure(RuntimeError):
    pass


class Fetcher:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'Fugleramme-Texas-Curation/2.0 (https://github.com/4o4R/fugleramme)'
        retry = Retry(total=4, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504], respect_retry_after_header=True)
        self.session.mount('https://', HTTPAdapter(max_retries=retry))
        self.last = 0.0

    def get(self, url: str) -> bytes:
        key = hashlib.sha256(url.encode()).hexdigest()
        path = CACHE / key
        if path.exists():
            return path.read_bytes()
        # Sequential, at most one new request per second; no search fan-out.
        time.sleep(max(0.0, 1.0 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        try:
            response = self.session.get(url, timeout=(15, 60))
            response.raise_for_status()
        except requests.RequestException as error:
            raise FetchFailure(f'{url}: {error}') from error
        data = response.content
        if not data:
            raise FetchFailure(f'{url}: empty response')
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_bytes(data)
        tmp.replace(path)
        return data


def taxonomy(names: list[str]) -> list[dict]:
    aliases = json.loads((ROOT / 'assets/birdnet_aliases.json').read_text())
    lookup: dict[str, list[tuple[str, str]]] = {}
    for line in (ROOT / 'assets/birdnet_labels_v2.4.txt').read_text().splitlines():
        if '_' in line:
            sci, common = line.split('_', 1)
            lookup.setdefault(norm(common), []).append((sci, common))
    rows = []
    for rank, requested in enumerate(names, 1):
        matches = lookup.get(norm(requested), [])
        method = 'exact common name'
        if not matches:
            for alternative in ALTERNATIVES.get(requested, []):
                matches = lookup.get(norm(alternative), [])
                if matches:
                    method = 'explicit spelling/common-name alternative'
                    break
        row = {'rank': rank, 'requested': requested, 'taxonomy_status': 'unresolved',
               'scientific': '', 'model_scientific': '', 'model_common': '', 'key': '',
               'note': AMBIGUOUS.get(requested, '')}
        if len(matches) == 1 and requested not in AMBIGUOUS:
            sci, common = matches[0]
            current = aliases.get(sci, sci)
            row.update(taxonomy_status=method, scientific=current, model_scientific=sci,
                       model_common=common, key=current.lower().replace(' ', '-'))
        if requested == 'Ivory-billed Woodpecker':
            row['note'] = 'Historical/conservation-status case; not ordinary present-day local coverage.'
        rows.append(row)
    assert len(rows) == len(names) == 534
    return rows


def contacts(items: list[dict], path: Path, field: str = 'source_local') -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype('DejaVuSans.ttf', 16)
    except OSError:
        font = ImageFont.load_default()
    for offset in range(0, len(items), 20):
        canvas = Image.new('RGB', (1440, 1450), PAPER)
        draw = ImageDraw.Draw(canvas)
        for index, row in enumerate(items[offset:offset + 20]):
            x, y = (index % 5) * 288, (index // 5) * 350
            image = Image.open(OUT / row[field]).convert('RGBA')
            image.thumbnail((264, 285), Image.Resampling.LANCZOS)
            canvas.paste(image, (x + (288 - image.width) // 2, y + 10), image)
            draw.text((x + 8, y + 300), f"{row['rank']}. {row['requested']}"[:34], font=font, fill='black')
            draw.text((x + 8, y + 322), f"Plate {row.get('plate', '')} - REVIEW", font=font, fill='black')
        canvas.save(path / f'page-{offset // 20 + 1:02d}.jpg', quality=90)


def quarantine_initial() -> None:
    classic = ROOT / 'assets/artwork/classic'
    manifest_path = classic / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    old = list(csv.DictReader((ROOT / '.texas-build-reports/source_ledger.csv').open()))
    notes = []
    for row in old:
        if row.get('status') != 'generated':
            continue
        filename = row['asset']
        src = classic / 'birds' / filename
        dest = WORK / 'quarantine' / filename
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dest)
        manifest.pop('birds/' + filename, None)
        notes.append({'file': filename, 'status': 'rejected' if filename in REJECT else 'needs visual recut',
                      'reason': REJECT.get(filename, 'Original automated whole-plate cutout was never individually approved.'),
                      'source_url': row.get('url', '')})
    write_json(manifest_path, dict(sorted(manifest.items())))
    write_csv(WORK / 'initial-review.csv', notes)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    names = [s.strip() for s in (ROOT / '.texas-build-reports/texas_species.txt').read_text().splitlines() if s.strip()]
    rows = taxonomy(names)
    fetch = Fetcher()
    catalog_bytes = fetch.get(CATALOG)
    catalog = json.loads(catalog_bytes)
    assert len(catalog) == 435
    write_json(OUT / 'audubon-catalog.json', catalog)
    write_json(OUT / 'catalog-provenance.json', {'url': CATALOG, 'sha256': hashlib.sha256(catalog_bytes).hexdigest(),
                                               'notice': 'Discovery index only. Catalog membership is not a license or species approval.'})
    by_number = {p['plate']: p for p in catalog}
    by_name: dict[str, list[dict]] = {}
    for plate in catalog:
        by_name.setdefault(norm(plate['name']), []).append(plate)
    base_manifest = json.loads(fetch.get(RAW + 'arnegiacomo/fugleramme/' + BASE + '/assets/artwork/classic/manifest.json'))
    base_keys = {re.sub(r'-\d+$', '', Path(k).stem) for k in base_manifest if k.startswith('birds/')}
    labels_path = OUT / 'model-labels.txt'
    shutil.copy2(ROOT / 'assets/birdnet_labels_v2.4.txt', labels_path)
    shutil.copy2(ROOT / 'assets/birdnet_aliases.json', OUT / 'model-aliases.json')
    obtained = []
    failures = []
    # Collect exact-title and explicit historical-title candidates. They remain
    # review-only even when the name is exact, because a plate can contain more
    # than one species. License, bird identity, and crop are separate approvals.
    for row in rows:
        key = row['key']
        row['asset_status'] = 'existing upstream' if key and key in base_keys else 'needs source'
        if row['taxonomy_status'] == 'unresolved':
            row['asset_status'] = 'taxonomy review'
        matches = by_name.get(norm(row['requested']), []) or by_name.get(norm(row['model_common']), [])
        plate = by_number.get(PLATES.get(row['requested']))
        match = 'historical-title candidate; verify on institution page' if plate else 'exact catalog title; still requires visual check'
        if not plate and len(matches) == 1:
            plate = matches[0]
        if not plate:
            continue
        row.update(plate=plate['plate'], historical_title=plate['name'], match_basis=match,
                   source_page='https://www.audubon.org/art/birds-of-america/' + plate['slug'],
                   institution_record=f"https://historicpittsburgh.org/islandora/object/pitt%3Aaud{plate['plate']:04d}",
                   license_status='unverified - check exact scan', crop_status='not reviewed')
        if row['asset_status'] == 'existing upstream':
            continue
        filename = plate['fileName']
        group = '0-99' if plate['plate'] < 100 else f"{plate['plate'] // 100 * 100}-{435 if plate['plate'] >= 400 else plate['plate'] // 100 * 100 + 99}"
        urls = ['https://media.audubon.org/boa_illustration/' + filename,
                RAW + 'nathanbuchar/audubon-bird-plates/master/plates/' + group + '/' + filename,
                plate['download']]
        errors = []
        for url in urls:
            try:
                data = fetch.get(url)
                import io
                image = Image.open(io.BytesIO(data)).convert('RGB')
                image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                local = f"sources/{plate['plate']:03d}.jpg"
                (OUT / 'sources').mkdir(exist_ok=True)
                image.save(OUT / local, quality=95)
                row.update(source_local=local, source_download=url, source_sha256=hashlib.sha256(data).hexdigest())
                row['asset_status'] = 'source downloaded - NOT a finished asset'
                obtained.append(row)
                break
            except (FetchFailure, OSError) as error:
                errors.append(str(error))
        else:
            row['asset_status'] = 'source download failed'
            failures.append({'rank': row['rank'], 'requested': row['requested'], 'errors': ' | '.join(errors)})
        if len(obtained) % 10 == 0:
            print(f"Fetched {len(obtained)} source plates; at row {row['rank']}", flush=True)
        write_csv(OUT / 'inventory.csv', rows)
    quarantine_initial()
    write_csv(OUT / 'inventory.csv', rows)
    write_csv(OUT / 'fetch-errors.csv', failures)
    contacts(obtained, OUT / 'source-contact-sheets')
    summary = {'requested_rows': len(rows), 'resolved_rows': sum(bool(r['key']) for r in rows),
               'unique_resolved_keys': len({r['key'] for r in rows if r['key']}),
               'unresolved_rows': sum(not r['key'] for r in rows),
               'existing_upstream_rows': sum(r['asset_status'] == 'existing upstream' for r in rows),
               'catalog_candidate_rows': sum('plate' in r for r in rows),
               'downloaded_source_rows': len(obtained), 'download_errors': len(failures),
               'new_approved_assets': 0, 'quarantined_initial_assets': 10,
               'scope': 'Broad Central Texas and Harris County planning list, not 534 common ZIP-code birds or a local abundance ranking.'}
    write_json(OUT / 'summary.json', summary)
    (OUT / 'README.md').write_text('# Texas artwork source inventory\n\nSources are NOT finished assets.\n\nEach row retains its requested name, exact model name, canonical key, matching basis, download status and original plate URL. Ambiguous taxa, license checks and visual crops remain explicit review items.\n\nThe first ten automated cutouts were quarantined after visual inspection found wrong subjects and damaged/whole-scene cutouts. The original build summary is historical and must not be read as current readiness.\n\nThese fork-only curation files are not intended to be part of the upstream artwork-only PR.\n', encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)
    if not obtained:
        raise RuntimeError('No sources downloaded; this is a failed acquisition run, not missing bird coverage.')


if __name__ == '__main__':
    main()
