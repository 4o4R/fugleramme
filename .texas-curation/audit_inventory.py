#!/usr/bin/env python3
# .texas-curation/audit_inventory.py
# Fork-only, offline audit of the exact model labels and cached source metadata.
# A taxonomic category match is a discovery lead, never visual/license approval.
from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.texas-curation'
OUT = WORK / 'audit'
REASONS = {
    'White-winged Scoter': 'No exact label in the pinned BirdNET v2.4 corpus. Do not substitute Velvet Scoter.',
    'Masked Duck': 'No exact label in the pinned BirdNET v2.4 corpus. Artwork does not add a classifier class.',
    "Lilian's Lovebird": 'No exact label in the pinned BirdNET v2.4 corpus. Verify occurrence/escape status separately.',
    'Western Flycatcher': 'Broad modern taxon overlaps Pacific-slope/Cordilleran labels; document split/lump policy before mapping.',
    'Mew Gull': 'Historical broad common name; distinguish Common Gull from Short-billed Gull rather than fuzzy-match.',
    "Thayer's Gull": 'Historical split/lump with Iceland Gull; require an explicit mapping and plumage decision.',
    'Vega Gull': 'Modern split versus historical Herring Gull grouping; no silent substitution.',
    'Ivory-billed Woodpecker': 'Historical/conservation-status record, not ordinary current local coverage; no exact v2.4 label.',
}


def text(metadata: dict, key: str) -> str:
    return html.unescape(re.sub(r'<[^>]+>', ' ', metadata.get(key, {}).get('value', ''))).strip()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for row in rows for k in row)) or ['status']
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    inventory = list(csv.DictReader((WORK / 'output/inventory.csv').open(encoding='utf-8')))
    pages = json.loads((WORK / 'pd-sources/commons-metadata.json').read_text())
    aliases = json.loads((ROOT / 'assets/birdnet_aliases.json').read_text())
    candidates = []
    seen_keys = defaultdict(list)
    for row in inventory:
        if not row['key']:
            continue
        seen_keys[row['key']].append(row['requested'])
        names = {row['scientific'], row['model_scientific']}
        names.update(old for old, new in aliases.items() if new == row['scientific'])
        for page in pages:
            info = (page.get('imageinfo') or [{}])[0]
            metadata = info.get('extmetadata', {})
            if text(metadata, 'LicenseShortName').lower() not in {
                'public domain', 'cc0', 'cc0 1.0', 'pdm', 'public domain mark'
            }:
                continue
            categories = text(metadata, 'Categories').split('|')
            matches = [(name, category) for name in sorted(names) for category in categories
                       if category == name or category.startswith(name + ' (')]
            if not matches:
                continue
            candidates.append({
                'requested': row['requested'], 'key': row['key'],
                'matched_scientific_name': matches[0][0], 'matched_category': matches[0][1],
                'file_title': page['title'], 'source_page': info.get('descriptionurl', ''),
                'license_metadata': text(metadata, 'LicenseShortName'),
                'artist': text(metadata, 'Artist'), 'credit': text(metadata, 'Credit'),
                'original_sha1': info.get('sha1', ''), 'prior_status': row['asset_status'],
                'review_status': 'Candidate only; confirm exact specimen, taxonomy, scan rights and crop.'
            })
    index = defaultdict(list)
    for candidate in candidates:
        index[candidate['key']].append(candidate)
    manifest = json.loads((ROOT / 'assets/artwork/classic/manifest.json').read_text())
    actual_files = list((ROOT / 'assets/artwork/classic/birds').glob('*.webp'))
    shipping_keys = {re.sub(r'-\d+$', '', path.stem) for path in actual_files}
    reviewed = WORK / 'approved-ledger.json'
    approved = json.loads(reviewed.read_text()) if reviewed.exists() else []
    approved_keys = {row['key'] for row in approved}
    annotated = []
    for original in inventory:
        row = dict(original)
        key = row['key']
        row['source_candidate_count'] = len(index.get(key, []))
        if not key:
            row['current_status'] = 'taxonomy review'
            row['note'] = REASONS.get(row['requested'], row.get('note', ''))
        elif key in approved_keys and key in shipping_keys:
            row['current_status'] = 'new reviewed artwork'
        elif original['asset_status'] == 'existing upstream' and key in shipping_keys:
            row['current_status'] = 'existing upstream artwork'
        elif key in index:
            row['current_status'] = 'source candidates; artwork not complete'
        else:
            row['current_status'] = 'source research still needed'
        annotated.append(row)
    counts = Counter(row['current_status'] for row in annotated)
    summary = {
        'requested_rows': len(inventory), 'resolved_rows': sum(bool(r['key']) for r in inventory),
        'unique_resolved_keys': len(seen_keys),
        'duplicate_resolved_keys': {k: v for k, v in seen_keys.items() if len(v) > 1},
        'unresolved_rows': sum(not r['key'] for r in inventory),
        'source_candidate_records': len(candidates), 'candidate_species': len(index),
        'additional_candidate_species_previously_missing_source': len({c['key'] for c in candidates if c['prior_status'] == 'needs source'}),
        'coverage_status': dict(counts),
        'scope': '534 regional planning entries; NOT 534 common birds at either ZIP code, NOT measured local abundance.',
        'completion_rule': 'A source URL, successful download or technically valid image is not completed artwork.'
    }
    assert len(inventory) == 534
    assert sum(counts.values()) == len(inventory)
    for row in approved:
        assert 'birds/' + row['asset'] in manifest, row['asset']
    write_json(OUT / 'summary.json', summary)
    write_json(OUT / 'metadata-candidates.json', candidates)
    write_csv(OUT / 'metadata-candidates.csv', candidates)
    write_csv(OUT / 'coverage.csv', annotated)
    write_csv(OUT / 'taxonomy-review.csv', [r for r in annotated if not r['key']])
    (OUT / 'README.md').write_text(
        '# Texas contribution audit\n\n'
        'This is a broad regional artwork worklist, not a ZIP-level occurrence estimate or top-bird ranking. '
        'Rarities, historical records and introduced/escaped birds need distinct occurrence review.\n\n'
        '`coverage.csv` separates exact model mapping, source discovery, inherited art and newly reviewed art. '
        '`taxonomy-review.csv` preserves unresolved species; it does not fabricate new model classes. '
        '`metadata-candidates.csv` uses exact scientific category matches in cached Commons metadata. '
        'A multi-species plate can match several rows: each specimen still needs independent selection.\n\n'
        'The legacy `.texas-build-reports/` directory is historical. Its generated/ready counts are superseded by this audit.\n',
        encoding='utf-8'
    )
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
