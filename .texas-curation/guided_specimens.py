#!/usr/bin/env python3
# .texas-curation/guided_specimens.py
# Select original scan pixels using explicit specimen prompts. No repainting.
# Review candidates are never automatically promoted to the application.
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_mask(source: Image.Image, spec: dict, predictor: object):
    w, h = source.size
    x0, y0, x1, y1 = [v / 100 * (w if i % 2 == 0 else h) for i, v in enumerate(spec['box'])]
    pad = max(15, round(max(x1 - x0, y1 - y0) * .25))
    region = (max(0, int(x0) - pad), max(0, int(y0) - pad), min(w, int(x1) + pad), min(h, int(y1) + pad))
    crop = source.crop(region)
    predictor.set_image(np.asarray(crop))
    points, labels = [], []
    for kind, label in [('positive', 1), ('negative', 0)]:
        for x, y in spec.get(kind, []):
            points.append([x * w / 100 - region[0], y * h / 100 - region[1]])
            labels.append(label)
    with torch.inference_mode():
        masks, scores, _ = predictor.predict(
            box=np.array([x0 - region[0], y0 - region[1], x1 - region[0], y1 - region[1]]),
            point_coords=np.asarray(points) if points else None,
            point_labels=np.asarray(labels) if labels else None,
            multimask_output=True,
        )
    result = []
    for m in masks:
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
        if n > 1:
            cutoff = max(2, float(stats[1:, cv2.CC_STAT_AREA].max()) * .0005)
            keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= cutoff]
            m = np.isin(lab, keep)
        alpha = Image.fromarray(m.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(.35))
        rgba = crop.convert('RGBA')
        rgba.putalpha(alpha)
        result.append((rgba, alpha))
    return region, result, scores


def halo(rgba: Image.Image) -> Image.Image:
    bbox = rgba.getchannel('A').getbbox()
    if not bbox:
        raise ValueError('empty mask')
    rgba = rgba.crop(bbox)
    radius = max(3, round(max(rgba.size) * .012))
    pad = radius + 3
    layer = Image.new('RGBA', (rgba.width + pad * 2, rgba.height + pad * 2), (240, 236, 229, 0))
    layer.paste(rgba, (pad, pad))
    paper = Image.new('RGBA', layer.size, (240, 236, 229, 0))
    paper.putalpha(layer.getchannel('A').filter(ImageFilter.MaxFilter(2 * radius + 1)))
    return Image.alpha_composite(paper, layer)


def main():
    parser = argparse.ArgumentParser()
    for name in ['spec', 'evidence', 'sources', 'mask-tools', 'output']:
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.mask_tools))
    from mobile_sam import SamPredictor, sam_model_registry
    torch.set_num_threads(4)
    cv2.setNumThreads(1)
    predictor = SamPredictor(sam_model_registry['vit_t'](checkpoint=str(args.mask_tools / 'mobile_sam.pt')).eval())
    choices = {int(r['rank']): r for r in json.loads((args.evidence / 'choices.json').read_text())}
    ledger = []
    for spec in json.loads(args.spec.read_text()):
        row = choices[int(spec['rank'])]
        meta = row['sources'][spec.get('source_index', 0)]
        pid = meta['pageid']
        paths = list(args.sources.rglob(f'{pid}.png'))
        if len(paths) != 1:
            raise RuntimeError(f'Expected exactly one source scan for {pid}, found {len(paths)}')
        data = paths[0].read_bytes()
        assert sha(data) == meta['normalized_sha256'], 'Source hash mismatch'
        source = Image.open(paths[0]).convert('RGB')
        region, masks, scores = build_mask(source, spec, predictor)
        source.crop(region).save(args.output / f'{spec["rank"]:03d}-region.jpg', quality=95)
        for variant, ((rgba, alpha), score) in enumerate(zip(masks, scores)):
            image = halo(rgba)
            preview = f'{spec["rank"]:03d}-v{variant}.png'
            image.save(args.output / preview)
            ledger.append({'rank': spec['rank'], 'key': row['key'], 'common': row['requested'],
                'scientific': row['scientific'], 'variant': variant, 'preview': preview,
                'alpha_sha256': sha(alpha.tobytes()), 'preview_sha256': sha((args.output / preview).read_bytes()),
                'source_sha256': sha(data), 'region': region, 'score': float(score),
                'source': meta, 'spec': spec, 'status': 'candidate - not approved'})
        print(spec['rank'], row['requested'], 'done', flush=True)
        (args.output / 'ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 16)
    ids = list(dict.fromkeys(r['rank'] for r in ledger))
    for offset in range(0, len(ids), 6):
        page = ids[offset:offset + 6]
        canvas = Image.new('RGB', (1600, 280 * len(page)), (240, 236, 229))
        draw = ImageDraw.Draw(canvas)
        for i, rank in enumerate(page):
            rows = [r for r in ledger if r['rank'] == rank]
            y = i * 280
            draw.text((8, y + 3), f'{rank}. {rows[0]["common"]}', font=font, fill='black')
            for col in range(4):
                path = args.output / (f'{rank:03d}-region.jpg' if col == 0 else rows[col - 1]['preview'])
                image = Image.open(path).convert('RGBA')
                image.thumbnail((385, 238), Image.Resampling.LANCZOS)
                x = col * 400
                canvas.paste(image, (x + (400 - image.width) // 2, y + 25 + (240 - image.height) // 2), image)
                draw.text((x + 6, y + 262), 'SCAN CROP' if col == 0 else f'v{col - 1}', font=font, fill='black')
            draw.line((0, y + 279, 1600, y + 279), fill=(160, 140, 120))
        canvas.save(args.output / f'review-{offset // 6 + 1:02d}.jpg', quality=94)


if __name__ == '__main__':
    main()
