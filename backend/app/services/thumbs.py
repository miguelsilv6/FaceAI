"""Miniaturas JPEG (grelhas da interface), geradas uma vez e guardadas ao lado dos ficheiros da análise.

São só para visualização: os downloads, o relatório e o ZIP usam sempre os ficheiros completos.
"""

from __future__ import annotations

from pathlib import Path

import cv2

from .imageio import read_image, write_jpeg

WIDTHS = (160, 360)


def thumbnail(src: str | Path, cache_dir: Path, width: int) -> Path:
    src = Path(src)
    if width not in WIDTHS:
        raise ValueError(f"Largura de miniatura inválida: {width}")
    dest = cache_dir / f"{src.stem}_w{width}.jpg"
    if dest.exists():
        return dest
    img = read_image(src)
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    write_jpeg(tmp, img, quality=85)
    tmp.replace(dest)
    return dest
