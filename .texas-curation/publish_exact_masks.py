#!/usr/bin/env python3
# .texas-curation/publish_exact_masks.py
# Fork-only publication of reviewed selections from original historical pixels.
# A mask hash is an approval boundary: rebuilding a different selection fails.
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import sys
import zlib
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.ndimage import binary_fill_holes

PAPER = (240, 236, 229)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def polygon(shape: tuple[int, int], points: list) -> np.ndarray:
    height, width = shape
    image = Image.new('L', (width, height))
    ImageDraw.Draw(image).polygon(
        [(round(x * width / 100), round(y * height / 100)) for x, y in points], fill=255
    )
    return np.asarray(image) > 0


def rebuild_mask(item: dict, guided: Path, shape: tuple[int, int]) -> np.ndarray:
    # Custom manually edited masks can be stored as bounded, compressed bitmaps.
    if 'mask_packbits_zlib_base64' in item:
        packed = zlib.decompress(base64.b64decode(item['mask_packbits_zlib_base64']))
        expected = (shape[0] * shape[1] + 7) // 8
        if len(packed) != expected:
            raise ValueError('Invalid packed mask length')
        mask = np.unpackbits(np.frombuffer(packed, dtype=np.uint8))[:shape[0] * shape[1]]
        return mask.reshape(shape).astype(bool)
    spec = item['selection']
    mask = np.zeros(shape, dtype=bool)
    for variant in spec.get('variants', [spec.get('variant', 1)]):
        path = guided / f"{item['rank']:03d}-v{variant}-alpha.png"
        incoming = np.asarray(Image.open(path)) > 127
        if incoming.shape != shape:
            raise ValueError(f'Mask/source dimensions disagree: {path}')
        mask |= incoming
    if spec.get('invert'):
        mask = ~mask
    for points in spec.get('add', []):
        mask |= polygon(shape, points)
    for points in spec.get('erase', []):
        mask &= ~polygon(shape, points)
    if spec.get('keep'):
        mask &= polygon(shape, spec['keep'])
    if spec.get('close', 0):
        size = int(spec['close'])
        mask = cv2.morphologyEx(mask.astype('uint8'), cv2.MORPH_CLOSE,
                               np.ones((size, size), np.uint8)).astype(bool)
    mask = binary_fill_holes(mask)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype('uint8'), 8)
    if spec.get('largest', True) and count > 1:
        mask = labels == (1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])))
    for points in spec.get('recover', []):
        mask |= polygon(shape, points)
    return mask


