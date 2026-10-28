"""把扫描 PDF 页面渲染为适合视觉模型的图块。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterator

import fitz
from PIL import Image


def iter_page_images(path: Path, scale: float, quality: int) -> Iterator[tuple[int, str, bytes]]:
    """逐页产出 (页码, 图块名, JPEG)，横版页面沿装订线切分。"""
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, 1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            if image.width / image.height >= 1.25:
                midpoint = image.width // 2
                overlap = max(24, round(image.width * 0.02))
                crops = (
                    ("left", image.crop((0, 0, midpoint + overlap, image.height))),
                    ("right", image.crop((midpoint - overlap, 0, image.width, image.height))),
                )
            else:
                crops = (("full", image),)
            for name, crop in crops:
                buffer = BytesIO()
                crop.save(buffer, format="JPEG", quality=quality, optimize=True)
                yield page_number, name, buffer.getvalue()
