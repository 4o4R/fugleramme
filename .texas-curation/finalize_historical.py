#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from PIL import Image

SNIPPETS={
'baird-smithsonian':"\n\n**Baird (Smithsonian)** - North American bird illustration by **Spencer Fullerton Baird**, from the Smithsonian Libraries image record linked through the exact Wikimedia Commons file page in the manifest. Public domain. Manifest key: `baird-smithsonian`.\n",
'smit-pzs':"\n\n**Smit (Proceedings of the Zoological Society of London)** - bird plate by **Joseph Smit**, from the *Proceedings of the Zoological Society of London* (1867), via the exact Wikimedia Commons file page linked in the manifest. Public domain. Manifest key: `smit-pzs`.\n",
}

def main():
 p=argparse.ArgumentParser()
 for n in ['repo','candidates','approved','output']: p.add_argument('--'+n,type=Path,required=True)
 a=p.parse_args(); repo=a.repo.resolve(); cand=a.candidates.resolve(); out=a.output.resolve(); out.mkdir(parents=True,exist_ok=True)
 sys.path[:0]=[str(repo),str(repo/'src')]
 from fugleramme.names import normalize
 from tools.add_bird import prepare,write_plate
 approved=json.loads(a.approved.read_text()); ledger=json.loads((cand/'ledger.json').read_text()); by={(r['common'],int(r['mask_index'])):r for r in ledger}
 mp=repo/'assets/artwork/classic/manifest.json'; manifest=json.loads(mp.read_text()); ap=repo/'assets/artwork/classic/ATTRIBUTION.md'; attr=ap.read_text(); birds=repo/'assets/artwork/classic/birds'; shipped=[]; skipped=[]
 for item in approved:
  row=by[(item['common'],int(item['mask_index']))]; key=row['key']; assert normalize(row['scientific'])==key
  if any(p.exists() for p in [birds/f'{key}.webp',birds/f'{key}-2.webp']): skipped.append({'common':row['common'],'key':key}); continue
  src=cand/row['preview']
  with Image.open(src) as im: assert im.mode=='RGBA' and im.getchannel('A').getextrema()==(0,255)
  dest=birds/f'{key}.webp'; write_plate(prepare(src),dest); manifest[f'birds/{dest.name}']={'source':item['source_key'],'url':row['source_page']}
  shipped.append({'common':row['common'],'scientific':row['scientific'],'key':key,'asset':dest.name,'source_key':item['source_key'],'source_page':row['source_page'],'source_title':row['source_title'],'artist':row['artist'],'credit':row['credit'],'license':row['license'],'mask_index':row['mask_index'],'review_status':'approved'})
 for k in sorted({r['source_key'] for r in shipped}):
  if k in SNIPPETS and f'`{k}`' not in attr: attr+=SNIPPETS[k]
 mp.write_text(json.dumps(dict(sorted(manifest.items())),indent=2)+'\n'); ap.write_text(attr)
 (out/'asset-provenance.json').write_text(json.dumps(shipped,indent=2)+'\n'); (out/'skipped-existing.json').write_text(json.dumps(skipped,indent=2)+'\n'); (out/'summary.json').write_text(json.dumps({'approved_requested':len(approved),'shipped':len(shipped),'skipped_existing':len(skipped)},indent=2)+'\n')
 if not shipped: raise RuntimeError('No new historical assets remained after deduplication')
if __name__=='__main__': main()
