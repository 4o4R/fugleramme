#!/usr/bin/env python3
# .texas-curation/export_guided_masks.py
# Keep the exact pre-halo alpha channel so thin feet, bills and source perches
# can be reviewed and corrected without resynthesizing or repainting the bird.
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

root = Path(__file__).parent
spec = importlib.util.spec_from_file_location('guided', root / 'guided_specimens.py')
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
argv = sys.argv[1:]
spec_path = Path(argv[argv.index('--spec') + 1])
out = Path(argv[argv.index('--output') + 1])
rows = json.loads(spec_path.read_text())
original_halo = module.halo
counter = 0


def retain_alpha(rgba):
    global counter
    row = rows[counter // 3]
    variant = counter % 3
    # Coordinates here are exactly the region coordinates recorded in ledger.
    rgba.getchannel('A').save(out / f'{row["rank"]:03d}-v{variant}-alpha.png')
    counter += 1
    return original_halo(rgba)


module.halo = retain_alpha
module.main()
assert counter == len(rows) * 3
