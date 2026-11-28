# Renders the first page of an imported PDF as the drawing background, with a
# size cap and messages a user can act on when a file cannot be used.

"""PDF background rendering shared by Import PDF and Open Layout."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PySide6.QtGui import QImage, QPixmap

from gft_opto.gui import messages
from gft_opto.gui.routing import SCENE_UNITS_PER_INCH

# Twice the PDF's 72 dpi keeps the background sharp up to about an ANSI D
# sheet. Larger sheets render at a lower resolution so the image stays near
# 16 million pixels (about 64 MB) instead of using 250 MB or failing.
MAX_BACKGROUND_PIXELS = 16_000_000
MAX_ZOOM = 2.0


class PdfBackgroundError(ValueError):
    """A PDF that cannot be used as a background. The text is for the user."""


@dataclass
class PdfBackground:
    pixmap: QPixmap
    page_count: int
    width_in: float
    height_in: float

    @property
    def scene_width(self) -> float:
        return self.width_in * SCENE_UNITS_PER_INCH

    @property
    def scene_height(self) -> float:
        return self.height_in * SCENE_UNITS_PER_INCH


def background_zoom(width_pt: float, height_pt: float) -> float:
    """Render scale for a page, capped so the image has a bounded pixel count."""
    return min(MAX_ZOOM, math.sqrt(MAX_BACKGROUND_PIXELS / (width_pt * height_pt)))


def render_first_page(path) -> PdfBackground:
    """Render page 1 of ``path``; raise PdfBackgroundError with a clear message."""
    name = Path(path).name
    try:
        document = pymupdf.open(path)
    except Exception:
        if not Path(path).is_file():
            raise PdfBackgroundError(messages.PDF_BACKGROUND_NOT_FOUND.format(name)) from None
        raise PdfBackgroundError(messages.PDF_BACKGROUND_UNREADABLE.format(name)) from None
    with document:
        if document.needs_pass:
            raise PdfBackgroundError(messages.PDF_BACKGROUND_PASSWORD.format(name))
        if document.page_count < 1:
            raise PdfBackgroundError(messages.PDF_BACKGROUND_NO_PAGES.format(name))
        try:
            page = document.load_page(0)
            rect = page.rect
            if not (rect.width > 0 and rect.height > 0):
                raise PdfBackgroundError(messages.PDF_BACKGROUND_NO_PAGES.format(name))
            zoom = background_zoom(rect.width, rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        except PdfBackgroundError:
            raise
        except Exception as error:
            raise PdfBackgroundError(messages.PDF_BACKGROUND_RENDER_FAILED.format(name, error)) from None
        # Keep the pixel buffer alive until Qt has copied it into the pixmap.
        samples = pix.samples
        image = QImage(samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image)
        del image, samples, pix
        if pixmap.isNull():
            raise PdfBackgroundError(messages.SUBSTATION_GUI4_COULD_NOT_DECODE_PDF_BACKGROUND)
        return PdfBackground(pixmap, document.page_count, rect.width / 72.0, rect.height / 72.0)
