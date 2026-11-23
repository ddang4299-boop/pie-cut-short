"""Build full TTF files of Be Vietnam Pro for the FFmpeg + libass render path.

@fontsource ships the font split into unicode-range subsets (latin, latin-ext, vietnamese) as
WOFF files. libass needs one face per weight that covers every Vietnamese glyph, so this merges
the subsets back into one TTF per weight under backend/assets/fonts/. Run once after
`npm install` in remotion/ (the output is committed, so normal users never need it).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fontTools.merge import Merger
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "remotion" / "node_modules" / "@fontsource" / "be-vietnam-pro" / "files"
OUT = ROOT / "backend" / "assets" / "fonts"
WEIGHTS = {600: "SemiBold", 800: "ExtraBold", 900: "Black"}  # the weights the composition uses
SUBSETS = ("latin", "latin-ext", "vietnamese")


def build(weight: int, style_name: str, tmp: Path) -> Path:
    parts = []
    for subset in SUBSETS:
        font = TTFont(SRC / f"be-vietnam-pro-{subset}-{weight}-normal.woff")
        font.flavor = None
        path = tmp / f"{subset}-{weight}.ttf"
        font.save(path)
        parts.append(str(path))
    merged = Merger().merge(parts)
    names = merged["name"]
    names.setName("Be Vietnam Pro", 1, 3, 1, 0x409)
    names.setName("Be Vietnam Pro", 16, 3, 1, 0x409)
    names.setName(style_name, 17, 3, 1, 0x409)
    names.setName(f"Be Vietnam Pro {style_name}", 4, 3, 1, 0x409)
    names.setName(f"BeVietnamPro-{style_name}", 6, 3, 1, 0x409)
    merged["OS/2"].usWeightClass = weight
    out = OUT / f"BeVietnamPro-{style_name}.ttf"
    merged.save(out)
    return out


def main() -> int:
    if not SRC.exists():
        print(f"missing {SRC}; run `npm install` in remotion/ first", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for weight, style_name in WEIGHTS.items():
            out = build(weight, style_name, Path(tmp))
            print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
