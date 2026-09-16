#!/usr/bin/env python3
# .texas-curation/finalize_contribution.py
# Fork-only assembly. The contribution itself contains only artwork and attribution.
# A segmentation model selects original scan pixels; it does not generate artwork.
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.request import Request, urlopen

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFilter


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_out(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def csv_out(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for row in rows for k in row)) or ['status']
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def polygon_alpha(size: tuple[int, int], points: list, basis: list) -> Image.Image:
    width, height = size
    alpha = Image.new('L', (width * 4, height * 4))
    ImageDraw.Draw(alpha).polygon(
        [(x / basis[0] * alpha.width, y / basis[1] * alpha.height) for x, y in points],
        fill=255,
    )
    return alpha.resize(size, Image.Resampling.LANCZOS)


def reviewed_alpha(source: Image.Image, spec: dict, predictor: object) -> Image.Image:
    assert list(source.size) == spec['source_size'], spec['key']
    crop = source.crop(spec['crop'])
    if 'polygon' in spec:
        alpha = polygon_alpha(crop.size, spec['polygon'], spec['guide_size'])
        alpha = alpha.filter(ImageFilter.GaussianBlur(.3))
    else:
        sx, sy = source.width / spec['basis'][0], source.height / spec['basis'][1]
        b = spec['box']
        x0, y0, x1, y1 = b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy
        pad = max(20, int(max(x1 - x0, y1 - y0) * .22))
        region = (max(0, int(x0 - pad)), max(0, int(y0 - pad)),
                  min(source.width, int(x1 + pad)), min(source.height, int(y1 + pad)))
        predictor.set_image(np.asarray(source.crop(region)))
        box = np.array([x0 - region[0], y0 - region[1], x1 - region[0], y1 - region[1]])
        points, labels = [], []
        r = spec['crop']
        bw, bh = spec['guide_size']
        for label, kind in [(1, 'positive'), (0, 'negative')]:
            for x, y in spec.get('prompt', {}).get(kind, []):
                points.append([r[0] + x / bw * (r[2] - r[0]) - region[0],
                               r[1] + y / bh * (r[3] - r[1]) - region[1]])
                labels.append(label)
        with torch.inference_mode():
            masks, _, _ = predictor.predict(
                box=box, point_coords=np.array(points) if points else None,
                point_labels=np.array(labels) if points else None, multimask_output=True,
            )
        full = Image.new('L', source.size)
        full.paste(Image.fromarray(masks[spec['mask_index']].astype(np.uint8) * 255), region[:2])
        mask = np.asarray(full.crop(r)) > 0
        arr = np.asarray(crop)
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB).astype(float)
        border = np.concatenate([lab[:3].reshape(-1, 3), lab[-3:].reshape(-1, 3),
                                 lab[:, :3].reshape(-1, 3), lab[:, -3:].reshape(-1, 3)])
        foreground = np.linalg.norm(lab - np.median(border, axis=0), axis=2) > 20
        def gate(poly: list) -> np.ndarray:
            return np.asarray(polygon_alpha(crop.size, poly, spec['guide_size'])) > 127
        for poly in spec.get('retain_ink', []):
            mask |= foreground & gate(poly)
        for poly in spec.get('retain_all', []):
            mask |= gate(poly)
        for rule in spec.get('color_erase', []):
            assert rule['condition'] == 'red_exceeds_blue'
            mask &= ~(gate(rule['polygon']) & (
                arr[:, :, 0].astype(int) - arr[:, :, 2].astype(int) > rule['threshold']))
        count, components, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        if count > 1:
            cutoff = max(3, stats[1:, cv2.CC_STAT_AREA].max() * .0008)
            ids = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] >= cutoff)
            mask = np.isin(components, ids[ids > 0])
        alpha = Image.fromarray(mask.astype(np.uint8) * 255)
        alpha = alpha.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(.4))
    # This is a hard gate: do not ship a mask that merely resembles the reviewed one.
    assert digest(alpha.tobytes()) == spec['alpha_raw_sha256'], 'Mask drift: ' + spec['key']
    return alpha


