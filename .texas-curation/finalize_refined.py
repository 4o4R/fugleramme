#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

ATTRIBUTION = {
    'audubon-pittsburgh': "\n\n**Audubon (Pittsburgh)** - *The Birds of America* (1827-1838), by **John James Audubon**, engraved and coloured by **Robert Havell**. [University of Pittsburgh scans](https://digital.library.pitt.edu/collection/audubons-birds-america), via the Wikimedia Commons file pages linked in the manifest. Public-domain originals and scans (Commons PD-Art / PD-old); these cutouts retain source pixels without generative repainting. Manifest key: `audubon-pittsburgh`.\n",
    'gould-trochilidae': "\n\n**Gould (Trochilidae)** - *A Monograph of the Trochilidae, or Family of Humming-Birds* by **John Gould**, with plates principally by **H. C. Richter** (1849-1861), from public-domain scans on Wikimedia Commons. Exact file pages are linked in the manifest. Manifest key: `gould-trochilidae`.\n",
    'emory-boundary-birds': "\n\n**Emory boundary survey (Birds)** - bird plates from *Report on the United States and Mexican Boundary Survey*, made under the direction of the U.S. Secretary of the Interior, bird volume edited by **Spencer Fullerton Baird** (1850s), via public-domain U.S. Department of the Interior scans on Wikimedia Commons. Exact file pages are linked in the manifest. Manifest key: `emory-boundary-birds`.\n",
}


def main() -> None:
    p=argparse.ArgumentParser()
    for name in ['repo','candidates','approved','output']:
        p.add_argument('--'+name, required=True, type=Path)
    a=p.parse_args(); repo=a.repo.resolve(); candidates=a.candidates.resolve(); output=a.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    import sys
    sys.path[:0]=[str(repo),str(repo/'src')]
    from fugleramme.names import normalize
    from tools.add_bird import prepare, write_plate
    approved=json.loads(a.approved.read_text())
    ledger=json.loads((candidates/'ledger.json').read_text())
    by={(r['common'],int(r['mask_index'])):r for r in ledger}
    mp=repo/'assets/artwork/classic/manifest.json'; manifest=json.loads(mp.read_text())
    ap=repo/'assets/artwork/classic/ATTRIBUTION.md'; attr=ap.read_text()
    birds=repo/'assets/artwork/classic/birds'; shipped=[]
    for item in approved:
        row=by[(item['common'],int(item['mask_index']))]; key=row['key']; assert normalize(row['scientific'])==key
        if any((birds/n).exists() for n in [f'{key}.webp',f'{key}-2.webp']):
            print('Already covered upstream; skipping:',key,flush=True); continue
        source=candidates/row['preview']
        with Image.open(source) as im:
            assert im.mode=='RGBA' and im.getchannel('A').getextrema()==(0,255)
        dest=birds/f'{key}.webp'; write_plate(prepare(source),dest)
        manifest[f'birds/{dest.name}']={'source':item['source_key'],'url':row['source_page']}
        shipped.append({'common':row['common'],'scientific':row['scientific'],'key':key,'asset':dest.name,'source_key':item['source_key'],'source_page':row['source_page'],'source_title':row['source_title'],'artist':row['artist'],'credit':row['credit'],'license':row['license'],'mask_index':row['mask_index'],'review_status':'approved'})
        print('Added refined asset:',key,flush=True)
    for k in sorted({r['source_key'] for r in shipped}):
        if f'`{k}`' not in attr: attr+=ATTRIBUTION[k]
    mp.write_text(json.dumps(dict(sorted(manifest.items())),indent=2)+'\n'); ap.write_text(attr)
    (output/'asset-provenance.json').write_text(json.dumps(shipped,indent=2)+'\n')
    (output/'summary.json').write_text(json.dumps({'approved_requested':len(approved),'shipped':len(shipped),'skipped_existing':len(approved)-len(shipped)},indent=2)+'\n')
    if not shipped: raise RuntimeError('No new refined assets remained after upstream deduplication.')

if __name__=='__main__': main()
