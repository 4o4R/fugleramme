#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--approved', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    candidates = args.candidates.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    import sys
    sys.path[:0] = [str(repo), str(repo / 'src')]
    from fugleramme.names import normalize
    from tools.add_bird import prepare, write_plate

    approved = json.loads(args.approved.read_text())
    ledger = json.loads((candidates / 'ledger.json').read_text())
    by_choice = {(r['common'], int(r['mask_index'])): r for r in ledger}
    manifest_path = repo / 'assets/artwork/classic/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    attr_path = repo / 'assets/artwork/classic/ATTRIBUTION.md'
    attribution = attr_path.read_text()
    birds = repo / 'assets/artwork/classic/birds'
    shipped = []

    for item in approved:
        row = by_choice[(item['common'], int(item['mask_index']))]
        key = row['key']
        assert normalize(row['scientific']) == key
        # Do not overwrite a species that landed upstream while this batch was reviewed.
        if any(p.exists() for p in [birds / f'{key}.webp', birds / f'{key}-2.webp']):
            print('Already covered upstream; skipping:', key, flush=True)
            continue
        source = candidates / row['preview']
        with Image.open(source) as image:
            assert image.mode == 'RGBA'
            assert image.getchannel('A').getextrema() == (0, 255)
        prepared = prepare(source)
        dest = birds / f'{key}.webp'
        write_plate(prepared, dest)
        manifest[f'birds/{dest.name}'] = {'source': item['source_key'], 'url': row['source_page']}
        shipped.append({
            'common': row['common'], 'scientific': row['scientific'], 'key': key,
            'asset': dest.name, 'source_key': item['source_key'], 'source_page': row['source_page'],
            'source_title': row['source_title'], 'artist': row['artist'], 'credit': row['credit'],
            'license': row['license'], 'mask_index': row['mask_index'], 'review_status': 'approved'
        })
        print('Added reviewed asset:', key, flush=True)

    if any(r['source_key'] == 'audubon-pittsburgh' for r in shipped) and '`audubon-pittsburgh`' not in attribution:
        attribution += "\n\n**Audubon (Pittsburgh)** - *The Birds of America* (1827-1838), by **John James Audubon**, engraved and coloured by **Robert Havell**. [University of Pittsburgh scans](https://digital.library.pitt.edu/collection/audubons-birds-america), via the Wikimedia Commons file pages linked in the manifest. Public-domain originals and scans (Commons PD-Art / PD-old); these cutouts retain source pixels without generative repainting. Manifest key: `audubon-pittsburgh`.\n"
    if any(r['source_key'] == 'sharpe-hirundinidae' for r in shipped) and '`sharpe-hirundinidae`' not in attribution:
        attribution += "\n\n**Sharpe (Hirundinidae)** - *A Monograph of the Hirundinidae, or Family of Swallows* by **Richard Bowdler Sharpe** and **Claude W. Wyatt** (1885-1894), from public-domain scans on Wikimedia Commons. Exact file pages are linked in the manifest. Manifest key: `sharpe-hirundinidae`.\n"

    manifest_path.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + '\n')
    attr_path.write_text(attribution)
    (output / 'asset-provenance.json').write_text(json.dumps(shipped, indent=2) + '\n')
    (output / 'summary.json').write_text(json.dumps({'approved_requested': len(approved), 'shipped': len(shipped), 'skipped_existing': len(approved)-len(shipped)}, indent=2) + '\n')
    if not shipped:
        raise RuntimeError('No new reviewed assets remained after upstream deduplication.')


if __name__ == '__main__':
    main()
