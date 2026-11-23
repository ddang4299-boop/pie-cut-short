"""Pack desktop/icon/piecut-<size>.png (from frontend/scripts/make_icon.mjs) into desktop/piecut.ico."""

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SIZES = [16, 24, 32, 48, 64, 128, 256]

images = [Image.open(HERE / "icon" / f"piecut-{s}.png").convert("RGBA") for s in SIZES]
images[-1].save(HERE / "piecut.ico", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
print(HERE / "piecut.ico")
