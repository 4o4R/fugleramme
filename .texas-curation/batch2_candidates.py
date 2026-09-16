#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFilter, ImageFont

PAPER = (240, 236, 229)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    req = Request(url, headers={'User-Agent': 'Fugleramme-Texas-Curation/4.0 (https://github.com/4o4R/fugleramme)'})
    with urlopen(req, timeout=90) as response:
        return response.read()


def cut(source: Image.Image, mask: np.ndarray) -> Image.Image:
    alpha = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(.45))
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError('empty mask')
    rgba = source.crop(bbox).convert('RGBA')
    rgba.putalpha(alpha.crop(bbox))
    radius = max(5, round(max(rgba.size) * .014))
    pad = radius + 4
    padded = Image.new('RGBA', (rgba.width + pad * 2, rgba.height + pad * 2), (*PAPER, 0))
    padded.paste(rgba, (pad, pad))
    halo = Image.new('RGBA', padded.size, (*PAPER, 0))
    halo.putalpha(padded.getchannel('A').filter(ImageFilter.MaxFilter(radius * 2 + 1)))
    final = Image.alpha_composite(halo, padded)
    final.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    return final


def sheet(rows: list[dict], out: Path) -> None:
    font = ImageFont.truetype('DejaVuSans.ttf', 16)
    small = ImageFont.truetype('DejaVuSans.ttf', 12)
    cellw, cellh, cols = 400, 360, 4
    canvas = Image.new('RGB', (cellw * cols, cellh * math.ceil(len(rows) / cols)), PAPER)
    draw = ImageDraw.Draw(canvas)
    for i, row in enumerate(rows):
        image = Image.open(out / row['preview']).convert('RGBA')
        image.thumbnail((360, 285), Image.Resampling.LANCZOS)
        x, y = (i % cols) * cellw, (i // cols) * cellh
        canvas.paste(image, (x + (cellw - image.width) // 2, y + 5), image)
        draw.text((x + 8, y + 298), f"{i + 1}. {row['common']}", font=font, fill='black')
        draw.text((x + 8, y + 322), f"mask {row['mask_index']} score {row['score']:.3f}", font=small, fill='black')
    canvas.save(out / 'contact-sheet.jpg', quality=94)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--mask-tools', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'previews').mkdir(exist_ok=True)
    (args.output / 'source-proof').mkdir(exist_ok=True)
    sys.path.insert(0, str(args.mask_tools))
    from mobile_sam import SamPredictor, sam_model_registry
    predictor = SamPredictor(sam_model_registry['vit_t'](checkpoint=str(args.mask_tools / 'mobile_sam.pt')).eval())
    torch.set_num_threads(4)
    specs = json.loads(args.spec.read_text())
    choices = {r['requested']: r for r in json.loads((args.evidence / 'choices.json').read_text())}
    ledger = []
    for spec in specs:
        row = choices[spec['common']]
        source_meta = row['sources'][0]
        data = fetch(source_meta['download_url'])
        if digest(data) != source_meta['download_sha256']:
            raise RuntimeError('source changed: ' + spec['common'])
        source = Image.open(io.BytesIO(data)).convert('RGB')
        predictor.set_image(np.asarray(source))
        w, h = source.size
        f = spec['box_frac']
        box = np.array([f[0] * w, f[1] * h, f[2] * w, f[3] * h])
        with torch.inference_mode():
            masks, scores, _ = predictor.predict(box=box, multimask_output=True)
        proof = source.copy()
        proof.thumbnail((900, 1100), Image.Resampling.LANCZOS)
        sx, sy = proof.width / w, proof.height / h
        ImageDraw.Draw(proof).rectangle((box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy), outline='red', width=3)
        proof_name = row['key'] + '.jpg'
        proof.save(args.output / 'source-proof' / proof_name, quality=92)
        for mask_index in range(3):
            preview_name = f"{row['key']}-m{mask_index}.png"
            cut(source, masks[mask_index]).save(args.output / 'previews' / preview_name)
            ledger.append({
                'rank': row['rank'], 'common': row['requested'], 'key': row['key'], 'scientific': row['scientific'],
                'mask_index': mask_index, 'score': float(scores[mask_index]), 'box_frac': f,
                'preview': 'previews/' + preview_name, 'source_proof': 'source-proof/' + proof_name,
                'source_page': source_meta['source_page'], 'source_title': source_meta['file_title'],
                'source_download_sha256': source_meta['download_sha256'], 'license': source_meta['license'],
                'artist': source_meta['artist'], 'credit': source_meta['credit'], 'status': 'review candidate only'
            })
        print('built candidates:', spec['common'], flush=True)
    (args.output / 'ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
    # One contact sheet per mask variant makes side-by-side mask quality review straightforward.
    for idx in range(3):
        subset = [r for r in ledger if r['mask_index'] == idx]
        tmp = args.output / f'variant-{idx}'
        tmp.mkdir(exist_ok=True)
        for r in subset:
            target = tmp / r['preview']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((args.output / r['preview']).read_bytes())
            r['preview'] = r['preview']
        sheet(subset, tmp)
        (tmp / 'contact-sheet.jpg').replace(args.output / f'contact-mask-{idx}.jpg')
    (args.output / 'summary.json').write_text(json.dumps({'species': len(specs), 'candidate_masks': len(ledger), 'approved': 0}, indent=2) + '\n')


if __name__ == '__main__':
    main()
