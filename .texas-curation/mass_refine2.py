#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, math, re, sys
from pathlib import Path
import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFilter, ImageFont

PAPER=(240,236,229)

def sha(data:bytes)->str: return hashlib.sha256(data).hexdigest()

def iou_mask(a:np.ndarray,b:np.ndarray)->float:
    inter=np.logical_and(a,b).sum(); union=np.logical_or(a,b).sum(); return float(inter/max(1,union))

def cleanup(mask:np.ndarray)->np.ndarray:
    m=mask.astype(np.uint8); n,lab,stats,_=cv2.connectedComponentsWithStats(m,8)
    if n<=1:return mask.astype(bool)
    areas=stats[1:,cv2.CC_STAT_AREA]; largest=int(np.argmax(areas))+1; keep={largest}; la=int(stats[largest,cv2.CC_STAT_AREA])
    lx,ly,lw,lh=stats[largest,:4]; margin=max(8,int(max(lw,lh)*.16)); box=(lx-margin,ly-margin,lx+lw+margin,ly+lh+margin)
    for idx in range(1,n):
        if idx==largest:continue
        area=int(stats[idx,cv2.CC_STAT_AREA]); x,y,w,h=stats[idx,:4]; cx=x+w/2;cy=y+h/2
        if area>=max(8,int(la*.003)) and box[0]<=cx<=box[2] and box[1]<=cy<=box[3]: keep.add(idx)
    return np.isin(lab,list(keep))

def cut(source:Image.Image,mask:np.ndarray)->Image.Image:
    mask=cleanup(mask); alpha=Image.fromarray(mask.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(.45)); bbox=alpha.getbbox()
    if bbox is None: raise ValueError('empty mask')
    rgba=source.crop(bbox).convert('RGBA'); rgba.putalpha(alpha.crop(bbox)); radius=max(5,round(max(rgba.size)*.014)); pad=radius+4
    padded=Image.new('RGBA',(rgba.width+2*pad,rgba.height+2*pad),(*PAPER,0)); padded.paste(rgba,(pad,pad)); halo=Image.new('RGBA',padded.size,(*PAPER,0)); halo.putalpha(padded.getchannel('A').filter(ImageFilter.MaxFilter(radius*2+1))); final=Image.alpha_composite(halo,padded); final.thumbnail((1200,1200),Image.Resampling.LANCZOS); return final

def image_features(arr:np.ndarray):
    lab=cv2.cvtColor(arr,cv2.COLOR_RGB2LAB).astype(np.float32); gray=cv2.cvtColor(arr,cv2.COLOR_RGB2GRAY); hsv=cv2.cvtColor(arr,cv2.COLOR_RGB2HSV)
    h,w=gray.shape; band=max(4,int(min(h,w)*.025)); border=np.concatenate([lab[:band].reshape(-1,3),lab[-band:].reshape(-1,3),lab[:,:band].reshape(-1,3),lab[:,-band:].reshape(-1,3)]); bg=np.median(border,axis=0); delta=np.linalg.norm(lab-bg,axis=2)
    strong=(delta>25)|(gray<170)|(hsv[:,:,1]>55)
    strong[:band]=False;strong[-band:]=False;strong[:,:band]=False;strong[:,-band:]=False
    return delta,strong,hsv[:,:,1]

