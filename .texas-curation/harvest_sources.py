#!/usr/bin/env python3
# .texas-curation/harvest_sources.py
# Fork-only source acquisition. No candidate is promoted to shipping artwork.
from __future__ import annotations

import csv
import hashlib
import html
import importlib.util
import io
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.texas-curation'
OUT = WORK / 'harvest'
API = 'https://commons.wikimedia.org/w/api.php'
spec = importlib.util.spec_from_file_location('helpers', WORK / 'collect.py')
assert spec and spec.loader
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)

# Research names ONLY. These do not change the pinned classifier or alias table.
RESEARCH_NAMES = {
    'White-winged Scoter': ['Melanitta deglandi'],
    'Masked Duck': ['Nomonyx dominicus'],
    "Lilian's Lovebird": ['Agapornis lilianae'],
    'Western Flycatcher': ['Empidonax difficilis', 'Empidonax occidentalis'],
    'Mew Gull': ['Larus canus', 'Larus brachyrhynchus'],
    "Thayer's Gull": ['Larus thayeri', 'Larus glaucoides'],
    'Vega Gull': ['Larus vegae'],
    'Ivory-billed Woodpecker': ['Campephilus principalis'],
}
LICENSES = {'public domain', 'cc0', 'cc0 1.0', 'pdm', 'public domain mark'}
HISTORICAL = re.compile(r'audubon|gould|keulemans|ridgway|fuertes|wilson|thorburn|gr.nvold|edwards|dresser|morris|jardine|swainson|lydon|naumann|elliot|smit|levaillant|barraband|baird|catesby|biodiversity|bhl|internet archive book', re.I)
REJECT_TITLE = re.compile(r'crossley|distribution|range.?map|skeleton|skull|anatom|eggs?\b|map\.|heilmann|fig140|topaz|icon\b|logo\b', re.I)


def plain(value: object) -> str:
    return html.unescape(re.sub(r'<[^>]+>', ' ', str(value))).strip()


def field(page: dict, name: str) -> str:
    info = (page.get('imageinfo') or [{}])[0]
    return plain(info.get('extmetadata', {}).get(name, {}).get('value', ''))


def scientific_names(row: dict, aliases: dict) -> list[str]:
    names = {row.get('scientific', ''), row.get('model_scientific', '')}
    names.update(RESEARCH_NAMES.get(row['requested'], []))
    names.discard('')
    names.update(old for old, new in aliases.items() if new in names)
    return sorted(names)


def exact_category(page: dict, names: list[str]) -> bool:
    categories = field(page, 'Categories').split('|')
    return any(c == name or c.startswith(name + ' (') or c.startswith(name + ' in ')
               for c in categories for name in names)


def eligible(page: dict) -> bool:
    info = (page.get('imageinfo') or [{}])[0]
    title = page.get('title', '')
    if REJECT_TITLE.search(title) or not title.lower().endswith(('.jpg', '.jpeg', '.png')):
        return False
    if field(page, 'LicenseShortName').lower() not in LICENSES:
        return False
    if min(info.get('width', 0), info.get('height', 0)) < 400:
        return False
    context = ' '.join(field(page, k) for k in ('Artist', 'Credit', 'Categories', 'ImageDescription'))
    # A PD photograph is not a historical plate. This is a discovery filter,
    # followed by independent visual review, not a final identity decision.
    if not (HISTORICAL.search(context) or '(illustrations)' in context):
        return False
    if 'John James Audubon Center at Mill Grove' in context or 'Zebra Publishing' in context:
        return False
    return True


def rank(page: dict, row: dict, names: list[str]) -> tuple[int, int]:
    score = 30 if exact_category(page, names) else 0
    title = page.get('title', '')
    info = page['imageinfo'][0]
    credit = field(page, 'Credit').lower()
    number = re.match(r'^File:(\d{1,3})[ _]', title)
    if 'pittsburgh' in credit or 'pitt.edu' in credit:
        score += 50
    if number:
        score += 10
        if row.get('plate') and int(number.group(1)) == int(row['plate']):
            score += 100
    if 'cropped' in title.lower():
        score -= 5
    if 'restor' in title.lower():
        score -= 8
    if 'illustrations' in field(page, 'Categories'):
        score += 10
    return score, min(info.get('width', 0), 12000)


def query(fetch: object, **extra: object) -> list[dict]:
    result = []
    continuation = {}
    for _ in range(4):
        params = {'action': 'query', 'prop': 'imageinfo',
                  'iiprop': 'url|size|sha1|extmetadata', 'iiurlwidth': 1800,
                  'format': 'json', 'formatversion': 2, **extra, **continuation}
        response = json.loads(fetch.get(API + '?' + urlencode(params)))
        if response.get('error'):
            raise RuntimeError(json.dumps(response['error']))
        result.extend(response.get('query', {}).get('pages', []))
        continuation = response.get('continue', {})
        if not continuation:
            break
    else:
        raise RuntimeError('Source query pagination incomplete; do not silently truncate.')
    return result


