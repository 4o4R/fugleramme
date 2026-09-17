#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
import cv2, numpy as np, torch
from PIL import Image, ImageFilter

src_path=Path(sys.argv[1]); mask_dir=Path(sys.argv[2]); out=Path(sys.argv[3]); out.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(mask_dir))
from mobile_sam import SamPredictor, sam_model_registry
PAPER=(240,236,229)

def cleanup(mask):
    m=mask.astype(np.uint8); n,lab,stats,_=cv2.connectedComponentsWithStats(m,8)
    if n<=1:return mask.astype(bool)
    areas=stats[1:,cv2.CC_STAT_AREA]; largest=int(np.argmax(areas))+1; la=int(stats[largest,cv2.CC_STAT_AREA]); lx,ly,lw,lh=stats[largest,:4]
    keep={largest}; margin=max(8,int(max(lw,lh)*.18)); box=(lx-margin,ly-margin,lx+lw+margin,ly+lh+margin)
    for idx in range(1,n):
        if idx==largest: continue
        area=int(stats[idx,cv2.CC_STAT_AREA]); x,y,w,h=stats[idx,:4]; cx=x+w/2; cy=y+h/2
        if area>=max(8,int(la*.003)) and box[0]<=cx<=box[2] and box[1]<=cy<=box[3]: keep.add(idx)
    return np.isin(lab,list(keep))

def cut(source,mask):
    mask=cleanup(mask); alpha=Image.fromarray(mask.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(.45)); bbox=alpha.getbbox()
    rgba=source.crop(bbox).convert('RGBA'); rgba.putalpha(alpha.crop(bbox)); radius=max(5,round(max(rgba.size)*.014)); pad=radius+4
    padded=Image.new('RGBA',(rgba.width+2*pad,rgba.height+2*pad),(*PAPER,0)); padded.paste(rgba,(pad,pad)); halo=Image.new('RGBA',padded.size,(*PAPER,0)); halo.putalpha(padded.getchannel('A').filter(ImageFilter.MaxFilter(radius*2+1))); final=Image.alpha_composite(halo,padded); final.thumbnail((1200,1200),Image.Resampling.LANCZOS); return final

source=Image.open(src_path).convert('RGB'); arr=np.asarray(source)
model=sam_model_registry['vit_t'](checkpoint=str(mask_dir/'mobile_sam.pt')).eval(); predictor=SamPredictor(model); torch.set_num_threads(4); predictor.set_image(arr)
# Eaton plate 81: bottom-right specimen is the bird labeled Nelson's Sparrow.
box=np.array([430,1080,920,1490],dtype=float)
with torch.inference_mode(): masks,scores,_=predictor.predict(box=box,multimask_output=True)
rows=[]
for i,(m,score) in enumerate(zip(masks,scores)):
    img=cut(source,m); name=f'nelson-v{i}.png'; img.save(out/name)
    rows.append({'variant':i,'score':float(score),'preview':name,'box':box.tolist()})
(out/'ledger.json').write_text(json.dumps(rows,indent=2)+'\n')
source.save(out/'source-proof.jpg',quality=92)
print(json.dumps(rows,indent=2))
