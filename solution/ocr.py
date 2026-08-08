from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from solution.store import PAGE_SEP, Store

TEXT_FLOOR = 200
DPI = 300
LANGS = "rus+eng"


def available() -> bool:
    return shutil.which("tesseract") is not None


def blank_pages(path: str) -> list[int]:
    import fitz

    with fitz.open(path) as doc:
        return [
            n
            for n, page in enumerate(doc)
            if page.get_images() and len(page.get_text().strip()) < TEXT_FLOOR
        ]


def read_page(path: str, index: int) -> str:
    import fitz

    with fitz.open(path) as doc:
        pix = doc[index].get_pixmap(dpi=DPI)
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "page.png"
            pix.save(png)
            done = subprocess.run(
                ["tesseract", str(png), "stdout", "-l", LANGS, "--psm", "6"],
                capture_output=True,
                text=True,
                check=False,
            )
    return done.stdout if done.returncode == 0 else ""


def transcribe(store: Store) -> dict[str, int]:
    if not available():
        return {"documents": 0, "pages": 0, "skipped": 1}

    docs = pages = 0
    for doc in store.docs():
        blanks = blank_pages(doc.path)
        if not blanks:
            continue
        parts = doc.text.split(PAGE_SEP)
        touched = False
        for index in blanks:
            if index >= len(parts) or len(parts[index].strip()) >= TEXT_FLOOR:
                continue
            text = read_page(doc.path, index).strip()
            if not text:
                continue
            parts[index] = (parts[index].strip() + "\n" + text).strip()
            pages += 1
            touched = True
        if touched:
            store.replace_text(doc.doc_id, PAGE_SEP.join(parts))
            docs += 1
    store.commit()
    return {"documents": docs, "pages": pages, "skipped": 0}
