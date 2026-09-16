# tests/test_pr92_artwork_fix.py
# Temporary PR #92 repair harness. This is intentionally diagnostic and will be
# removed after the corrected WebP is committed.

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image


def test_generate_pr92_repaired_webp() -> None:
    """Feather side-edge artwork cuts and emit the repaired WebP for retrieval."""
    path = Path("assets/artwork/classic/birds/dryobates-pubescens.webp")
    image = Image.open(path).convert("RGBA")
    width, height = image.size
    pixels = image.load()

    # The project's artwork guide explicitly recommends fading a branch/stem into
    # the paper when it exits a cutout. Restrict the edit to 6% of each side so
    # the bird and central branch retain their original pixels.
    feather = max(36, round(width * 0.06))

    # Smoothstep gives a soft, natural transition: fully transparent exactly at
    # the image boundary, then progressively restores the original alpha until
    # the end of the narrow feather band. RGB is untouched.
    for y in range(height):
        for x in range(feather):
            t = x / feather
            factor = t * t * (3.0 - 2.0 * t)
            r, g, b, a = pixels[x, y]
            if a:
                pixels[x, y] = (r, g, b, round(a * factor))

            xr = width - 1 - x
            r, g, b, a = pixels[xr, y]
            if a:
                pixels[xr, y] = (r, g, b, round(a * factor))

    output = io.BytesIO()
    image.save(output, format="WEBP", lossless=True, method=6)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")

    alpha = image.getchannel("A")
    print(f"PR92_REPAIR size={width}x{height} feather={feather} alpha_bbox={alpha.getbbox()}")
    print("PR92_WEBP_BASE64_BEGIN")
    print(encoded)
    print("PR92_WEBP_BASE64_END")

    # Intentionally fail so GitHub keeps the emitted payload plainly visible in
    # the job log. This temporary test is deleted in the final repair commit.
    raise AssertionError("temporary PR92 repair payload emitted above")
