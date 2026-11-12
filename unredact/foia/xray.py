"""xray - find redactions that don't actually redact.

A PDF "redaction" is often just a black rectangle drawn on top of text that is
still in the file. Anyone can select, copy or extract it. This module flags:

  * unapplied Redact annotations (marked for redaction but never burned in)
  * dark filled rectangles / Square annotations that have extractable text under them

It only reports text the file already exposes. It does not reconstruct
properly redacted content. Requires PyMuPDF: pip install pymupdf
"""
from __future__ import annotations

import json
import sys

try:
    import pymupdf
except ImportError:  # pragma: no cover
    try:
        import fitz as pymupdf  # older PyMuPDF
    except ImportError:
        pymupdf = None

DARK = 0.25          # every RGB channel <= this counts as a "redaction-colored" fill
MIN_SIDE = 4.0       # points; ignore hairlines and bullets
COVER = 0.5          # fraction of a word's area that must sit under the box


def available() -> bool:
    return pymupdf is not None


def _need():
    if pymupdf is None:
        raise RuntimeError("x-ray needs PyMuPDF: pip install 'unredact[xray]'")


def _dark(color) -> bool:
    if not color:
        return False
    if len(color) == 1:          # gray
        return color[0] <= DARK
    if len(color) == 4:          # CMYK: dark if K high or all inks high
        c, m, y, k = color
        return k >= 1 - DARK or min(c, m, y) >= 1 - DARK
    return all(ch <= DARK for ch in color[:3])


def _light_int(rgb: int) -> bool:
    r, g, b = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    return min(r, g, b) >= 200


def _boxes(page):
    out = []
    for d in page.get_drawings():
        if d.get("fill") is None or not _dark(d.get("fill")):
            continue
        if d.get("fill_opacity", 1) is not None and d.get("fill_opacity", 1) < 0.9:
            continue
        r = d["rect"]
        if r.width >= MIN_SIDE and r.height >= MIN_SIDE:
            out.append(("drawn box", r))
    for a in page.annots() or []:
        kind = a.type[1]
        if kind == "Redact":
            out.append(("unapplied Redact annotation", a.rect))
        elif kind == "Square" and _dark((a.colors or {}).get("fill")):
            out.append(("box annotation", a.rect))
    return out


def _word_colors(page):
    """Map each span bbox -> text color so we can skip light text printed on a dark banner."""
    spans = []
    for b in page.get_text("dict").get("blocks", []):
        for line in b.get("lines", []):
            for s in line.get("spans", []):
                spans.append((pymupdf.Rect(s["bbox"]), s.get("color", 0)))
    return spans


def scan(path: str) -> dict:
    _need()
    doc = pymupdf.open(path)
    findings, image_only = [], []
    for pno, page in enumerate(doc, 1):
        words = page.get_text("words")
        boxes = _boxes(page)
        if not words and page.get_images():
            image_only.append(pno)
        if not boxes or not words:
            continue
        spans = _word_colors(page)
        for kind, box in boxes:
            hidden = []
            for w in words:
                wr = pymupdf.Rect(w[:4])
                if wr.is_empty or wr.get_area() == 0:
                    continue
                inter = wr & box
                if inter.is_empty or inter.get_area() / wr.get_area() < COVER:
                    continue
                center = pymupdf.Point((wr.x0 + wr.x1) / 2, (wr.y0 + wr.y1) / 2)
                color = next((c for r, c in spans if center in r), 0)
                if kind == "drawn box" and _light_int(color):
                    continue  # white text on a dark banner is visible, not hidden
                hidden.append((w[5], w[6], w[7], w[4]))  # block, line, word order
            if hidden:
                hidden.sort()
                findings.append({"page": pno, "kind": kind,
                                 "rect": [round(v, 1) for v in box],
                                 "text": " ".join(h[3] for h in hidden)})
    return {"file": path, "pages": len(doc), "findings": findings, "image_only_pages": image_only}


def report(res: dict, as_json=False) -> str:
    if as_json:
        return json.dumps(res, indent=2)
    lines = [f"{res['file']}: {res['pages']} page(s)"]
    if res["findings"]:
        lines.append(f"\033[31m{len(res['findings'])} leaky redaction(s)\033[0m - text is still in the file:")
        for f in res["findings"]:
            lines.append(f"  p{f['page']} [{f['kind']}] {f['rect']}\n    -> {f['text']}")
    else:
        lines.append("no leaky redactions found")
    if res["image_only_pages"]:
        n = len(res["image_only_pages"])
        lines.append(f"{n} image-only page(s) (scanned; redactions are burned into pixels, nothing to extract)")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    for p in sys.argv[1:]:
        print(report(scan(p)))
