#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from PIL import Image

NEW_ATTRIBUTIONS = {
    "gould-beagle": "**Gould (Voyage of the Beagle)** - bird plate by **John Gould** from the zoological results of **Charles Darwin's** voyage of H.M.S. *Beagle* (19th century), via the exact public-domain Wikimedia Commons file page linked in the manifest. Manifest key: `gould-beagle`.",
    "smit-british-museum": "**Smit (British Museum catalogue)** - bird plate by **Joseph Smit** from the *Catalogue of the Birds in the British Museum*, via the exact public-domain Wikimedia Commons file page linked in the manifest. Manifest key: `smit-british-museum`.",
}


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--inputs',type=Path,required=True)
    p.add_argument('--approved',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    repo=a.repo.resolve(); inputs=a.inputs.resolve(); out=a.output.resolve(); out.mkdir(parents=True,exist_ok=True)
    import sys
    sys.path[:0]=[str(repo),str(repo/'src')]
    from fugleramme.names import normalize
    from tools.add_bird import prepare, write_plate

    rows=json.loads((inputs/'ledger.json').read_text())
    by={(int(r['rank']),int(r['variant'])):r for r in rows}
    approved=json.loads(a.approved.read_text())
    manifest_path=repo/'assets/artwork/classic/manifest.json'
    attr_path=repo/'assets/artwork/classic/ATTRIBUTION.md'
    birds=repo/'assets/artwork/classic/birds'
    manifest=json.loads(manifest_path.read_text()); attribution=attr_path.read_text()
    shipped=[]; skipped=[]
    for item in approved:
        row=by[(int(item['rank']),int(item['variant']))]
        assert row['key']==item['key']
        assert normalize(row['scientific'])==row['key']
        assert row['license'] in {'Public domain','CC0'}
        key=row['key']
        if any((birds/f'{key}{suffix}').exists() for suffix in ['.webp','-2.webp','.png','.jpg','.jpeg']):
            skipped.append({'key':key,'reason':'already covered on current main'}); continue
        src=inputs/row['preview']
        with Image.open(src) as im:
            assert im.mode=='RGBA'
            assert im.getchannel('A').getextrema()==(0,255)
        prepared=prepare(src); dest=birds/f'{key}.webp'; write_plate(prepared,dest)
        manifest[f'birds/{dest.name}']={'source':item['source_key'],'url':row['source_page']}
        shipped.append({'rank':row['rank'],'common':row['common'],'scientific':row['scientific'],'key':key,'asset':dest.name,'source_key':item['source_key'],'source_page':row['source_page'],'source_title':row['source_title'],'artist':row['artist'],'credit':row['credit'],'license':row['license'],'variant':row['variant'],'review_status':'visually approved alternate'})
    for source_key in sorted({r['source_key'] for r in shipped}):
        marker=f'Manifest key: `{source_key}`'
        if marker in attribution: continue
        paragraph=NEW_ATTRIBUTIONS.get(source_key)
        if paragraph is None: raise RuntimeError(f'Missing attribution for {source_key}')
        attribution=attribution.rstrip()+'\n\n'+paragraph+'\n'
    manifest_path.write_text(json.dumps(dict(sorted(manifest.items())),indent=2)+'\n')
    attr_path.write_text(attribution)
    (out/'asset-provenance.json').write_text(json.dumps(shipped,indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps({'approved_requested':len(approved),'shipped':len(shipped),'skipped_existing':len(skipped),'skipped':skipped},indent=2)+'\n')
    print((out/'summary.json').read_text())
    if not shipped: raise RuntimeError('No new alternate assets remained')

if __name__=='__main__': main()