def download(fetch: object, page: dict) -> dict:
    info = page['imageinfo'][0]
    pageid = int(page['pageid'])
    part = pageid % 4
    path = OUT / f'sources-{part}' / f'{pageid}.png'
    url = info.get('thumburl') or info['url']
    data = fetch.get(url)
    image = Image.open(io.BytesIO(data)).convert('RGB')
    image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return {'pageid': pageid, 'source_local': str(path.relative_to(OUT)),
            'file_title': page['title'], 'source_page': info.get('descriptionurl', ''),
            'download_url': url, 'original_url': info.get('url', ''),
            'original_sha1': info.get('sha1', ''),
            'download_sha256': hashlib.sha256(data).hexdigest(),
            'normalized_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'license': field(page, 'LicenseShortName'), 'license_url': field(page, 'LicenseUrl'),
            'artist': field(page, 'Artist'), 'credit': field(page, 'Credit'),
            'date': field(page, 'DateTimeOriginal'),
            'width': image.width, 'height': image.height}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    inventory = list(csv.DictReader((WORK / 'output/inventory.csv').open(encoding='utf-8')))
    aliases = json.loads((ROOT / 'assets/birdnet_aliases.json').read_text())
    cached = json.loads((WORK / 'pd-sources/commons-metadata.json').read_text())
    fetch = helpers.Fetcher()
    downloaded = {}
    choices = []
    all_candidates = []
    query_errors = []
    for row in inventory:
        if row['asset_status'] == 'existing upstream':
            continue
        names = scientific_names(row, aliases)
        candidates = {p['pageid']: p for p in cached if eligible(p) and exact_category(p, names)}
        basis = 'exact scientific category in the cached historical collection'
        # For the remaining taxa, query the illustration category before search.
        if not candidates:
            basis = 'exact illustration category or explicit scientific-name search; visual identity pending'
            for name in names:
                try:
                    pages = query(fetch, generator='categorymembers', gcmtitle=f'Category:{name} (illustrations)',
                                  gcmtype='file', gcmlimit=50)
                    for page in pages:
                        if eligible(page):
                            candidates[page['pageid']] = page
                except Exception as error:
                    query_errors.append({'requested': row['requested'], 'query': name, 'error': str(error)})
            if not candidates:
                # Search is discovery only. Retain matching evidence and metadata
                # so text mentioning a bird cannot masquerade as an approved plate.
                for name in names[:2]:
                    try:
                        pages = query(fetch, generator='search', gsrnamespace=6, gsrlimit=30,
                                      gsrsearch=f'"{name}" (illustration OR Gould OR Audubon OR Keulemans OR BHL) -Crossley -filetype:pdf -filetype:djvu')
                        for page in pages:
                            if eligible(page):
                                candidates[page['pageid']] = page
                    except Exception as error:
                        query_errors.append({'requested': row['requested'], 'query': name, 'error': str(error)})
        ordered = sorted(candidates.values(), key=lambda p: rank(p, row, names), reverse=True)
        record = {'rank': int(row['rank']), 'requested': row['requested'], 'key': row['key'],
                  'scientific': row['scientific'], 'research_names': names,
                  'taxonomy_status': row['taxonomy_status'], 'match_basis': basis,
                  'candidate_count': len(ordered), 'status': 'no eligible source found', 'sources': []}
        # One best Audubon plate is sufficient for the first review. Download two
        # alternatives from new searches, because exact categories can contain
        # mixed plates and scans that are unsuitable for clean individual cuts.
        limit = 1 if basis.startswith('exact scientific category') else 2
        for page in ordered:
            all_candidates.append({'rank': record['rank'], 'requested': row['requested'],
                                   'key': row['key'], 'match_basis': basis, 'metadata': page})
        for page in ordered[:limit]:
            try:
                pageid = page['pageid']
                if pageid not in downloaded:
                    downloaded[pageid] = download(fetch, page)
                record['sources'].append(downloaded[pageid])
                record['status'] = 'source available; identity and cutout NOT approved'
            except Exception as error:
                query_errors.append({'requested': row['requested'], 'query': page['title'], 'error': str(error)})
                record['status'] = 'source download failed'
        choices.append(record)
        helpers.write_json(OUT / 'choices.json', choices)
        helpers.write_json(OUT / 'candidate-metadata.json', all_candidates)
        helpers.write_json(OUT / 'downloaded-sources.json', list(downloaded.values()))
        helpers.write_csv(OUT / 'errors.csv', query_errors)
        if len(choices) % 10 == 0:
            print(f'Researched {len(choices)} / 430 worklist gaps; downloaded {len(downloaded)} source images', flush=True)
    summary = {'requested_rows': 534, 'existing_rows': 104,
               'researched_rows': len(choices), 'unique_downloaded_sources': len(downloaded),
               'rows_with_source': sum(bool(r['sources']) for r in choices),
               'rows_without_source': sum(not r['sources'] for r in choices),
               'errors': len(query_errors), 'approved_artwork': 0,
               'statuses': dict(Counter(r['status'] for r in choices))}
    helpers.write_json(OUT / 'summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)
    if not downloaded:
        raise RuntimeError('Acquisition failed; no downloaded sources.')


if __name__ == '__main__':
    main()
