#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.texas-curation'
spec = importlib.util.spec_from_file_location('harvest', WORK / 'harvest_sources.py')
assert spec and spec.loader
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
h.OUT = WORK / 'missing-category-sources'
h.OUT.mkdir(parents=True, exist_ok=True)

TARGETS = {
    'Neotropic Cormorant': {
        'key':'nannopterum-brasilianum','scientific':'Nannopterum brasilianum',
        'categories':['Phalacrocorax brasilianus (illustrations)','Nannopterum brasilianum (illustrations)'],
        'terms':['Phalacrocorax brasilianus','Phalacrocorax olivaceus','Carbo brasilianus','Graculus brasiliensis']},
    'Western Sandpiper': {
        'key':'calidris-mauri','scientific':'Calidris mauri',
        'categories':['Calidris mauri (illustrations)','Ereunetes mauri (illustrations)'],
        'terms':['Calidris mauri','Ereunetes mauri','Tringa mauri']},
    'Green Jay': {
        'key':'cyanocorax-yncas','scientific':'Cyanocorax yncas',
        'categories':['Cyanocorax yncas (illustrations)','Xanthoura luxuosa (illustrations)'],
        'terms':['Cyanocorax yncas','Xanthoura luxuosa','Cyanocorax luxuosus']},
    'Chihuahuan Raven': {
        'key':'corvus-cryptoleucus','scientific':'Corvus cryptoleucus',
        'categories':['Corvus cryptoleucus (illustrations)'],
        'terms':['Corvus cryptoleucus','White-necked Raven','White-necked Crow']},
    'Shiny Cowbird': {
        'key':'molothrus-bonariensis','scientific':'Molothrus bonariensis',
        'categories':['Molothrus bonariensis (illustrations)'],
        'terms':['Molothrus bonariensis','Molobrus bonariensis']},
    'Bronzed Cowbird': {
        'key':'molothrus-aeneus','scientific':'Molothrus aeneus',
        'categories':['Molothrus aeneus (illustrations)','Tangavius aeneus (illustrations)'],
        'terms':['Molothrus aeneus','Callothrus aeneus','Tangavius aeneus']},
    'Blue-footed Booby': {
        'key':'sula-nebouxii','scientific':'Sula nebouxii',
        'categories':['Sula nebouxii (illustrations)'],
        'terms':['Sula nebouxii','Sula nebouxi','Blue-footed Gannet']},
    'Common Pauraque': {
        'key':'nyctidromus-albicollis','scientific':'Nyctidromus albicollis',
        'categories':['Nyctidromus albicollis (illustrations)'],
        'terms':['Nyctidromus albicollis','Caprimulgus albicollis']},
    'Yellow-green Vireo': {
        'key':'vireo-flavoviridis','scientific':'Vireo flavoviridis',
        'categories':['Vireo flavoviridis (illustrations)','Vireosylvia flavoviridis (illustrations)'],
        'terms':['Vireo flavoviridis','Vireosylvia flavoviridis']},
    'Clay-colored Thrush': {
        'key':'turdus-grayi','scientific':'Turdus grayi',
        'categories':['Turdus grayi (illustrations)','Merula grayi (illustrations)'],
        'terms':['Turdus grayi','Merula grayi','Clay-coloured Thrush']},
    'Curve-billed Thrasher': {
        'key':'toxostoma-curvirostre','scientific':'Toxostoma curvirostre',
        'categories':['Toxostoma curvirostre (illustrations)','Harporhynchus curvirostris (illustrations)'],
        'terms':['Toxostoma curvirostre','Harporhynchus curvirostris']},
    'Slaty-backed Gull': {
        'key':'larus-schistisagus','scientific':'Larus schistisagus',
        'categories':['Larus schistisagus (illustrations)'],
        'terms':['Larus schistisagus','Slaty-backed Gull']},
    'Mexican Duck': {
        'key':'anas-diazi','scientific':'Anas diazi',
        'categories':['Anas diazi (illustrations)'],
        'terms':['Anas diazi','Mexican Duck']},
    'Yellow-chevroned Parakeet': {
        'key':'brotogeris-chiriri','scientific':'Brotogeris chiriri',
        'categories':['Brotogeris chiriri (illustrations)'],
        'terms':['Brotogeris chiriri','Brotogerys chiriri','Psittacus chiriri']},
    'Nanday Parakeet': {
        'key':'aratinga-nenday','scientific':'Aratinga nenday',
        'categories':['Aratinga nenday (illustrations)','Nandayus nenday (illustrations)'],
        'terms':['Aratinga nenday','Nandayus nenday','Psittacus nenday']},
    'Mitred Parakeet': {
        'key':'psittacara-mitratus','scientific':'Psittacara mitratus',
        'categories':['Psittacara mitratus (illustrations)','Conurus mitratus (illustrations)'],
        'terms':['Psittacara mitratus','Conurus mitratus']},
    'Red-masked Parakeet': {
        'key':'psittacara-erythrogenys','scientific':'Psittacara erythrogenys',
        'categories':['Psittacara erythrogenys (illustrations)','Conurus erythrogenys (illustrations)'],
        'terms':['Psittacara erythrogenys','Conurus erythrogenys']},
    'Tropical Kingbird': {
        'key':'tyrannus-melancholicus','scientific':'Tyrannus melancholicus',
        'categories':['Tyrannus melancholicus (illustrations)'],
        'terms':['Tyrannus melancholicus','Tropical Kingbird']},
}