def fetch_scan(url: str, expected_sha: str) -> bytes:
    for attempt in range(4):
        try:
            request = Request(url, headers={'User-Agent': 'Fugleramme-Texas-Curation/3.0 (https://github.com/4o4R/fugleramme)'})
            with urlopen(request, timeout=90) as response:
                data = response.read()
            assert digest(data) == expected_sha, 'Source scan changed: ' + url
            return data
        except (OSError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError('Unreachable download state')


def contact_sheet(ledger: list[dict], out: Path) -> None:
    canvas = Image.new('RGB', (1600, 1800), (242, 237, 226))
    draw = ImageDraw.Draw(canvas)
    for index, row in enumerate(ledger):
        x, y = (index % 4) * 400, (index // 4) * 360
        image = Image.open(out / 'previews' / (row['key'] + '.png')).convert('RGB')
        image.thumbnail((340, 310), Image.Resampling.LANCZOS)
        canvas.paste(image, (x + (400 - image.width) // 2, y))
        draw.text((x + 10, y + 313), row['common'], fill='black', font_size=18)
        draw.text((x + 10, y + 337), row['scientific'], fill='black', font_size=14)
    canvas.save(out / 'contact-sheet.jpg', quality=95)


def coverage(args: argparse.Namespace, shipped: list[dict], before: set[str], after: set[str], normalize: object) -> dict:
    work = args.inputs / '.texas-curation'
    original = list(csv.DictReader((work / 'output/inventory.csv').open(encoding='utf-8')))
    harvest = {row['requested']: row for row in json.loads((args.harvest / 'choices.json').read_text())}
    historical = {row['requested']: row for row in json.loads((args.historical / 'choices.json').read_text())}
    errors = list(csv.DictReader((args.historical / 'errors.csv').open(encoding='utf-8')))
    error_counts = Counter(row['requested'] for row in errors)
    masses = {normalize(row['scientific_name']): float(row['mass_g']) for row in csv.DictReader((args.repo / 'assets/bird_sizes.csv').open())}
    model = {line.split('_', 1)[0] for line in (args.repo / 'assets/birdnet_labels_v2.4.txt').read_text().splitlines() if '_' in line}
    added = {row['key'] for row in shipped}
    records, source_records = [], []
    for old in original:
        key = old['key']
        if key:
            assert old['model_scientific'] in model
            assert normalize(old['model_scientific']) == key
        sources = {}
        for collection, label in [(harvest, 'scientific-name search'), (historical, 'historical-name search')]:
            for source in collection.get(old['requested'], {}).get('sources', []):
                sources[source['pageid']] = (source, label)
        for source, label in sources.values():
            source_records.append({'planning_order': int(old['rank']), 'requested_name': old['requested'],
                'canonical_key': key, 'search_basis': label, 'review_status': 'Source candidate only; not an approved specimen or finished asset.', **source})
        if key in added:
            status = 'new reviewed artwork'
        elif key and key in before:
            status = 'existing upstream artwork'
        elif not key:
            status = 'taxonomy or model support unresolved'
        elif sources:
            status = 'source candidate; artwork unfinished'
        elif error_counts[old['requested']]:
            status = 'source search incomplete; query errors'
        else:
            status = 'source not found in searches performed'
        records.append({'planning_order': int(old['rank']), 'requested_name': old['requested'],
            'model_scientific': old['model_scientific'], 'model_common': old['model_common'],
            'canonical_scientific': old['scientific'], 'canonical_key': key,
            'name_match_basis': old['taxonomy_status'], 'status': status,
            'body_mass_g': masses.get(key, ''), 'mass_present': bool(key and masses.get(key, 0) > 0),
            'source_candidate_count': len(sources), 'historical_search_errors': error_counts[old['requested']],
            'note': old.get('note', '')})
    assert len(records) == 534
    assert len({r['requested_name'] for r in records}) == 534
    assert all(r['mass_present'] for r in records if r['canonical_key'])
    report = args.output / 'audit'
    csv_out(report / 'coverage.csv', records)
    csv_out(report / 'source-candidates.csv', source_records)
    csv_out(report / 'taxonomy-review.csv', [r for r in records if not r['canonical_key']])
    csv_out(report / 'source-search-errors.csv', errors)
    csv_out(report / 'remaining-artwork.csv', [r for r in records if r['canonical_key'] not in after])
    summary = {'requested_rows': len(records), 'resolved_model_rows': sum(bool(r['canonical_key']) for r in records),
        'unresolved_model_rows': sum(not r['canonical_key'] for r in records),
        'existing_covered_rows': sum(r['canonical_key'] in before for r in records),
        'new_reviewed_assets': len(shipped), 'covered_rows_after': sum(r['canonical_key'] in after for r in records),
        'remaining_uncovered_rows': sum(r['canonical_key'] not in after for r in records),
        'missing_masses_for_resolved_rows': sum(not r['mass_present'] for r in records if r['canonical_key']),
        'candidate_source_records': len(source_records), 'historical_search_error_records': len(errors),
        'status_counts': dict(Counter(r['status'] for r in records)),
        'scope': '534 regional planning entries. Not measured ZIP-level occurrence, abundance, or a list of 534 common local birds.',
        'completion': 'The reviewed artwork increment and coverage audit are complete; remaining artwork is explicitly unfinished.'}
    json_out(report / 'summary.json', summary)
    (report / 'README.md').write_text(
        '# Regional coverage audit\n\n'
        '`coverage.csv` accounts for every requested row. `source-candidates.csv` preserves the exact scan metadata, rights statements and hashes. '
        'A candidate source is not a finished asset: it may contain multiple species, an unsuitable crop, or a mistaken taxonomic category. '
        '`remaining-artwork.csv` and `taxonomy-review.csv` are open worklists, not completed artwork.\n\n'
        'All resolved rows already have a positive body mass in the upstream dataset. No masses, model labels, or runtime aliases were fabricated or changed. '
        'The eight unresolved rows require classifier-support or explicit split/lump decisions; adding an illustration does not add a model class.\n\n'
        'Historical searches returned query errors for some terms. These errors remain in `source-search-errors.csv`; a negative result does not establish that no historical illustration exists.\n\n'
        'The third-party Audubon discovery catalog has title/plate misalignment in its late plate block. '
        'Examples rejected during review: the entry assigning Band-tailed Pigeon to plate 368 (the scan is Rock Grous), '
        'Sharp-shinned Hawk to 375 (Lesser Red-Poll), and Red-cockaded Woodpecker to 390 (a mixed finch/sparrow plate). '
        'Final asset identity is tied to the selected source scan, not to that catalog title.\n', encoding='utf-8')
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ['repo', 'inputs', 'review', 'pd', 'mask_tools', 'harvest', 'historical', 'output']:
        parser.add_argument('--' + name.replace('_', '-'), required=True, type=Path)
    args = parser.parse_args()
    for key, value in vars(args).items():
        setattr(args, key, value.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(args.repo), str(args.repo / 'src'), str(args.mask_tools)]
    from mobile_sam import SamPredictor, sam_model_registry
    from tools.add_bird import prepare, preview, write_plate
    from fugleramme.names import normalize
    torch.set_num_threads(4)
    cv2.setNumThreads(1)
    predictor = SamPredictor(sam_model_registry['vit_t'](checkpoint=str(args.mask_tools / 'mobile_sam.pt')).eval())
    specs = {r['key']: r for r in json.loads((args.inputs / '.texas-curation/final-mask-spec.json').read_text())}
    originals = list(csv.DictReader((args.review / 'review-ledger.csv').open(encoding='utf-8')))
    assert len(originals) == 18 and len(specs) == 7
    classic = args.repo / 'assets/artwork/classic'
    birds = classic / 'birds'
    before = {re.sub(r'-\d+$', '', p.stem) for p in birds.glob('*.webp')}
    manifest_path = classic / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    protected = {p: digest((args.repo / p).read_bytes()) for p in ['assets/bird_sizes.csv', 'assets/birdnet_aliases.json', 'assets/birdnet_labels_v2.4.txt']}
    pd_rows = {int(r['plate']): r for r in csv.DictReader((args.pd / 'sources.csv').open())}
    ledger = []
    for old in originals:
        key = old['key']
        assert normalize(old['scientific']) == key
        if key in before:
            print('Already covered upstream; no overwrite:', key, flush=True)
            continue
        dest = birds / old['asset']
        assert not dest.exists()
        if key not in specs:
            data = (args.review / 'images' / old['asset']).read_bytes()
            assert digest(data) == old['sha256'], key
            dest.write_bytes(data)
            method = 'previous high-resolution cutout; source and final composition reviewed'
        else:
            spec = specs[key]
            source_path = args.pd / 'sources' / f"{int(old['plate']):03d}.png"
            source_data = source_path.read_bytes()
            assert digest(source_data) == pd_rows[int(old['plate'])]['normalized_sha256']
            alpha = reviewed_alpha(Image.open(io.BytesIO(source_data)).convert('RGB'), spec, predictor)
            scan_data = fetch_scan(old['source_download'], old['source_sha256'])
            scan = Image.open(io.BytesIO(scan_data)).convert('RGB')
            sx, sy = scan.width / spec['source_size'][0], scan.height / spec['source_size'][1]
            x0, y0, x1, y1 = spec['crop']
            box = (round(x0 * sx), round(y0 * sy), round(x1 * sx), round(y1 * sy))
            image = scan.crop(box).convert('RGBA')
            image.putalpha(alpha.resize(image.size, Image.Resampling.LANCZOS))
            temp = args.output / (key + '.png')
            image.save(temp)
            final = prepare(temp)
            write_plate(final, dest)
            temp.unlink()
            method = 'exact-hash reviewed alpha on original high-resolution scan pixels'
        image = Image.open(dest).convert('RGBA')
        assert max(image.size) <= 1200 and min(image.size) >= 100, key
        assert image.getchannel('A').getextrema() == (0, 255), key
        manifest['birds/' + old['asset']] = {'source': 'audubon-pittsburgh', 'url': old['source_page']}
        preview(image, args.output / 'previews' / (key + '.png'))
        row = {k: old[k] for k in ['key', 'common', 'scientific', 'plate', 'asset', 'source_page', 'source_download', 'source_sha256', 'commons_original_sha1', 'license', 'artist', 'credit']}
        row.update(method=method, width=image.width, height=image.height, bytes=dest.stat().st_size,
                   asset_sha256=digest(dest.read_bytes()), technical_checks='passed',
                   composition_review='reviewed against source; final high-resolution preview supplied')
        if key in specs:
            row['reviewed_alpha_sha256'] = specs[key]['alpha_raw_sha256']
        ledger.append(row)
        print('Built reviewed asset:', key, flush=True)
    json_out(manifest_path, dict(sorted(manifest.items())))
    attribution = classic / 'ATTRIBUTION.md'
    text = attribution.read_text()
    if '`audubon-pittsburgh`' not in text:
        text += ('\n**Audubon (Pittsburgh)** - *The Birds of America* (1827-1838), by **John James Audubon**, '
                 'engraved and coloured by **Robert Havell**. [University of Pittsburgh scans](https://digital.library.pitt.edu/collection/audubons-birds-america), '
                 'via the Wikimedia Commons file pages linked in the manifest. Public-domain originals and scans (Commons PD-Art / PD-old); '
                 'these cutouts retain source pixels without generative repainting. Manifest key: `audubon-pittsburgh`.\n')
    attribution.write_text(text, encoding='utf-8')
    for path, sha in protected.items():
        assert digest((args.repo / path).read_bytes()) == sha, path
    after = {re.sub(r'-\d+$', '', p.stem) for p in birds.glob('*.webp')}
    summary = coverage(args, ledger, before, after, normalize)
    summary['protected_data_sha256'] = protected
    summary['upstream_base_sha'] = '1423668dcab119e372c45ea93dc54154d1352a78'
    json_out(args.output / 'summary.json', summary)
    csv_out(args.output / 'asset-provenance.csv', ledger)
    json_out(args.output / 'asset-provenance.json', ledger)
    contact_sheet(ledger, args.output)
    patch = args.output / 'patch/assets/artwork/classic'
    (patch / 'birds').mkdir(parents=True, exist_ok=True)
    for row in ledger:
        shutil.copy2(birds / row['asset'], patch / 'birds' / row['asset'])
    for filename in ['manifest.json', 'ATTRIBUTION.md']:
        shutil.copy2(classic / filename, patch / filename)
    (args.output / 'README.md').write_text(
        '# Reviewed Texas artwork contribution\n\n'
        'The `patch/` directory contains the reviewed artwork increment and its attribution. '
        'Apply it only to the recorded upstream base, or merge the manifest entries rather than overwriting a newer manifest. '
        '`previews/` and `contact-sheet.jpg` show the exact exported files through the frame paper renderer.\n\n'
        '`audit/` accounts for all 534 planning rows and explicitly lists unfinished artwork and taxonomy cases. '
        '`asset-provenance.csv` records the original scans, rights metadata, and source/output hashes. '
        'The selected historic artwork is not AI-generated. Non-generative segmentation and manual selection change alpha only.\n\n'
        'This package does not include fonts, detector software, machine configuration, or changes to application code. '
        'Passing software tests does not independently certify zoological identity, historical taxonomy, or legal status in every jurisdiction.\n', encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
