"""Bounded PDF rasterisation for MCP image tools (jpeg/png, exact crop, retries by resizing)."""
from __future__ import annotations
import io
from dataclasses import dataclass
from pathlib import Path

import pymupdf

MAX_BYTES = 1_800_000
MAX_PIXELS = 3_800_000

@dataclass
class PageImage:
    data: bytes
    fmt: str
    width: int
    height: int
    quality: int
    scale: float


def render_page(path: Path, page: int, zoom: float=1.25, region: tuple[float,float,float,float] | None=None, mode: str='auto') -> PageImage:
    """Render one PDF page. Normalized crop is (x0,y0,x1,y1), all in [0,1]."""
    if mode not in ('auto','jpeg','png'):
        raise ValueError('Format must be auto/jpeg/png')
    zoom=max(.55,min(float(zoom),2.0))
    if region is not None:
        if (len(region)!=4 or any(not 0<=v<=1 for v in region)
           or region[2]<=region[0] or region[3]<=region[1]):
            raise ValueError('Region must be 0..1 with x0<x1 and y0<y1')
    with pymupdf.open(path) as doc:
        if doc.needs_pass: raise ValueError('Encrypted PDF')
        if not 1<=page<=len(doc): raise ValueError('Invalid PDF page')
        p=doc[page-1]
        r=p.rect
        clip=None
        if region is not None:
            x0,y0,x1,y1=region
            clip=pymupdf.Rect(r.x0+r.width*x0,r.y0+r.height*y0,r.x0+r.width*x1,r.y0+r.height*y1)
        bounds=clip or r
        zoom=min(zoom,(MAX_PIXELS/(bounds.width*bounds.height))**.5)
        for attempt in range(6):
            scale=zoom*(.75**attempt)
            pix=p.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=clip,
                             colorspace=pymupdf.csRGB,alpha=False)
            # JPEG is reliable for photo pages, mathematically precise page number remains in response.
            fmt='jpeg' if mode in ('auto','jpeg') else 'png'
            quality=max(45,82-attempt*7)
            data=pix.tobytes('jpg',jpg_quality=quality) if fmt=='jpeg' else pix.tobytes('png')
            if len(data)<=MAX_BYTES:
                return PageImage(data,fmt,pix.width,pix.height,quality,scale)
        raise ValueError('Page image too large even after reduction; use view_pdf_region')
