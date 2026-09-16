#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, io, importlib.util, json, math, sys
from pathlib import Path
from urllib.request import Request,urlopen
import numpy as np, torch
from PIL import Image,ImageDraw,ImageFont

PICKS={
67:'PyrocephalusParvirostrisDarwin.jpg',
263:'Birds of New York (Plate 68)',
279:'Birds of New York (Plate 91)',
329:'399 I. Mourning Warbler (cropped).jpg',
351:'The birds of America (Pl. 163)',
400:'Jacana spinosa 1849.jpg',
481:'Numida meleagris 1869.jpg',
493:'PhaethonIndicusSmit.jpg',
504:'Melopsittacus undulatus -Nanodes undulatus Undulated parrakeet',
512:'Ara ararauna -Macrocercus ararauna Blue & yellow Maccaw',
525:'Euodice malabarica 1876.jpg',
526:'Estrilda melpoda 1876.jpg',
528:'Vidua macroura 1869.jpg',
}

def loadmod(name,path):
 spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);assert spec and spec.loader;spec.loader.exec_module(mod);return mod

def fetch(url):
 req=Request(url,headers={'User-Agent':'Fugleramme-Texas-Curation/5.1 (https://github.com/4o4R/fugleramme)'});return urlopen(req,timeout=90).read()
def page_field(page,k):
 info=(page.get('imageinfo') or [{}])[0];return info.get('extmetadata',{}).get(k,{}).get('value','')
def strip_html(s):
 import re,html
 return html.unescape(re.sub(r'<[^>]+>',' ',str(s))).strip()
def contact(rows,out):
 try:font=ImageFont.truetype('DejaVuSans.ttf',15);small=ImageFont.truetype('DejaVuSans.ttf',10)
 except:font=ImageFont.load_default();small=font
 cols=4;cw,ch=400,360
 for v in range(3):
  vr=[r for r in rows if r['variant']==v]
  can=Image.new('RGB',(cols*cw,math.ceil(len(vr)/cols)*ch),(240,236,229));d=ImageDraw.Draw(can)
  for i,r in enumerate(vr):
   im=Image.open(out/r['preview']).convert('RGBA');im.thumbnail((365,275),Image.Resampling.LANCZOS);x=(i%cols)*cw;y=(i//cols)*ch;can.paste(im,(x+(cw-im.width)//2,y+3),im);d.text((x+7,y+287),f"{r['rank']}. {r['common']}",font=font,fill='black');d.text((x+7,y+311),f"v{v} {r['score']:.2f} {r['basis']}",font=small,fill='black');d.text((x+7,y+329),r['source_title'][5:58],font=small,fill='black')
  can.save(out/f'contact-v{v}.jpg',quality=92)
def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--mask-tools',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True);(a.output/'previews').mkdir(exist_ok=True);(a.output/'proof').mkdir(exist_ok=True)
 ref=loadmod('ref',a.repo/'.texas-curation/mass_refine2.py');sys.path.insert(0,str(a.mask_tools));from mobile_sam import SamPredictor,sam_model_registry
 predictor=SamPredictor(sam_model_registry['vit_t'](checkpoint=str(a.mask_tools/'mobile_sam.pt')).eval());torch.set_num_threads(4)
 meta=json.loads((a.evidence/'candidate-metadata.json').read_text());byrank={}
 for item in meta:byrank.setdefault(int(item['rank']),[]).append(item)
 choices=json.loads((a.evidence/'choices.json').read_text());rowby={int(r['rank']):r for r in choices};ledger=[];errors=[]
 for rank,want in PICKS.items():
  try:
   matches=[x for x in byrank.get(rank,[]) if want.lower() in x['metadata']['title'].lower()]
   if not matches:raise RuntimeError('preferred source not present: '+want)
   item=matches[0];page=item['metadata'];info=page['imageinfo'][0];url=info.get('thumburl') or info['url'];data=fetch(url);src=Image.open(io.BytesIO(data)).convert('RGB');arr=np.asarray(src);delta,strong,sat=ref.image_features(arr);boxes=ref.proposals(strong,delta);predictor.set_image(arr);opts=[]
   for x0,y0,x1,y1,basis in boxes:
    with torch.inference_mode():masks,scores,_=predictor.predict(box=np.array([x0,y0,x1,y1],dtype=float),multimask_output=True)
    for mi,(m,ps) in enumerate(zip(masks,scores)):
     sc=ref.score_mask(m,float(ps),delta,strong,sat)
     if sc>-900:opts.append((sc,m,mi,(x0,y0,x1,y1),basis,float(ps)))
   opts.sort(key=lambda z:z[0],reverse=True);distinct=[]
   for opt in opts:
    if all(ref.iou_mask(opt[1],o[1])<.72 for o in distinct):distinct.append(opt)
    if len(distinct)==3:break
   if not distinct:raise RuntimeError('no plausible masks')
   r=rowby[rank];proof=src.copy();proof.thumbnail((900,1100),Image.Resampling.LANCZOS);proofn=f'proof/{rank:03d}-{r["key"]}.jpg';proof.save(a.output/proofn,quality=90)
   for v,opt in enumerate(distinct):
    prev=f'previews/{rank:03d}-{r["key"]}-v{v}.png';ref.cut(src,opt[1]).save(a.output/prev);ledger.append({'rank':rank,'common':r['requested'],'scientific':r['scientific'],'key':r['key'],'variant':v,'preview':prev,'proof':proofn,'score':opt[0],'sam_score':opt[5],'basis':opt[4],'source_title':page['title'],'source_page':info.get('descriptionurl',''),'download_url':url,'license':strip_html(page_field(page,'LicenseShortName')),'artist':strip_html(page_field(page,'Artist')),'credit':strip_html(page_field(page,'Credit')),'status':'ALTERNATE SOURCE REVIEW CANDIDATE ONLY'})
   print('alternate',rank,r['requested'],page['title'],flush=True)
  except Exception as e:errors.append({'rank':rank,'error':repr(e)});print('FAILED',rank,repr(e),flush=True)
 (a.output/'ledger.json').write_text(json.dumps(ledger,indent=2)+'\n');(a.output/'errors.json').write_text(json.dumps(errors,indent=2)+'\n');contact(ledger,a.output);(a.output/'summary.json').write_text(json.dumps({'requested':len(PICKS),'species_with_candidates':len({r['rank'] for r in ledger}),'candidate_masks':len(ledger),'errors':len(errors)},indent=2)+'\n')
if __name__=='__main__':main()