YEAR = re.compile(r'\b(1[5-9]\d{2}|19[0-2]\d)\b')

def discovery_eligible(page: dict) -> bool:
    info = (page.get('imageinfo') or [{}])[0]
    if not page.get('title','').lower().endswith(('.jpg','.jpeg','.png','.tif','.tiff')):
        return False
    if h.field(page,'LicenseShortName').lower() not in h.LICENSES:
        return False
    if min(info.get('width',0), info.get('height',0)) < 400:
        return False
    title=page.get('title','')
    if h.REJECT_TITLE.search(title):
        return False
    context=' '.join(h.field(page,k) for k in ('Artist','Credit','Categories','ImageDescription','DateTimeOriginal'))
    return bool(h.HISTORICAL.search(context) or '(illustrations)' in context or YEAR.search(context))

fetch = h.helpers.Fetcher()
rows=[]; metadata=[]; errors=[]; downloaded={}
for common, cfg in TARGETS.items():
    pages={}
    for cat in cfg['categories']:
        try:
            for page in h.query(fetch, generator='categorymembers', gcmtitle='Category:'+cat,
                                gcmtype='file', gcmlimit=50):
                if discovery_eligible(page): pages[page['pageid']]=page
        except Exception as e:
            errors.append({'requested':common,'query':'Category:'+cat,'error':str(e)})
    for term in cfg['terms']:
        try:
            for page in h.query(fetch, generator='search', gsrnamespace=6, gsrlimit=50,
                                gsrsearch='"'+term+'" -Crossley -filetype:pdf -filetype:djvu'):
                if discovery_eligible(page): pages[page['pageid']]=page
        except Exception as e:
            errors.append({'requested':common,'query':term,'error':str(e)})
    ordered=sorted(pages.values(), key=lambda p:(('(illustrations)' in h.field(p,'Categories')), bool(h.HISTORICAL.search(' '.join(h.field(p,k) for k in ('Artist','Credit','Categories','ImageDescription')))), min((p.get('imageinfo') or [{}])[0].get('width',0),12000)), reverse=True)
    rec={'requested':common,**cfg,'candidate_count':len(ordered),'sources':[],'status':'no candidate'}
    for page in ordered:
        metadata.append({'requested':common,'key':cfg['key'],'metadata':page})
    for page in ordered[:5]:
        try:
            pid=page['pageid']
            if pid not in downloaded: downloaded[pid]=h.download(fetch,page)
            rec['sources'].append(downloaded[pid]); rec['status']='source candidates; visual identity pending'
        except Exception as e:
            errors.append({'requested':common,'query':page.get('title',''),'error':str(e)})
    rows.append(rec)
    h.helpers.write_json(h.OUT/'choices.json',rows)
    h.helpers.write_json(h.OUT/'metadata.json',metadata)
    h.helpers.write_csv(h.OUT/'errors.csv',errors)
    print(common, len(rec['sources']), '/', len(ordered), flush=True)
h.helpers.write_json(h.OUT/'summary.json',{
    'researched':len(rows),'rows_with_candidate':sum(bool(r['sources']) for r in rows),
    'rows_without_candidate':sum(not r['sources'] for r in rows),'downloaded_sources':len(downloaded),
    'errors':len(errors),'approved_artwork':0,
    'warning':'Discovery only; visual identity/cutout review required.'})
