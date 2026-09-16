#!/usr/bin/env python3
from __future__ import annotations
import argparse, html, io, json, math, re
from pathlib import Path
from urllib.parse import urlencode
import requests
from PIL import Image,ImageDraw,ImageFont

API='https://commons.wikimedia.org/w/api.php'; UA='Fugleramme-Texas-Curation/5.0 (https://github.com/4o4R/fugleramme)'
TERMS={
'Neotropic Cormorant':['Nannopterum brasilianum','Phalacrocorax brasilianus','Phalacrocorax olivaceus','Brazilian Cormorant','Olivaceous Cormorant'],
'Western Sandpiper':['Calidris mauri','Ereunetes mauri','Western Sandpiper'],
'Green Jay':['Cyanocorax yncas','Xanthoura yncas','Xanthoura luxuosa','Green Jay'],
'Chihuahuan Raven':['Corvus cryptoleucus','White-necked Raven','Chihuahuan Raven'],
'Shiny Cowbird':['Molothrus bonariensis','Shiny Cowbird'],
'Bronzed Cowbird':['Molothrus aeneus','Tangavius aeneus','Callothrus aeneus','Bronzed Cowbird'],
'Blue-footed Booby':['Sula nebouxii','Sula nebouxi','Blue-footed Booby'],
'Common Pauraque':['Nyctidromus albicollis','Caprimulgus albicollis','Pauraque'],
'Yellow-green Vireo':['Vireo flavoviridis','Vireosylvia flavoviridis','Yellow-green Vireo'],
'Clay-colored Thrush':['Turdus grayi','Merula grayi','Clay-colored Robin','Clay-coloured Thrush'],
'Curve-billed Thrasher':['Toxostoma curvirostre','Harporhynchus curvirostris','Curve-billed Thrasher'],
'Slaty-backed Gull':['Larus schistisagus','Slaty-backed Gull'],
'Mexican Duck':['Anas diazi','Mexican Duck'],
'Yellow-chevroned Parakeet':['Brotogeris chiriri','Brotogerys chiriri','Psittacus chiriri','Yellow-chevroned Parakeet'],
'Nanday Parakeet':['Aratinga nenday','Nandayus nenday','Psittacus nenday','Nanday Parakeet'],
'Mitred Parakeet':['Psittacara mitratus','Conurus mitratus','Mitred Parakeet'],
'Red-masked Parakeet':['Psittacara erythrogenys','Conurus erythrogenys','Red-masked Parakeet'],
'Tropical Kingbird':['Tyrannus melancholicus','Tropical Kingbird'],
}
PD={'public domain','cc0','cc0 1.0','pdm','public domain mark','no known copyright restrictions'}
ILL=re.compile(r'illustrat|drawing|painting|plate|lithograph|chromolith|hand.?colou?r|monograph|biodiversity|bhl|internet archive|audubon|gould|keulemans|smit|baird|cassin|fuertes|gr.nvold|dresser|morris|catesby|barraband|ridgway|swainson|elliot|lydon|learmonth|iconograph|ornitholog',re.I)
BAD=re.compile(r'photograph|camera|flickr photo|ebird|inat|observation|distribution|range map|skeleton|skull|egg\b|stamp|logo|icon',re.I)

def plain(v):return html.unescape(re.sub(r'<[^>]+>',' ',str(v))).strip()
def field(p,k):return plain((p.get('imageinfo') or [{}])[0].get('extmetadata',{}).get(k,{}).get('value',''))
def eligible(p):
 info=(p.get('imageinfo') or [{}])[0];title=p.get('title','');lic=field(p,'LicenseShortName').lower();ctx=' '.join([title,field(p,'Artist'),field(p,'Credit'),field(p,'Categories'),field(p,'ImageDescription')])
 return title.lower().endswith(('.jpg','.jpeg','.png','.tif','.tiff')) and lic in PD and min(info.get('width',0),info.get('height',0))>=350 and bool(ILL.search(ctx)) and not BAD.search(ctx)
def query(session,**extra):
 out=[];cont={}
 for _ in range(5):
  params={'action':'query','prop':'imageinfo','iiprop':'url|size|sha1|extmetadata','iiurlwidth':1800,'format':'json','formatversion':2,**extra,**cont};r=session.get(API,params=params,timeout=60);r.raise_for_status();data=r.json();out.extend(data.get('query',{}).get('pages',[]));cont=data.get('continue',{});
  if not cont:break
 return out
