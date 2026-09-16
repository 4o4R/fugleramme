#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


def source_key(row: dict) -> str:
    artist = (row.get('artist') or '').strip()
    credit = (row.get('credit') or '').strip()
    license_name = (row.get('license') or '').strip()
    if artist == 'John James Audubon' and credit == 'University of Pittsburgh':
        return 'audubon-pittsburgh'
    raw = f'{artist}|{credit}|{license_name}'.encode()
    return 'commons-pd-' + hashlib.sha1(raw).hexdigest()[:10]


def attribution_block(key: str, row: dict) -> str:
    artist = (row.get('artist') or 'Unknown / not stated on Commons').strip()
    credit = (row.get('credit') or 'Wikimedia Commons file page').strip()
    license_name = (row.get('license') or 'Public domain').strip()
    if key == 'audubon-pittsburgh':
        return "\n\n**Audubon (Pittsburgh)** - *The Birds of America* (1827-1838), by **John James Audubon**, engraved and coloured by **Robert Havell**. [University of Pittsburgh scans](https://digital.library.pitt.edu/collection/audubons-birds-america), via the Wikimedia Commons file pages linked in the manifest. Public-domain originals and scans (Commons PD-Art / PD-old); these cutouts retain source pixels without generative repainting. Manifest key: `audubon-pittsburgh`.\n"
    return (
        f"\n\n**Wikimedia Commons public-domain source ({key})** - "
        f"artist/creator: **{artist}**. Source/scan credit: {credit}. "
        f"License status recorded by Commons: **{license_name}**. Exact file pages are linked per asset in `manifest.json`; "
        f"these cutouts retain source pixels without generative repainting. Manifest key: `{key}`.\n"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--approved', type=Path, required=True)
    p.add_argument('--batch0', type=Path, required=True)
    p.add_argument('--batch1', type=Path, required=True)
    p.add_argument('--batch2', type=Path, required=True)
    p.add_argument('--batch3', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    repo = a.repo.resolve(); output = a.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    batches = [a.batch0.resolve(), a.batch1.resolve(), a.batch2.resolve(), a.batch3.resolve()]

    import sys
    sys.path[:0] = [str(repo), str(repo / 'src')]
    from fugleramme.names import normalize
    from tools.add_bird import prepare, write_plate

    approved = {int(k): {int(x) for x in v} for k, v in json.loads(a.approved.read_text()).items()}
    manifests = []
    chosen = []
    for part, folder in enumerate(batches):
        ledger = json.loads((folder / 'ledger.json').read_text())
        by_rank = {int(r['rank']): r for r in ledger}
        missing = approved[part] - set(by_rank)
        if missing:
            raise RuntimeError(f'approved ranks missing from part {part}: {sorted(missing)}')
        for rank in sorted(approved[part]):
            row = by_rank[rank].copy(); row['_part'] = part
            chosen.append(row)

    if len(chosen) != 133:
        raise RuntimeError(f'expected 133 reviewed selections, got {len(chosen)}')

    mp = repo / 'assets/artwork/classic/manifest.json'; manifest = json.loads(mp.read_text())
    ap = repo / 'assets/artwork/classic/ATTRIBUTION.md'; attribution = ap.read_text()
    birds = repo / 'assets/artwork/classic/birds'
    shipped = []; skipped = []
    attribution_examples = {}

    for row in sorted(chosen, key=lambda r: int(r['rank'])):
        key = row['key']
        if normalize(row['scientific']) != key:
            raise RuntimeError(f'taxonomy/key mismatch: {row["common"]} {row["scientific"]} -> {key}')
        existing = [p for p in birds.glob(f'{key}*.webp') if p.stem == key or p.stem.startswith(key + '-')]
        if existing:
            skipped.append({'common': row['common'], 'key': key, 'reason': 'already covered in current upstream'})
            print('Already covered upstream; skipping:', key, flush=True)
            continue
        source = batches[row['_part']] / row['preview']
        if not source.exists():
            raise FileNotFoundError(source)
        with Image.open(source) as im:
            if im.mode != 'RGBA' or im.getchannel('A').getextrema() != (0, 255):
                raise RuntimeError(f'bad alpha candidate: {source}')
        skey = source_key(row)
        dest = birds / f'{key}.webp'
        write_plate(prepare(source), dest)
        manifest[f'birds/{dest.name}'] = {'source': skey, 'url': row['source_page']}
        attribution_examples.setdefault(skey, row)
        shipped.append({
            'rank': int(row['rank']), 'common': row['common'], 'scientific': row['scientific'], 'key': key,
            'asset': dest.name, 'source_key': skey, 'source_page': row['source_page'], 'source_title': row['source_title'],
            'artist': row.get('artist', ''), 'credit': row.get('credit', ''), 'license': row.get('license', ''),
            'auto_score': row.get('auto_score'), 'part': row['_part'], 'review_status': 'visually screened contact-sheet candidate',
        })
        print('Added screened asset:', row['rank'], key, flush=True)

    for skey, row in sorted(attribution_examples.items()):
        if f'`{skey}`' not in attribution:
            attribution += attribution_block(skey, row)

    mp.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + '\n')
    ap.write_text(attribution)
    (output / 'asset-provenance.json').write_text(json.dumps(shipped, indent=2) + '\n')
    (output / 'skipped-existing.json').write_text(json.dumps(skipped, indent=2) + '\n')
    (output / 'summary.json').write_text(json.dumps({
        'approved_requested': len(chosen), 'shipped': len(shipped), 'skipped_existing': len(skipped),
        'unique_source_groups': len({r['source_key'] for r in shipped}),
    }, indent=2) + '\n')
    if not shipped:
        raise RuntimeError('No new assets remained after upstream deduplication.')

if __name__ == '__main__':
    main()