def proposals(strong:np.ndarray,delta:np.ndarray):
    h,w=strong.shape; work=strong.astype(np.uint8); work=cv2.morphologyEx(work,cv2.MORPH_CLOSE,np.ones((7,7),np.uint8)); work=cv2.dilate(work,np.ones((13,13),np.uint8),iterations=1)
    n,lab,stats,_=cv2.connectedComponentsWithStats(work,8); comps=[]
    for idx in range(1,n):
        x,y,bw,bh,area=map(int,stats[idx]); frac=area/(h*w)
        if frac<.001 or bw<18 or bh<18:continue
        touches=sum([x<=3,y<=3,x+bw>=w-4,y+bh>=h-4])
        if touches>=2 or (bw>.92*w and bh>.92*h):continue
        content=float(delta[lab==idx].mean()) if np.any(lab==idx) else 0
        comps.append((math.log1p(area)+content/35,x,y,bw,bh))
    comps.sort(reverse=True); out=[]
    for _,x,y,bw,bh in comps[:7]:
        pad=int(max(bw,bh)*.13)+5; out.append((max(0,x-pad),max(0,y-pad),min(w-1,x+bw+pad),min(h-1,y+bh+pad),'dilated-component'))
    ys,xs=np.nonzero(strong)
    if len(xs):
        for q0,q1,label in [(.04,.96,'ink-96'),(.12,.88,'ink-76')]:
            x0,x1=np.quantile(xs,[q0,q1]);y0,y1=np.quantile(ys,[q0,q1]); pad=int(max(x1-x0,y1-y0)*.06)+4; out.append((max(0,int(x0)-pad),max(0,int(y0)-pad),min(w-1,int(x1)+pad),min(h-1,int(y1)+pad),label))
    uniq=[]
    def biou(a,b):
        ix0=max(a[0],b[0]);iy0=max(a[1],b[1]);ix1=min(a[2],b[2]);iy1=min(a[3],b[3]); inter=max(0,ix1-ix0)*max(0,iy1-iy0); aa=max(1,(a[2]-a[0])*(a[3]-a[1]));bb=max(1,(b[2]-b[0])*(b[3]-b[1]));return inter/max(1,aa+bb-inter)
    for b in out:
        if not any(biou(b[:4],u[:4])>.9 for u in uniq):uniq.append(b)
    return uniq[:8]

def score_mask(mask,pred,delta,strong,sat):
    h,w=mask.shape; area=int(mask.sum());frac=area/(h*w)
    if frac<.0012 or frac>.40:return -999
    ys,xs=np.nonzero(mask)
    if not len(xs):return -999
    x0,x1,y0,y1=int(xs.min()),int(xs.max()),int(ys.min()),int(ys.max());bw=x1-x0+1;bh=y1-y0+1;barea=bw*bh;fill=area/max(1,barea);touches=sum([x0<=2,y0<=2,x1>=w-3,y1>=h-3])
    strong_frac=float(strong[mask].mean());mean_delta=float(delta[mask].mean());mean_sat=float(sat[mask].mean())
    if strong_frac<.12 or mean_delta<12:return -999
    if bw>.74*w and bh>.74*h and fill<.28:return -999
    if touches>=2:return -999
    area_pref=max(0,1-abs(math.log(max(frac,1e-5)/.045))/3)
    return float(pred)*2.0+strong_frac*2.0+min(mean_delta/45,1.6)+min(mean_sat/110,1.0)*.45+area_pref*.8+min(fill,.75)*.25-touches*.25

