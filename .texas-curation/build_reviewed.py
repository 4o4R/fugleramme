#!/usr/bin/env python3
# .texas-curation/build_reviewed.py
# Fork-only curation tooling. Review output before promoting anything into classic.
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.texas-curation'
OUT = WORK / 'review-build'
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from fugleramme.names import normalize
from tools.add_bird import prepare, preview, write_plate

spec = importlib.util.spec_from_file_location('collection_helpers', WORK / 'collect.py')
assert spec and spec.loader
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
PAPER = (240, 236, 229)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_mask(image: Image.Image, choice: dict) -> Image.Image:
    """Infer only paper outside a visually selected bird, or use a traced polygon.

    Work at the same 1800-pixel plate size as the reviewed reference. Applying
    this fixed-resolution mask to the higher-resolution scan preserves the
    selected outline while improving the bird's colour/feather detail.
    """
    full = image.convert('RGB')
    full.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
    width, height = full.size
    bw, bh = choice['basis']
    if choice['method'] == 'polygon':
        factor = 4
        mask = Image.new('L', (width * factor, height * factor))
        draw = ImageDraw.Draw(mask)
        points = [(round(x * width / bw * factor), round(y * height / bh * factor))
                  for x, y in choice['polygon']]
        draw.polygon(points, fill=255)
        return mask.resize((width, height), Image.Resampling.LANCZOS)

    x0, y0, x1, y1 = choice['box']
    rect = (round(x0 * width / bw), round(y0 * height / bh),
            round(x1 * width / bw), round(y1 * height / bh))
    crop = np.asarray(full.crop(rect))
    lab = cv2.cvtColor(crop, cv2.COLOR_RGB2LAB).astype(float)
    border = np.concatenate([lab[:2].reshape(-1, 3), lab[-2:].reshape(-1, 3),
                             lab[:, :2].reshape(-1, 3), lab[:, -2:].reshape(-1, 3)])
    distance = np.linalg.norm(lab - np.median(border, axis=0), axis=2)
    mask = (distance > 20).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if count < 2:
        raise ValueError('Selected crop has no foreground.')
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = (labels == biggest).astype(np.uint8)
    # Fill internal pale plumage. Do not punch white feathering out as paper.
    outside = np.pad(1 - mask, 1, constant_values=1)
    cv2.floodFill(outside, None, (0, 0), 2)
    mask = (outside[1:-1, 1:-1] != 2).astype(np.uint8) * 255
    alpha = Image.fromarray(mask).filter(ImageFilter.GaussianBlur(0.45))
    full_mask = Image.new('L', (width, height))
    full_mask.paste(alpha, rect[:2])
    return full_mask


def cut(image: Image.Image, choice: dict) -> Image.Image:
    mask = selected_mask(image, choice).resize(image.size, Image.Resampling.LANCZOS)
    bbox = mask.getbbox()
    if bbox is None:
        raise ValueError('Empty selection.')
    # Crop first; then add transparent padding BEFORE dilating the paper halo.
    rgba = image.crop(bbox).convert('RGBA')
    rgba.putalpha(mask.crop(bbox))
    radius = max(6, round(max(rgba.size) * 0.014))
    padding = radius + 4
    padded = Image.new('RGBA', (rgba.width + padding * 2, rgba.height + padding * 2), (*PAPER, 0))
    padded.paste(rgba, (padding, padding))
    halo = Image.new('RGBA', padded.size, (*PAPER, 0))
    halo.putalpha(padded.getchannel('A').filter(ImageFilter.MaxFilter(radius * 2 + 1)))
    return Image.alpha_composite(halo, padded)


def validate(path: Path, key: str, known: set[str], masses: set[str]) -> dict:
    if key not in known or normalize(key) != key:
        raise ValueError(f'Noncanonical or unknown model key: {key}')
    if key not in masses:
        raise ValueError(f'Missing body mass: {key}')
    with Image.open(path) as image:
        image.load()
        if image.format != 'WEBP' or image.mode != 'RGBA':
            raise ValueError(f'Expected transparent WebP: {path}')
        alpha = np.asarray(image.getchannel('A'))
        if max(image.size) > 1200 or min(image.size) < 80:
            raise ValueError(f'Unexpected image dimensions: {image.size}')
        if alpha.min() != 0 or alpha.max() != 255:
            raise ValueError('Missing transparency or opaque subject.')
        return {'width': image.width, 'height': image.height, 'bytes': path.stat().st_size,
                'sha256': digest(path), 'canonical_name': True, 'body_mass_present': True,
                'technical_checks': 'passed', 'final_visual_review': 'pending'}


