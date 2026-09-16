#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFilter, ImageFont

PAPER = (240, 236, 229)


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cut(source: Image.Image, mask: np.ndarray) -> Image.Image:
    mask = cleanup(mask)
    alpha = Image.fromarray(mask.astype(np.uint8) * 255)
    alpha = alpha.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.45))
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError('empty mask')
    rgba = source.crop(bbox).convert('RGBA')
    rgba.putalpha(alpha.crop(bbox))
    radius = max(5, round(max(rgba.size) * 0.014))
    pad = radius + 4
    padded = Image.new('RGBA', (rgba.width + pad * 2, rgba.height + pad * 2), (*PAPER, 0))
    padded.paste(rgba, (pad, pad))
    halo = Image.new('RGBA', padded.size, (*PAPER, 0))
    halo.putalpha(padded.getchannel('A').filter(ImageFilter.MaxFilter(radius * 2 + 1)))
    final = Image.alpha_composite(halo, padded)
    final.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    return final


def cleanup(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if count <= 1:
        return mask.astype(bool)
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest_id = int(np.argmax(areas)) + 1
    largest = int(stats[largest_id, cv2.CC_STAT_AREA])
    keep = {largest_id}
    lx, ly, lw, lh = stats[largest_id, :4]
    margin = max(10, int(max(lw, lh) * 0.18))
    main_box = (lx - margin, ly - margin, lx + lw + margin, ly + lh + margin)
    for idx in range(1, count):
        if idx == largest_id:
            continue
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area < max(10, int(largest * 0.004)):
            continue
        x, y, w, h = stats[idx, :4]
        cx, cy = x + w / 2, y + h / 2
        if main_box[0] <= cx <= main_box[2] and main_box[1] <= cy <= main_box[3]:
            keep.add(idx)
    return np.isin(labels, list(keep))


def foreground(source: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lab = cv2.cvtColor(source, cv2.COLOR_RGB2LAB).astype(np.float32)
    h, w = source.shape[:2]
    band = max(3, round(min(h, w) * 0.025))
    border = np.concatenate([
        lab[:band].reshape(-1, 3), lab[-band:].reshape(-1, 3),
        lab[:, :band].reshape(-1, 3), lab[:, -band:].reshape(-1, 3),
    ])
    bg = np.median(border, axis=0)
    delta = np.linalg.norm(lab - bg, axis=2)
    gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    fg = ((delta > 18) | (gray < 185)).astype(np.uint8)
    # Remove page-edge grime and tiny printed noise before box proposal.
    kernel = np.ones((3, 3), np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    fg[:band] = fg[-band:] = 0
    fg[:, :band] = fg[:, -band:] = 0
    strong = ((delta > 30) | (gray < 145))
    return fg.astype(bool), strong


def boxes_from_foreground(fg: np.ndarray) -> list[tuple[int, int, int, int, str]]:
    h, w = fg.shape
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(fg.astype(np.uint8), 8)
    comps = []
    image_area = h * w
    for idx in range(1, count):
        x, y, bw, bh, area = map(int, stats[idx])
        frac = area / image_area
        if frac < 0.0007 or bw < 12 or bh < 12:
            continue
        cx, cy = centroids[idx]
        # Text/caption fragments tend to be tiny, horizontal and near page edges.
        edge_penalty = 0.4 if (cx < 0.06 * w or cx > 0.94 * w or cy < 0.06 * h or cy > 0.94 * h) else 0
        shape_penalty = 0.3 if max(bw / max(1, bh), bh / max(1, bw)) > 8 else 0
        score = math.log1p(area) - edge_penalty - shape_penalty
        comps.append((score, x, y, bw, bh, area))
    comps.sort(reverse=True)
    boxes: list[tuple[int, int, int, int, str]] = []
    for n, (_, x, y, bw, bh, area) in enumerate(comps[:8]):
        pad = int(max(bw, bh) * 0.16) + 6
        boxes.append((max(0, x - pad), max(0, y - pad), min(w - 1, x + bw + pad), min(h - 1, y + bh + pad), f'component-{n}'))
    # Add the bounding box of all substantial central ink as a fallback.
    ys, xs = np.nonzero(fg)
    if len(xs):
        lo_x, hi_x = np.quantile(xs, [0.08, 0.92])
        lo_y, hi_y = np.quantile(ys, [0.08, 0.92])
        pad = int(max(hi_x - lo_x, hi_y - lo_y) * 0.08) + 4
        boxes.append((max(0, int(lo_x) - pad), max(0, int(lo_y) - pad), min(w - 1, int(hi_x) + pad), min(h - 1, int(hi_y) + pad), 'central-ink'))
    # De-duplicate nearly identical boxes.
    unique = []
    for box in boxes:
        coords = box[:4]
        if not any(iou(coords, old[:4]) > 0.88 for old in unique):
            unique.append(box)
    return unique[:9]


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    aa = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    bb = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    return inter / max(1, aa + bb - inter)


def mask_score(mask: np.ndarray, pred_score: float, fg: np.ndarray, strong: np.ndarray) -> float:
    h, w = mask.shape
    area = int(mask.sum())
    frac = area / (h * w)
    if frac < 0.002 or frac > 0.50:
        return -999
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return -999
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    bbox_area = max(1, (x1 - x0 + 1) * (y1 - y0 + 1))
    fill = area / bbox_area
    fg_precision = float(fg[mask].mean()) if area else 0
    strong_precision = float(strong[mask].mean()) if area else 0
    # Prefer plausible isolated illustration subjects. The target area is broad
    # because birds range from tiny hummingbirds to full-page raptors.
    area_pref = max(0.0, 1.0 - abs(math.log(max(frac, 1e-5) / 0.055)) / 3.2)
    border = int(x0 <= 2 or y0 <= 2 or x1 >= w - 3 or y1 >= h - 3)
    return float(pred_score) * 2.2 + fg_precision * 1.25 + strong_precision * 0.8 + area_pref * 0.8 + min(fill, 0.7) * 0.35 - border * 0.8


def contact_sheets(rows: list[dict], output: Path) -> None:
    try:
        font = ImageFont.truetype('DejaVuSans.ttf', 15)
        small = ImageFont.truetype('DejaVuSans.ttf', 11)
    except OSError:
        font = ImageFont.load_default(); small = font
    cols, cellw, cellh = 5, 320, 330
    for offset in range(0, len(rows), 25):
        page = rows[offset:offset + 25]
        canvas = Image.new('RGB', (cols * cellw, math.ceil(len(page) / cols) * cellh), PAPER)
        draw = ImageDraw.Draw(canvas)
        for i, row in enumerate(page):
            image = Image.open(output / row['preview']).convert('RGBA')
            image.thumbnail((290, 250), Image.Resampling.LANCZOS)
            x, y = (i % cols) * cellw, (i // cols) * cellh
            canvas.paste(image, (x + (cellw - image.width)//2, y + 4), image)
            draw.text((x + 7, y + 258), f"{row['rank']}. {row['common']}"[:39], font=font, fill='black')
            draw.text((x + 7, y + 280), f"score {row['auto_score']:.2f} / {row['box_basis']}", font=small, fill='black')
            draw.text((x + 7, y + 298), row['source_title'][5:52], font=small, fill='black')
        canvas.save(output / f'contact-{offset // 25 + 1:02d}.jpg', quality=92)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--sources', type=Path, required=True)
    p.add_argument('--mask-tools', type=Path, required=True)
    p.add_argument('--covered', type=Path, required=True)
    p.add_argument('--exclusions', type=Path, required=True)
    p.add_argument('--part', type=int, choices=range(4), required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'previews').mkdir(exist_ok=True)
    (args.output / 'proof').mkdir(exist_ok=True)
    sys.path.insert(0, str(args.mask_tools))
    from mobile_sam import SamPredictor, sam_model_registry
    predictor = SamPredictor(sam_model_registry['vit_t'](checkpoint=str(args.mask_tools / 'mobile_sam.pt')).eval())
    torch.set_num_threads(4)
    cv2.setNumThreads(1)

    covered = {re.sub(r'-\\d+$', '', p.stem) for p in (args.covered / 'assets/artwork/classic/birds').glob('*.webp')}
    excluded = set(json.loads(args.exclusions.read_text())['species'])
    rows = json.loads((args.evidence / 'choices.json').read_text())
    ledger = []
    failures = []
    for row in rows:
        if not row.get('key') or row['key'] in covered or row['requested'] in excluded or not row.get('sources'):
            continue
        source_meta = row['sources'][0]
        pageid = int(source_meta['pageid'])
        if pageid % 4 != args.part:
            continue
        source_path = args.sources / f'{pageid}.png'
        try:
            if not source_path.exists():
                raise FileNotFoundError(source_path)
            if digest_bytes(source_path.read_bytes()) != source_meta['normalized_sha256']:
                raise RuntimeError('normalized source hash mismatch')
            source = Image.open(source_path).convert('RGB')
            arr = np.asarray(source)
            fg, strong = foreground(arr)
            boxes = boxes_from_foreground(fg)
            if not boxes:
                raise RuntimeError('no foreground boxes')
            predictor.set_image(arr)
            options = []
            for x0, y0, x1, y1, basis in boxes:
                box = np.array([x0, y0, x1, y1], dtype=float)
                with torch.inference_mode():
                    masks, scores, _ = predictor.predict(box=box, multimask_output=True)
                for idx, (mask, score) in enumerate(zip(masks, scores)):
                    value = mask_score(mask, float(score), fg, strong)
                    options.append((value, mask, idx, (x0, y0, x1, y1), basis, float(score)))
            options.sort(key=lambda item: item[0], reverse=True)
            best = options[0]
            if best[0] < 1.2:
                raise RuntimeError(f'no plausible mask; best={best[0]:.3f}')
            candidate = cut(source, best[1])
            preview = f"previews/{int(row['rank']):03d}-{row['key']}.png"
            candidate.save(args.output / preview)
            proof = source.copy()
            proof.thumbnail((900, 1100), Image.Resampling.LANCZOS)
            sx, sy = proof.width / source.width, proof.height / source.height
            x0, y0, x1, y1 = best[3]
            ImageDraw.Draw(proof).rectangle((x0*sx, y0*sy, x1*sx, y1*sy), outline='red', width=3)
            proof_path = f"proof/{int(row['rank']):03d}-{row['key']}.jpg"
            proof.save(args.output / proof_path, quality=90)
            ledger.append({
                'rank': int(row['rank']), 'common': row['requested'], 'key': row['key'], 'scientific': row['scientific'],
                'preview': preview, 'proof': proof_path, 'auto_score': best[0], 'sam_score': best[5],
                'mask_index': best[2], 'box': list(best[3]), 'box_basis': best[4],
                'source_pageid': pageid, 'source_title': source_meta['file_title'], 'source_page': source_meta['source_page'],
                'download_url': source_meta['download_url'], 'original_url': source_meta.get('original_url', ''),
                'license': source_meta['license'], 'license_url': source_meta.get('license_url', ''),
                'artist': source_meta.get('artist', ''), 'credit': source_meta.get('credit', ''),
                'source_normalized_sha256': source_meta['normalized_sha256'],
                'preview_sha256': digest_bytes((args.output / preview).read_bytes()),
                'status': 'AUTO REVIEW CANDIDATE ONLY - species identity/crop not yet approved',
            })
            print('candidate', row['rank'], row['requested'], f"{best[0]:.2f}", flush=True)
        except Exception as exc:
            failures.append({'rank': row.get('rank'), 'common': row.get('requested'), 'key': row.get('key'), 'error': repr(exc)})
            print('FAILED', row.get('rank'), row.get('requested'), repr(exc), flush=True)
        (args.output / 'ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
        (args.output / 'failures.json').write_text(json.dumps(failures, indent=2) + '\n')
    ledger.sort(key=lambda r: r['rank'])
    contact_sheets(ledger, args.output)
    summary = {'part': args.part, 'review_candidates': len(ledger), 'failures': len(failures), 'approved': 0,
               'warning': 'These are review candidates only. No candidate is approved without source/visual review.'}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2), flush=True)
    if not ledger:
        raise RuntimeError('No review candidates produced')


if __name__ == '__main__':
    main()