def sheets(rows,out):
    try:font=ImageFont.truetype('DejaVuSans.ttf',14);small=ImageFont.truetype('DejaVuSans.ttf',10)
    except OSError:font=ImageFont.load_default();small=font
    cols=5;cw,ch=320,330
    for variant in range(3):
        vr=[r for r in rows if r['variant']==variant]
        for off in range(0,len(vr),25):
            page=vr[off:off+25];can=Image.new('RGB',(cols*cw,math.ceil(len(page)/cols)*ch),PAPER);d=ImageDraw.Draw(can)
            for i,r in enumerate(page):
                im=Image.open(out/r['preview']).convert('RGBA');im.thumbnail((290,250),Image.Resampling.LANCZOS);x=(i%cols)*cw;y=(i//cols)*ch;can.paste(im,(x+(cw-im.width)//2,y+4),im);d.text((x+7,y+258),f"{r['rank']}. {r['common']}"[:39],font=font,fill='black');d.text((x+7,y+280),f"v{variant} {r['refine_score']:.2f} {r['basis']}",font=small,fill='black');d.text((x+7,y+298),r['source_title'][5:50],font=small,fill='black')
            can.save(out/f'contact-v{variant}-{off//25+1:02d}.jpg',quality=92)

def main():
    p=argparse.ArgumentParser();
    for n in ['evidence','sources','mask-tools','covered','approved','coordination','reviewed','output']:p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--part',type=int,choices=range(4),required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True);(a.output/'previews').mkdir(exist_ok=True);(a.output/'proof').mkdir(exist_ok=True)
    sys.path.insert(0,str(a.mask_tools));from mobile_sam import SamPredictor,sam_model_registry
    predictor=SamPredictor(sam_model_registry['vit_t'](checkpoint=str(a.mask_tools/'mobile_sam.pt')).eval());torch.set_num_threads(4);cv2.setNumThreads(1)
    covered={re.sub(r'-\d+$','',p.stem) for p in (a.covered/'assets/artwork/classic/birds').glob('*.webp')}; mass={int(k):set(v) for k,v in json.loads(a.approved.read_text()).items()};excluded=set(json.loads(a.coordination.read_text())['species'])|set(json.loads(a.reviewed.read_text())['species']);rows=json.loads((a.evidence/'choices.json').read_text());ledger=[];fail=[]
    for row in rows:
        if not row.get('key') or row['key'] in covered or row['requested'] in excluded or not row.get('sources'):continue
        if int(row['rank']) in mass[a.part]:continue
        meta=row['sources'][0];pid=int(meta['pageid']);
        if pid%4!=a.part:continue
        sp=a.sources/f'{pid}.png'
        try:
            if not sp.exists():raise FileNotFoundError(sp)
            if sha(sp.read_bytes())!=meta['normalized_sha256']:raise RuntimeError('source hash mismatch')
            src=Image.open(sp).convert('RGB');arr=np.asarray(src);delta,strong,sat=image_features(arr);boxes=proposals(strong,delta)
            if not boxes:raise RuntimeError('no proposals')
            predictor.set_image(arr);opts=[]
            for x0,y0,x1,y1,basis in boxes:
                with torch.inference_mode():masks,scores,_=predictor.predict(box=np.array([x0,y0,x1,y1],dtype=float),multimask_output=True)
                for mi,(m,ps) in enumerate(zip(masks,scores)):
                    sc=score_mask(m,float(ps),delta,strong,sat)
                    if sc>-900:opts.append((sc,m,mi,(x0,y0,x1,y1),basis,float(ps)))
            opts.sort(key=lambda z:z[0],reverse=True);distinct=[]
            for opt in opts:
                if all(iou_mask(opt[1],old[1])<.72 for old in distinct):distinct.append(opt)
                if len(distinct)==3:break
            if not distinct:raise RuntimeError('no plausible refined mask')
            proof=src.copy();proof.thumbnail((900,1100),Image.Resampling.LANCZOS);proofname=f"proof/{int(row['rank']):03d}-{row['key']}.jpg";proof.save(a.output/proofname,quality=90)
            for v,opt in enumerate(distinct):
                prev=f"previews/{int(row['rank']):03d}-{row['key']}-v{v}.png";cut(src,opt[1]).save(a.output/prev);ledger.append({'rank':int(row['rank']),'common':row['requested'],'key':row['key'],'scientific':row['scientific'],'variant':v,'preview':prev,'proof':proofname,'refine_score':opt[0],'sam_score':opt[5],'box':list(opt[3]),'basis':opt[4],'source_pageid':pid,'source_title':meta['file_title'],'source_page':meta['source_page'],'license':meta['license'],'license_url':meta.get('license_url',''),'artist':meta.get('artist',''),'credit':meta.get('credit',''),'status':'REFINED REVIEW CANDIDATE ONLY'})
            print('refined',row['rank'],row['requested'],len(distinct),flush=True)
        except Exception as e:fail.append({'rank':row.get('rank'),'common':row.get('requested'),'error':repr(e)});print('FAILED',row.get('rank'),row.get('requested'),repr(e),flush=True)
        (a.output/'ledger.json').write_text(json.dumps(ledger,indent=2)+'\n');(a.output/'failures.json').write_text(json.dumps(fail,indent=2)+'\n')
    sheets(ledger,a.output);species=len({r['rank'] for r in ledger});(a.output/'summary.json').write_text(json.dumps({'part':a.part,'species_with_refined_candidates':species,'candidate_masks':len(ledger),'failures':len(fail),'approved':0},indent=2)+'\n');print('species',species,'masks',len(ledger),'failures',len(fail))
    if not ledger:raise RuntimeError('no refined candidates')
if __name__=='__main__':main()
