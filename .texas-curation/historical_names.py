#!/usr/bin/env python3
# .texas-curation/historical_names.py
# Source-discovery terms only. They are NOT runtime aliases or species approvals.
from pathlib import Path
import importlib.util
import json
import re

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.texas-curation'
spec = importlib.util.spec_from_file_location('harvest', WORK / 'harvest_sources.py')
assert spec and spec.loader
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
h.OUT = WORK / 'historical-sources'
h.OUT.mkdir(parents=True, exist_ok=True)
TERMS = {
    'Neotropic Cormorant': ['Phalacrocorax brasilianus', 'Carbo brasilianus', 'Phalacrocorax olivaceus'],
    'Western Sandpiper': ['Ereunetes mauri', 'Tringa mauri'],
    'Green Jay': ['Xanthoura luxuosa', 'Cyanocorax luxuosus'],
    'Chihuahuan Raven': ['White-necked Raven', 'Corvus cryptoleucus'],
    "Cassin's Sparrow": ['Aimophila cassinii', 'Peucaea cassini'],
    "Nelson's Sparrow": ['Ammodramus nelsoni', 'Nelson Sparrow'],
    'Shiny Cowbird': ['Molothrus bonariensis', 'Molobrus bonariensis'],
    'Bronzed Cowbird': ['Callothrus aeneus', 'Tangavius aeneus'],
    'Blue-footed Booby': ['Sula nebouxi', 'Blue-footed Gannet'],
    'Paint-billed Crake': ['Neocrex erythrops', 'Porzana erythrops'],
    'Common Pauraque': ['Nyctidromus albicollis', 'Caprimulgus albicollis'],
    'Yellow-green Vireo': ['Vireosylvia flavoviridis'],
    'Clay-colored Thrush': ['Merula grayi', 'Clay-coloured Thrush'],
    'Curve-billed Thrasher': ['Harporhynchus curvirostris', 'Toxostoma curvirostris'],
    'Vega Gull': ['Larus vegae'], 'Slaty-backed Gull': ['Larus schistisagus'],
    'Mexican Duck': ['Anas diazi'], "Lilian's Lovebird": ['Agapornis lilianae', 'Nyasa Lovebird'],
    'Yellow-chevroned Parakeet': ['Psittacus chiriri', 'Brotogerys chiriri'],
    'Nanday Parakeet': ['Nandayus nenday', 'Psittacus nenday'],
    'Blue-crowned Parakeet': ['Conurus acuticaudatus', 'Psittacus acuticaudatus'],
    'Mitred Parakeet': ['Conurus mitratus'], 'Red-masked Parakeet': ['Conurus erythrogenys'],
    'Tropical Kingbird': ['Tyrannus melancholicus'],
    'Bronze Mannikin': ['Spermestes cucullatus', 'Lonchura cucullata']
}
choices = json.loads((WORK / 'harvest/choices.json').read_text())
fetch = h.helpers.Fetcher()
rows, metadata, errors, downloaded = [], [], [], {}
for row in choices:
    if row['requested'] not in TERMS:
        continue
    pages = {}
    terms = list(dict.fromkeys([row['requested']] + TERMS[row['requested']]))
    for term in terms:
        try:
            found = h.query(fetch, generator='search', gsrnamespace=6, gsrlimit=30,
                            gsrsearch='"' + term + '" -Crossley -filetype:pdf -filetype:djvu')
            for page in found:
                if h.eligible(page):
                    pages[page['pageid']] = page
        except Exception as error:
            errors.append({'requested': row['requested'], 'term': term, 'error': str(error)})
    ordered = sorted(pages.values(), key=lambda p: h.rank(p, row, row['research_names']), reverse=True)
    out = {**row, 'research_terms': terms, 'sources': [], 'candidate_count': len(ordered),
           'match_basis': 'historical-name discovery; modern species identity requires explicit review'}
    for page in ordered:
        metadata.append({'requested': row['requested'], 'key': row['key'], 'metadata': page})
    for page in ordered[:4]:
        try:
            pid = page['pageid']
            if pid not in downloaded:
                downloaded[pid] = h.download(fetch, page)
            out['sources'].append(downloaded[pid])
        except Exception as error:
            errors.append({'requested': row['requested'], 'term': page['title'], 'error': str(error)})
    rows.append(out)
    h.helpers.write_json(h.OUT / 'choices.json', rows)
    h.helpers.write_json(h.OUT / 'metadata.json', metadata)
    h.helpers.write_csv(h.OUT / 'errors.csv', errors)
    print(row['requested'], 'sources:', len(out['sources']), flush=True)
h.helpers.write_json(h.OUT / 'summary.json', {'researched': len(rows),
    'rows_with_candidate': sum(bool(r['sources']) for r in rows), 'errors': len(errors),
    'approved_artwork': 0, 'warning': 'Discovery names are not classifier aliases or identity approval.'})