def make_cutout(source: Image.Image, mask: np.ndarray) -> Image.Image:
    # All visible RGB is sampled from the source; only alpha and the paper halo
    # are changed. Source occlusions are not painted over or hallucinated.
    alpha = Image.fromarray(mask.astype('uint8') * 255)
    if alpha.getbbox() is None:
        raise ValueError('Empty reviewed mask')
    rgba = source.convert('RGBA')
    rgba.putalpha(alpha)
    rgba = rgba.crop(alpha.getbbox())
    rgba.putalpha(rgba.getchannel('A').filter(ImageFilter.GaussianBlur(.35)))
    radius = max(4, round(max(rgba.size) * .009))
    pad = radius + 3
    base = Image.new('RGBA', (rgba.width + 2 * pad, rgba.height + 2 * pad), (*PAPER, 0))
    base.paste(rgba, (pad, pad))
    halo = Image.new('RGBA', base.size, (*PAPER, 0))
    halo.putalpha(base.getchannel('A').filter(ImageFilter.MaxFilter(radius * 2 + 1)))
    return Image.alpha_composite(halo, base)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ('repo', 'guided', 'sources', 'approved', 'inventory', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(repo), str(repo / 'src')]
    from fugleramme.names import normalize
    from tools.add_bird import prepare, write_plate

    approval = json.loads(args.approved.read_text())
    lookup = {(r['rank'], r['variant']): r for r in json.loads((args.guided / 'ledger.json').read_text())}
    classic = repo / 'assets/artwork/classic'
    birds = classic / 'birds'
    manifest_path = classic / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    existing = {re.sub(r'-\d+$', '', path.stem) for path in birds.glob('*.webp')}
    shipped, skipped = [], []
    for item in approval['reviewed_rows']:
        variant = item.get('selection', {}).get('variants', [item.get('selection', {}).get('variant', 1)])[0]
        record = item.get('record') or lookup[item['rank'], variant]
        key = item['key']
        if record['key'] != key or normalize(record['scientific']) != key:
            raise ValueError(f'Unapproved taxon or alias change: {key}')
        if key in existing:
            skipped.append({'key': key, 'reason': 'already on current main; not overwritten'})
            continue
        meta = record['source']
        if meta['license'] not in {'Public domain', 'CC0'}:
            raise ValueError(f'Unapproved source terms for {key}')
        matches = list(args.sources.rglob(str(meta['pageid']) + '.png'))
        if not matches:
            raise FileNotFoundError(f"Original source unavailable: {meta['pageid']}")
        source_bytes = matches[0].read_bytes()
        if digest(source_bytes) != record['source_sha256']:
            raise ValueError(f'Source pixels changed: {key}')
        source = Image.open(matches[0]).convert('RGB').crop(record['region'])
        mask = rebuild_mask(item, args.guided, (source.height, source.width))
        mask_hash = digest(mask.astype('uint8').tobytes())
        if mask_hash != item['mask_sha256']:
            raise ValueError(f'Rebuilt mask is not the visually reviewed selection: {key}')
        preview = out / (key + '.png')
        make_cutout(source, mask).save(preview)
        destination = birds / (key + '.webp')
        write_plate(prepare(preview), destination)
        with Image.open(destination) as image:
            if image.mode != 'RGBA' or image.getchannel('A').getextrema() != (0, 255) or max(image.size) > 1200:
                raise ValueError(f'Invalid final artwork encoding: {key}')
        manifest['birds/' + destination.name] = {'source': item['source_key'], 'url': meta['source_page']}
        existing.add(key)
        shipped.append({
            'rank': record['rank'], 'common': record['common'], 'scientific': record['scientific'],
            'key': key, 'asset': 'assets/artwork/classic/birds/' + destination.name,
            'source_key': item['source_key'], 'source_page': meta['source_page'],
            'source_title': meta['file_title'], 'license': meta['license'],
            'source_sha256': record['source_sha256'], 'source_region': record['region'],
            'mask_sha256': mask_hash, 'asset_sha256': digest(destination.read_bytes()),
            'identity_evidence': item.get('identity_evidence', ''),
            'review': item.get('review', 'Source specimen and finished cutout visually reviewed; source RGB retained.'),
        })
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    attr_path = classic / 'ATTRIBUTION.md'
    attribution = attr_path.read_text()
    for key, text in approval.get('attributions', {}).items():
        if f'Manifest key: `{key}`' not in attribution:
            attribution = attribution.rstrip() + '\n\n' + text + '\n'
    for item in shipped:
        if f"`{item['source_key']}`" not in attribution:
            raise ValueError('Missing work attribution: ' + item['source_key'])
    attr_path.write_text(attribution)

    # Persist the state beside the application, rather than relying on expiring
    # build artifacts or a chat's estimated completion count.
    reports = repo / 'docs/texas-coverage'
    reports.mkdir(parents=True, exist_ok=True)
    ledger_path = reports / 'reviewed-assets.json'
    prior = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    by_key = {r['key']: r for r in prior}
    by_key.update({r['key']: r for r in shipped})
    ledger_path.write_text(json.dumps(sorted(by_key.values(), key=lambda r: r['rank']), indent=2) + '\n')
    inventory = list(csv.DictReader(args.inventory.open()))
    for row in inventory:
        row['artwork_status'] = ('taxonomy-review' if not row.get('key') else
                                 'present' if row['key'] in existing else 'missing')
        entry = manifest.get('birds/' + row.get('key', '') + '.webp', {})
        row['shipped_source_page'] = entry.get('url', '')
    with (reports / 'coverage.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(inventory[0]))
        writer.writeheader()
        writer.writerows(inventory)
    summary = {'worklist_entries': len(inventory),
               'artwork_present': sum(r['artwork_status'] == 'present' for r in inventory),
               'artwork_missing': sum(r['artwork_status'] == 'missing' for r in inventory),
               'taxonomy_review': sum(r['artwork_status'] == 'taxonomy-review' for r in inventory),
               'new_this_batch': len(shipped), 'skipped_existing': skipped,
               'batch': approval['batch']}
    (reports / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (reports / 'README.md').write_text(
        '# Texas regional artwork coverage\n\n'
        'This is the inherited 534-entry regional planning worklist, not a ranking of common birds at a ZIP code. '
        'Rare, introduced, historical and questionable regional entries remain explicit research cases. '
        'A present filename measures artwork availability, not local occurrence or detector capability.\n\n'
        '`coverage.csv` accounts for every worklist row. `reviewed-assets.json` records source pixels, '
        'selected region, mask and asset hashes for batches published through the exact-mask workflow. '
        'It does not retrospectively claim a new visual review of all older artwork. '
        'Classifier labels and runtime aliases are not modified to inflate coverage.\n', encoding='utf-8')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (out / 'provenance.json').write_text(json.dumps(shipped, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
