"""
Certificate PDF renderer.

ReportLab only — pure-Python wheels, so this installs on Windows without
GTK/Cairo the way WeasyPrint would need. The QR code is optional: if the
`qrcode` package is missing the certificate still renders, just without it.

    pip install reportlab qrcode pillow
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
UPLOAD_DIR = BASE_DIR / "uploads"
CERT_DIR = UPLOAD_DIR / "certificates"

PAGE_W, PAGE_H = landscape(A4)  # 842 x 595 pt

# Where we look for the institute logo, first hit wins. Upload one through
# POST /certificates/branding/logo to take the top slot.
LOGO_CANDIDATES = [
    UPLOAD_DIR / "branding" / "logo.png",
    UPLOAD_DIR / "branding" / "logo.jpg",
    BASE_DIR / "app" / "assets" / "logo.jpg",
    BASE_DIR.parent / "frontend" / "public" / "xloopdigital_logo.jpg",
]

# Palettes mirror the frontend design tokens in globals.css.
TEMPLATES = {
    "classic": {
        "frame": (0.788, 0.643, 0.161),      # gold
        "frameInner": (0.263, 0.220, 0.792),  # indigo
        "ink": (0.071, 0.078, 0.165),
        "soft": (0.353, 0.372, 0.502),
        "accent": (0.263, 0.220, 0.792),
        "wash": (0.925, 0.918, 0.992),
    },
    "modern": {
        "frame": (0.263, 0.220, 0.792),
        "frameInner": (0.545, 0.576, 0.973),
        "ink": (0.071, 0.078, 0.165),
        "soft": (0.353, 0.372, 0.502),
        "accent": (0.263, 0.220, 0.792),
        "wash": (0.933, 0.941, 0.996),
    },
    "elegant": {
        "frame": (0.059, 0.463, 0.431),       # teal
        "frameInner": (0.788, 0.643, 0.161),
        "ink": (0.043, 0.129, 0.122),
        "soft": (0.286, 0.404, 0.392),
        "accent": (0.059, 0.463, 0.431),
        "wash": (0.863, 0.961, 0.945),
    },
}

INSTITUTE_NAME = "SmartLMS"
INSTITUTE_TAGLINE = "Mari Energies Bootcamp · Xloop Digital"


# --------------------------------------------------------------------------
# small drawing helpers
# --------------------------------------------------------------------------

def _centre(c, text: str, y: float, font: str, size: float, colour) -> None:
    c.setFont(font, size)
    c.setFillColorRGB(*colour)
    c.drawString((PAGE_W - stringWidth(text, font, size)) / 2, y, text)


def _fit_centre(
    c, text: str, y: float, font: str, size: float, colour, max_width: float
) -> float:
    """Shrink the font until the line fits, then centre it. Returns the size used."""
    while size > 8 and stringWidth(text, font, size) > max_width:
        size -= 1
    _centre(c, text, y, font, size, colour)
    return size


def _find_logo() -> Optional[Path]:
    for path in LOGO_CANDIDATES:
        if path.exists():
            return path
    return None


def _qr_reader(url: str):
    try:
        import qrcode

        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return ImageReader(buf)
    except Exception as exc:  # missing qrcode/pillow shouldn't block issuance
        print(f"[certificates] QR skipped: {exc}")
        return None


def _fmt_date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %B %Y")
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "")).strftime("%d %B %Y")
        except ValueError:
            return value
    return datetime.utcnow().strftime("%d %B %Y")


# --------------------------------------------------------------------------
# page furniture
# --------------------------------------------------------------------------

def _draw_frame(c, p) -> None:
    c.setLineJoin(1)
    c.setStrokeColorRGB(*p["frame"])
    c.setLineWidth(6)
    c.rect(18, 18, PAGE_W - 36, PAGE_H - 36)

    c.setStrokeColorRGB(*p["frameInner"])
    c.setLineWidth(1.2)
    c.rect(28, 28, PAGE_W - 56, PAGE_H - 56)

    # corner ticks — quiet nod to a printed diploma, no clip-art flourishes
    c.setStrokeColorRGB(*p["frame"])
    c.setLineWidth(2.5)
    arm = 26
    for x, y, dx, dy in (
        (40, 40, 1, 1),
        (PAGE_W - 40, 40, -1, 1),
        (40, PAGE_H - 40, 1, -1),
        (PAGE_W - 40, PAGE_H - 40, -1, -1),
    ):
        c.line(x, y, x + arm * dx, y)
        c.line(x, y, x, y + arm * dy)


def _draw_watermark(c, p) -> None:
    c.saveState()
    c.setFillAlpha(0.05)
    c.setFillColorRGB(*p["accent"])
    c.translate(PAGE_W / 2, PAGE_H / 2 - 20)
    c.rotate(20)
    text = INSTITUTE_NAME.upper()
    c.setFont("Helvetica-Bold", 78)
    c.drawString(-stringWidth(text, "Helvetica-Bold", 78) / 2, -28, text)
    c.restoreState()


def _draw_seal(c, p, cx: float, cy: float, r: float = 34) -> None:
    c.saveState()
    c.setFillColorRGB(*p["wash"])
    c.setStrokeColorRGB(*p["frame"])
    c.setLineWidth(2)
    c.circle(cx, cy, r, stroke=1, fill=1)
    c.setLineWidth(0.8)
    c.circle(cx, cy, r - 6, stroke=1, fill=0)

    c.setFillColorRGB(*p["accent"])
    c.setFont("Helvetica-Bold", 12)
    c.drawString(cx - stringWidth("VERIFIED", "Helvetica-Bold", 12) / 2, cy + 2, "VERIFIED")
    c.setFont("Helvetica", 6.5)
    c.setFillColorRGB(*p["soft"])
    label = "OFFICIAL SEAL"
    c.drawString(cx - stringWidth(label, "Helvetica", 6.5) / 2, cy - 11, label)
    c.restoreState()


def _draw_signature(c, p, cx: float, y: float, name: str, role: str) -> None:
    c.setStrokeColorRGB(*p["soft"])
    c.setLineWidth(0.8)
    c.line(cx - 85, y, cx + 85, y)

    name = name or "—"
    c.setFont("Helvetica-Bold", 10)
    c.setFillColorRGB(*p["ink"])
    c.drawString(cx - stringWidth(name, "Helvetica-Bold", 10) / 2, y - 13, name)

    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(*p["soft"])
    c.drawString(cx - stringWidth(role, "Helvetica", 7.5) / 2, y - 24, role.upper())


def _draw_stat(c, p, x: float, y: float, label: str, value: str) -> None:
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(*p["soft"])
    c.drawString(x - stringWidth(label.upper(), "Helvetica", 7) / 2, y + 13, label.upper())
    c.setFont("Helvetica-Bold", 13)
    c.setFillColorRGB(*p["accent"])
    c.drawString(x - stringWidth(value, "Helvetica-Bold", 13) / 2, y - 2, value)


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------

def render_certificate(cert: dict, verify_url: str) -> Path:
    """
    Render `cert` (a certificate document) to uploads/certificates/<serial>.pdf
    and return the path.
    """
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    palette = TEMPLATES.get(cert.get("template", "classic"), TEMPLATES["classic"])
    out_path = CERT_DIR / f"{cert['serial']}.pdf"

    c = pdfcanvas.Canvas(str(out_path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Certificate {cert['serial']}")
    c.setAuthor(INSTITUTE_NAME)
    c.setSubject(cert.get("courseTitle", ""))

    _draw_frame(c, palette)
    _draw_watermark(c, palette)

    # ---- header: logo, institute, tagline -------------------------------
    logo = _find_logo()
    top = PAGE_H - 62
    if logo:
        try:
            reader = ImageReader(str(logo))
            iw, ih = reader.getSize()
            h = 40.0
            w = h * (iw / ih)
            c.drawImage(reader, (PAGE_W - w) / 2, top - 14, w, h, mask="auto")
            top -= 30
        except Exception as exc:
            print(f"[certificates] logo skipped: {exc}")

    _centre(c, INSTITUTE_NAME.upper(), top - 22, "Helvetica-Bold", 15, palette["ink"])
    _centre(c, INSTITUTE_TAGLINE, top - 35, "Helvetica", 8, palette["soft"])

    # ---- title -----------------------------------------------------------
    _centre(c, "CERTIFICATE OF COMPLETION", top - 74, "Helvetica-Bold", 27, palette["accent"])
    c.setStrokeColorRGB(*palette["frame"])
    c.setLineWidth(1.5)
    c.line(PAGE_W / 2 - 110, top - 86, PAGE_W / 2 + 110, top - 86)

    # ---- recipient -------------------------------------------------------
    _centre(c, "This is to certify that", top - 112, "Helvetica-Oblique", 11, palette["soft"])
    _fit_centre(
        c,
        cert.get("studentName", "Student"),
        top - 148,
        "Times-BoldItalic",
        34,
        palette["ink"],
        PAGE_W - 200,
    )
    c.setStrokeColorRGB(*palette["soft"])
    c.setLineWidth(0.6)
    c.line(PAGE_W / 2 - 190, top - 158, PAGE_W / 2 + 190, top - 158)

    _centre(
        c,
        "has successfully completed the course",
        top - 180,
        "Helvetica",
        10.5,
        palette["soft"],
    )
    _fit_centre(
        c,
        cert.get("courseTitle", "Course"),
        top - 204,
        "Helvetica-Bold",
        17,
        palette["accent"],
        PAGE_W - 200,
    )

    program = cert.get("programTitle") or ""
    if program:
        _fit_centre(
            c,
            f"under the {program} program",
            top - 220,
            "Helvetica-Oblique",
            9.5,
            palette["soft"],
            PAGE_W - 220,
        )

    # ---- result strip ----------------------------------------------------
    strip_y = 232
    c.setFillColorRGB(*palette["wash"])
    c.setStrokeColorRGB(*palette["frame"])
    c.setLineWidth(0.7)
    strip_w = 390
    c.roundRect(PAGE_W / 2 - strip_w / 2, strip_y - 18, strip_w, 46, 8, stroke=1, fill=1)

    stats = [
        ("Grade", cert.get("grade") or "—"),
        ("Score", f"{cert.get('percentage', 0):.1f}%"),
        ("Completed", _fmt_date(cert.get("completedAt"))),
    ]
    slot = strip_w / len(stats)
    for i, (label, value) in enumerate(stats):
        _draw_stat(
            c, palette, PAGE_W / 2 - strip_w / 2 + slot * (i + 0.5), strip_y, label, value
        )

    # ---- signatures + seal ----------------------------------------------
    sig_y = 142
    _draw_signature(c, palette, 200, sig_y, cert.get("instructorName", ""), "Course Instructor")
    _draw_seal(c, palette, PAGE_W / 2, sig_y + 4)
    _draw_signature(
        c, palette, PAGE_W - 200, sig_y, cert.get("issuedByName", ""), "Program Director"
    )

    # ---- footer: serial, QR, verify hint ---------------------------------
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColorRGB(*palette["ink"])
    c.drawString(56, 74, f"Certificate No. {cert.get('serial', '')}")
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(*palette["soft"])
    c.drawString(56, 62, f"Issued on {_fmt_date(cert.get('issuedAt'))}")
    c.drawString(56, 51, "Scan the code, or verify this certificate number at")
    c.setFont("Helvetica-Bold", 7)
    c.setFillColorRGB(*palette["accent"])
    c.drawString(56, 40, verify_url)

    qr = _qr_reader(verify_url)
    if qr:
        c.drawImage(qr, PAGE_W - 142, 44, 58, 58, mask="auto")

    c.showPage()
    c.save()
    return out_path