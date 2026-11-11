"""Turn uploaded files into plain text plus page/photo images for the AI."""
import base64
import io
import os

from PIL import Image, ImageOps

try:  # iPhone photos (.heic) work if pillow-heif is installed
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass

import pymupdf

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".html", ".htm", ".srt", ".vtt"}
MAX_SIDE = 2000          # longest edge sent to the AI, in pixels
MAX_PDF_PAGES = 60
MIN_TEXT_PER_PAGE = 40   # below this, a PDF page is treated as scanned


def _encode_image(img: Image.Image) -> str:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.convert("RGBA").split()[-1])
        img = bg
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _thumb(b64: str) -> str:
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    img.thumbnail((360, 360))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


def extract_file(name: str, data: bytes, pdf_mode: str = "auto"):
    """Return (text_parts, images, warnings). images = [{name, b64}] (JPEG)."""
    ext = os.path.splitext(name)[1].lower()
    texts, images, warnings = [], [], []

    if ext == ".pdf":
        doc = pymupdf.open(stream=data, filetype="pdf")
        if doc.page_count > MAX_PDF_PAGES:
            warnings.append(f"{name}: only the first {MAX_PDF_PAGES} of {doc.page_count} pages were used.")
        for i, page in enumerate(doc):
            if i >= MAX_PDF_PAGES:
                break
            text = page.get_text().strip()
            use_image = pdf_mode == "images" or (pdf_mode == "auto" and len(text) < MIN_TEXT_PER_PAGE)
            if use_image:
                zoom = MAX_SIDE / max(page.rect.width, page.rect.height)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                images.append({"name": f"{name} - page {i + 1}", "b64": _encode_image(img)})
            elif text:
                texts.append(f"[{name} - page {i + 1}]\n{text}")
    elif ext in IMAGE_EXT:
        try:
            images.append({"name": name, "b64": _encode_image(Image.open(io.BytesIO(data)))})
        except Exception as e:
            warnings.append(f"{name}: couldn't open this image ({e}).")
    elif ext == ".docx":
        import docx

        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        texts.append(f"[{name}]\n" + "\n".join(parts))
    elif ext == ".pptx":
        from pptx import Presentation

        prs = Presentation(io.BytesIO(data))
        for i, slide in enumerate(prs.slides, 1):
            parts = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                parts.append("Notes: " + slide.notes_slide.notes_text_frame.text)
            if parts:
                texts.append(f"[{name} - slide {i}]\n" + "\n".join(parts))
    elif ext in TEXT_EXT or not ext:
        for enc in ("utf-8-sig", "utf-16", "cp1252", "shift_jis"):
            try:
                texts.append(f"[{name}]\n" + data.decode(enc))
                break
            except Exception:
                continue
        else:
            warnings.append(f"{name}: couldn't read the text encoding.")
    else:
        warnings.append(f"{name}: file type {ext} isn't supported (try PDF, image, Word, PowerPoint or text).")

    return texts, images, warnings


def make_thumb(b64: str) -> str:
    return _thumb(b64)
