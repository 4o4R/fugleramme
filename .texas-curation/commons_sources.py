#!/usr/bin/env python3
# .texas-curation/commons_sources.py
# Fork-only acquisition. Fetch independent public-domain scans, not Audubon.org's
# restricted digital files. Nothing downloaded here is automatically approved art.
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.texas-curation'
OUT = WORK / 'pd-sources'
API = 'https://commons.wikimedia.org/w/api.php'
spec = importlib.util.spec_from_file_location('collection_helpers', WORK / 'collect.py')
assert spec and spec.loader
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def plain(value: object) -> str:
    return re.sub(r'<[^>]+>', ' ', str(value)).strip()


def field(metadata: dict, name: str) -> str:
    return plain(metadata.get(name, {}).get('value', ''))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fetch = helpers.Fetcher()
    inventory = list(csv.DictReader((WORK / 'output/inventory.csv').open(encoding='utf-8')))
    wanted = {int(r['plate']) for r in inventory if r.get('plate') and r['asset_status'] != 'existing upstream'}
    pages = []
    continuation = {}
    # Enumerate one collection, not six full-text searches per species. Keep full
    # license metadata and revision evidence. A transport/API error fails the run.
    for batch in range(30):
        params = {'action': 'query', 'generator': 'categorymembers',
                  'gcmtitle': 'Category:The Birds of America', 'gcmtype': 'file',
                  'gcmlimit': '50', 'prop': 'imageinfo',
                  'iiprop': 'url|size|sha1|extmetadata', 'iiurlwidth': '1800',
                  'format': 'json', 'formatversion': '2', **continuation}
        response = json.loads(fetch.get(API + '?' + urlencode(params)))
        if response.get('error'):
            raise RuntimeError(json.dumps(response['error']))
        pages.extend(response.get('query', {}).get('pages', []))
        continuation = response.get('continue', {})
        print(f'Collection batch {batch + 1}: {len(pages)} files', flush=True)
        if not continuation:
            break
    else:
        raise RuntimeError('Collection pagination did not finish; inventory is incomplete.')
    helpers.write_json(OUT / 'commons-metadata.json', pages)
    candidates: dict[int, list[dict]] = {}
    for page in pages:
        match = re.match(r'^File:(\d{1,3})[ _]', page.get('title', ''))
        if not match:
            continue
        plate = int(match.group(1))
        if plate not in wanted:
            continue
        info = (page.get('imageinfo') or [{}])[0]
        metadata = info.get('extmetadata', {})
        license_name = field(metadata, 'LicenseShortName')
        # Match the LICENSE field, never a word elsewhere in an image description.
        if license_name.lower() not in {'public domain', 'cc0', 'cc0 1.0', 'pdm', 'public domain mark'}:
            continue
        # Pittsburgh originals avoid silently relabeling an Audubon.org mirror.
        origin = ' '.join(field(metadata, k) for k in ('Credit', 'Source', 'ImageDescription'))
        if 'pittsburgh' not in origin.lower() and 'pitt.edu' not in origin.lower():
            continue
        if info.get('mime', 'image/jpeg') not in ('image/jpeg', 'image/png'):
            continue
        candidates.setdefault(plate, []).append(page)
    ledger = []
    for index, plate in enumerate(sorted(wanted), 1):
        choices = candidates.get(plate, [])
        if not choices:
            ledger.append({'plate': plate, 'status': 'no verified Pittsburgh file in direct collection', 'source_local': ''})
            continue
        page = max(choices, key=lambda p: p['imageinfo'][0].get('width', 0))
        info = page['imageinfo'][0]
        metadata = info.get('extmetadata', {})
        url = info.get('thumburl') or info['url']
        data = fetch.get(url)
        image = Image.open(io.BytesIO(data)).convert('RGB')
        image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
        path = OUT / 'sources' / f'{plate:03d}.png'
        path.parent.mkdir(exist_ok=True)
        image.save(path)
        ledger.append({'plate': plate, 'status': 'downloaded public-domain source; crop unreviewed',
                       'title': page['title'], 'pageid': page['pageid'],
                       'source_page': info.get('descriptionurl', ''), 'download_url': url,
                       'original_url': info['url'], 'original_sha1': info.get('sha1', ''),
                       'download_sha256': hashlib.sha256(data).hexdigest(),
                       'normalized_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                       'license': field(metadata, 'LicenseShortName'),
                       'license_url': field(metadata, 'LicenseUrl'),
                       'artist': field(metadata, 'Artist'), 'credit': field(metadata, 'Credit'),
                       'source_local': str(path.relative_to(OUT)),
                       'width': image.width, 'height': image.height})
        helpers.write_csv(OUT / 'sources.csv', ledger)
        if index % 10 == 0:
            print(f'Processed {index}/{len(wanted)} requested plates', flush=True)
    helpers.write_csv(OUT / 'sources.csv', ledger)
    got = sum(bool(r.get('source_local')) for r in ledger)
    helpers.write_json(OUT / 'summary.json', {'requested_plates': len(wanted), 'collection_files': len(pages),
                       'downloaded_pd_plates': got, 'missing_from_collection': len(wanted) - got,
                       'new_approved_assets': 0})
    # Keep rights guidance in the committed audit; raw scans stay in the artifact.
    (OUT / 'README.md').write_text('# Public-domain source scans\n\nThese are independent University of Pittsburgh scans distributed on Wikimedia Commons. Each source was accepted only from its explicit license field plus institutional origin. `sources.csv` records exact pages, image hashes and license metadata. Source acquisition is not visual crop approval.\n\nAudubon.org is used for historical-to-modern identity research only. Its website explicitly restricts its own digital files to non-commercial educational use: https://www.audubon.org/terms-use . Those files must not be promoted into an openly reusable contribution merely because the original artwork is public domain.\n', encoding='utf-8')
    print(f'Downloaded {got} independently licensed plates', flush=True)
    if got == 0:
        raise RuntimeError('No independently licensed scans obtained; do not report success.')


if __name__ == '__main__':
    main()