def rank(p,terms):
 title=p.get('title','');cats=field(p,'Categories');ctx=' '.join([title,cats,field(p,'ImageDescription'),field(p,'Credit')]);score=0
 for t in terms:
  if t.lower() in title.lower():score+=80
  if t.lower() in cats.lower():score+=55
  if t.lower() in ctx.lower():score+=20
 if '(illustrations)' in cats.lower():score+=25
 if re.search(r'keulemans|gould|smit|baird|cassin|audubon|fuertes|gr.nvold|ridgway',ctx,re.I):score+=15
 info=p['imageinfo'][0];score+=min(info.get('width',0),4000)/1000
 return score
def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True);(a.output/'sources').mkdir(exist_ok=True)
 rows=json.loads((a.evidence/'choices.json').read_text());by={r['requested']:r for r in rows};s=requests.Session();s.headers['User-Agent']=UA;results=[];errors=[]
 for common,terms in TERMS.items():
  row=by[common];pages={}
  try:
   for term in terms:
    for cat in [f'Category:{term} (illustrations)',f'Category:{term}']:
     for page in query(s,generator='categorymembers',gcmtitle=cat,gcmtype='file',gcmlimit=50):
      if eligible(page):pages[page['pageid']]=page
    for page in query(s,generator='search',gsrnamespace=6,gsrlimit=50,gsrsearch=f'"{term}" illustration'):
     if eligible(page):pages[page['pageid']]=page
    for page in query(s,generator='search',gsrnamespace=6,gsrlimit=50,gsrsearch=f'"{term}" bird plate'):
     if eligible(page):pages[page['pageid']]=page
  except Exception as e:errors.append({'common':common,'error':repr(e)})
  ordered=sorted(pages.values(),key=lambda x:rank(x,terms),reverse=True);sources=[]
  for page in ordered[:4]:
   try:
    info=page['imageinfo'][0];url=info.get('thumburl') or info['url'];data=s.get(url,timeout=90).content;im=Image.open(io.BytesIO(data)).convert('RGB');im.thumbnail((1800,1800),Image.Resampling.LANCZOS);fn=f"sources/{row['rank']}-{page['pageid']}.jpg";im.save(a.output/fn,quality=94);sources.append({'file':fn,'pageid':page['pageid'],'title':page['title'],'source_page':info.get('descriptionurl',''),'download_url':url,'license':field(page,'LicenseShortName'),'artist':field(page,'Artist'),'credit':field(page,'Credit'),'categories':field(page,'Categories'),'score':rank(page,terms)})
   except Exception as e:errors.append({'common':common,'page':page.get('title'),'error':repr(e)})
  results.append({'rank':int(row['rank']),'common':common,'scientific':row.get('scientific',''),'key':row.get('key',''),'terms':terms,'sources':sources});Path(a.output/'results.json').write_text(json.dumps(results,indent=2)+'\n');print(common,len(sources),flush=True)
 Path(a.output/'errors.json').write_text(json.dumps(errors,indent=2)+'\n')
 # contact sheets, one cell per source candidate
 items=[(r,src) for r in results for src in r['sources']];cols=4;cw,ch=400,390
 try:font=ImageFont.truetype('DejaVuSans.ttf',15);small=ImageFont.truetype('DejaVuSans.ttf',10)
 except:font=ImageFont.load_default();small=font
 for off in range(0,len(items),20):
  pg=items[off:off+20];can=Image.new('RGB',(cols*cw,math.ceil(len(pg)/cols)*ch),(240,236,229));d=ImageDraw.Draw(can)
  for i,(r,src) in enumerate(pg):
   im=Image.open(a.output/src['file']);im.thumbnail((365,300),Image.Resampling.LANCZOS);x=(i%cols)*cw;y=(i//cols)*ch;can.paste(im,(x+(cw-im.width)//2,y+3));d.text((x+6,y+310),f"{r['rank']}. {r['common']}",font=font,fill='black');d.text((x+6,y+334),src['title'][5:57],font=small,fill='black');d.text((x+6,y+351),f"score {src['score']:.0f} / {src['license']}",font=small,fill='black')
  can.save(a.output/f'contact-{off//20+1:02d}.jpg',quality=92)
 Path(a.output/'summary.json').write_text(json.dumps({'searched':len(results),'with_candidates':sum(bool(r['sources']) for r in results),'without_candidates':sum(not r['sources'] for r in results),'downloaded_candidates':len(items),'errors':len(errors)},indent=2)+'\n')
if __name__=='__main__':main()