def contact_sheets(rows: list[dict]) -> None:
    font = ImageFont.truetype('DejaVuSans.ttf', 18)
    for offset in range(0, len(rows), 12):
        canvas = Image.new('RGB', (1600, 1240), PAPER)
        draw = ImageDraw.Draw(canvas)
        for n, row in enumerate(rows[offset:offset + 12]):
            image = Image.open(OUT / 'images' / row['asset']).convert('RGBA')
            image.thumbnail((365, 340), Image.Resampling.LANCZOS)
            x, y = (n % 4) * 400, (n // 4) * 410
            canvas.paste(image, (x + (400 - image.width) // 2, y + 8), image)
            draw.text((x + 12, y + 357), row['common'], font=font, fill='black')
            draw.text((x + 12, y + 382), f"Plate {row['plate']} | {row['width']} x {row['height']}", font=font, fill='black')
        canvas.save(OUT / f'contact-{offset // 12 + 1:02d}.jpg', quality=93)


def main() -> None:
    choices = json.loads((WORK / 'selections.json').read_text())
    assert len({c['key'] for c in choices}) == len(choices)
    sources = {int(r['plate']): r for r in csv.DictReader((WORK / 'pd-sources/sources.csv').open())}
    inventory = {r['key']: r for r in csv.DictReader((WORK / 'output/inventory.csv').open()) if r['key']}
    known = {normalize(line.split('_', 1)[0])
             for line in (ROOT / 'assets/birdnet_labels_v2.4.txt').read_text().splitlines() if '_' in line}
    with (ROOT / 'assets/bird_sizes.csv').open() as stream:
        masses = {normalize(row[0]) for row in csv.reader(stream) if row}
    for folder in ('images', 'previews', 'input-png', 'source-proof'):
        (OUT / folder).mkdir(parents=True, exist_ok=True)
    fetch = helpers.Fetcher()
    rows = []
    for choice in choices:
        key = choice['key']
        source = sources[choice['plate']]
        if source['license'] != 'Public domain' or 'Pittsburgh' not in source['credit']:
            raise ValueError(f'Unapproved source license/origin: {key}')
        # Commons explicitly offers this standard thumbnail width. Use the
        # independent Pittsburgh scan; never substitute Audubon.org files.
        url = re.sub(r'/1920px-', '/3840px-', source['download_url']).split('?', 1)[0]
        data = fetch.get(url)
        image = Image.open(io.BytesIO(data)).convert('RGB')
        raw = OUT / 'input-png' / f'{key}.png'
        cut(image, choice).save(raw)
        prepared = prepare(raw)
        destination = OUT / 'images' / f'{key}.webp'
        write_plate(prepared, destination)
        preview(prepared, OUT / 'previews' / f'{key}.png')
        # Preserve an annotated whole-plate proof showing which specimen was cut.
        proof = image.copy()
        proof.thumbnail((900, 1100), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(proof)
        sx, sy = proof.width / choice['basis'][0], proof.height / choice['basis'][1]
        if 'box' in choice:
            x0, y0, x1, y1 = choice['box']
            draw.rectangle((x0 * sx, y0 * sy, x1 * sx, y1 * sy), outline='red', width=2)
        else:
            draw.line([(x * sx, y * sy) for x, y in choice['polygon']] +
                      [(choice['polygon'][0][0] * sx, choice['polygon'][0][1] * sy)], fill='red', width=2)
        proof.save(OUT / 'source-proof' / f'{key}.jpg', quality=90)
        row = {'key': key, 'common': inventory[key]['requested'], 'scientific': inventory[key]['scientific'],
               'plate': choice['plate'], 'asset': destination.name, 'method': choice['method'],
               'source_key': 'audubon-pittsburgh', 'source_page': source['source_page'],
               'source_download': url, 'source_sha256': hashlib.sha256(data).hexdigest(),
               'commons_original_sha1': source['original_sha1'], 'license': source['license'],
               'artist': source['artist'], 'credit': source['credit'],
               'identity_reference': inventory[key].get('source_page', ''),
               **validate(destination, key, known, masses)}
        rows.append(row)
        helpers.write_csv(OUT / 'review-ledger.csv', rows)
        print(f"Built {len(rows)}/{len(choices)}: {key}", flush=True)
    helpers.write_json(OUT / 'summary.json', {'built_candidates': len(rows), 'technical_passes': len(rows),
                       'new_shipping_assets': 0, 'final_visual_review': 'pending',
                       'generator': 'Non-generative selection/paper removal; original bird pixels retained.'})
    contact_sheets(rows)
    # Keep the review branch small; PNG intermediates need not be committed.
    print(json.dumps({'built_candidates': len(rows), 'new_shipping_assets': 0}))


if __name__ == '__main__':
    main()
