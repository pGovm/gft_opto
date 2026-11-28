# Checks that PDF backgrounds render within a pixel budget, that unusable
# files produce a clear dialog, and that multi-page files say page 1 is used.

"""PDF background import: size cap, failure messages and multi-page notice."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtWidgets import QApplication, QMessageBox

from gft_opto.gui.pdf_background import (
    MAX_BACKGROUND_PIXELS, PdfBackgroundError, background_zoom, render_first_page,
)

APP = QApplication.instance() or QApplication([])


def make_pdf(path, *pages, password=None):
    with pymupdf.open() as document:
        for width_in, height_in in pages:
            page = document.new_page(width=width_in * 72, height=height_in * 72)
            page.insert_text((72, 72), "SHEET", fontsize=40)
        if password:
            document.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                          owner_pw=password, user_pw=password)
        else:
            document.save(path)
    return path


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_small_sheets_keep_the_full_resolution(self):
        # ANSI D (34 x 22 in) still renders at twice the PDF's 72 dpi.
        background = render_first_page(make_pdf(self.folder / "d.pdf", (34, 22)))
        self.assertEqual((background.pixmap.width(), background.pixmap.height()), (34 * 144, 22 * 144))
        self.assertEqual((background.width_in, background.height_in), (34, 22))
        self.assertEqual((background.scene_width, background.scene_height), (1700, 1100))

    def test_large_sheets_stay_within_the_pixel_budget(self):
        for size in ((48, 36), (200, 150)):  # the second used to fail outright
            background = render_first_page(make_pdf(self.folder / "big.pdf", size))
            pixels = background.pixmap.width() * background.pixmap.height()
            with self.subTest(size=size):
                self.assertLessEqual(pixels, MAX_BACKGROUND_PIXELS * 1.001)
                self.assertGreater(pixels, MAX_BACKGROUND_PIXELS * 0.99)
                # The aspect ratio and physical size are unchanged.
                self.assertAlmostEqual(background.pixmap.width() / background.pixmap.height(),
                                       size[0] / size[1], places=2)
                self.assertEqual((background.width_in, background.height_in), size)

    def test_zoom_never_exceeds_twice_72_dpi(self):
        self.assertEqual(background_zoom(72, 72), 2.0)
        self.assertLess(background_zoom(48 * 72, 36 * 72), 2.0)

    def test_unusable_files_have_clear_messages(self):
        empty = self.folder / "empty.pdf"
        empty.write_bytes(b"")
        junk = self.folder / "junk.pdf"
        junk.write_bytes(b"%PDF-1.7 this is not really a pdf" * 10)
        locked = make_pdf(self.folder / "locked.pdf", (11, 8.5), password="secret")
        cases = {
            self.folder / "missing.pdf": "was not found",
            empty: "could not be read as a PDF",
            junk: "could not be read as a PDF",
            locked: "password-protected",
        }
        for path, text in cases.items():
            with self.subTest(path=path.name), self.assertRaises(PdfBackgroundError) as caught:
                render_first_page(path)
            self.assertIn(text, str(caught.exception))
            self.assertIn(path.name, str(caught.exception))
            self.assertNotIn("code=", str(caught.exception))

    def test_colours_are_not_swapped(self):
        path = self.folder / "red.pdf"
        with pymupdf.open() as document:
            page = document.new_page(width=200, height=100)
            page.draw_rect(pymupdf.Rect(0, 0, 100, 100), color=(1, 0, 0), fill=(1, 0, 0))
            page.draw_rect(pymupdf.Rect(100, 0, 200, 100), color=(0, 0, 1), fill=(0, 0, 1))
            document.save(path)
        image = render_first_page(path).pixmap.toImage()
        left, right = image.pixelColor(50, 100), image.pixelColor(350, 100)
        self.assertEqual((left.red(), left.green(), left.blue()), (255, 0, 0))
        self.assertEqual((right.red(), right.green(), right.blue()), (0, 0, 255))

    def test_page_count_is_reported(self):
        background = render_first_page(make_pdf(self.folder / "three.pdf", (11, 8.5), (8.5, 11), (11, 17)))
        self.assertEqual(background.page_count, 3)
        self.assertEqual((background.width_in, background.height_in), (11, 8.5))


class ImportPdfDialogTests(unittest.TestCase):
    def setUp(self):
        from gft_opto.gui.substation_gui import SubstationGuiMockup

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.window = SubstationGuiMockup()

    def tearDown(self):
        self.window._saved_snapshot = self.window._layout_data()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def import_pdf(self, path):
        with patch("gft_opto.gui.substation_gui.QFileDialog.getOpenFileName",
                   return_value=(str(path), "")), \
                patch.object(QMessageBox, "warning") as warning, \
                patch.object(QMessageBox, "information") as information:
            self.window.on_import_pdf_clicked()
        return warning, information

    def test_failure_shows_a_dialog_and_keeps_the_drawing(self):
        bad = self.folder / "broken.pdf"
        bad.write_bytes(b"not a pdf")
        warning, information = self.import_pdf(bad)
        warning.assert_called_once()
        self.assertIn("could not be read as a PDF", warning.call_args.args[2])
        information.assert_not_called()
        self.assertFalse(self.window.workspace_view.has_pdf_background())
        self.assertIn("Import failed", self.window.footer_status_label.text())

    def test_password_protected_pdf_is_explained(self):
        locked = make_pdf(self.folder / "locked.pdf", (11, 8.5), password="secret")
        warning, _ = self.import_pdf(locked)
        self.assertIn("password-protected", warning.call_args.args[2])

    def test_multi_page_pdf_says_only_page_1_is_used(self):
        warning, information = self.import_pdf(make_pdf(self.folder / "set.pdf", (11, 8.5), (11, 8.5)))
        warning.assert_not_called()
        information.assert_called_once()
        self.assertIn("has 2 pages. Only page 1", information.call_args.args[2])
        self.assertTrue(self.window.workspace_view.has_pdf_background())
        self.assertIn("page 1 only", self.window.footer_status_label.text())

    def test_single_page_pdf_imports_quietly_at_its_physical_size(self):
        warning, information = self.import_pdf(make_pdf(self.folder / "one.pdf", (48, 36)))
        warning.assert_not_called()
        information.assert_not_called()
        rect = self.window.workspace_view._background_item.sceneBoundingRect()
        self.assertAlmostEqual(rect.width(), 2400, delta=1)
        self.assertAlmostEqual(rect.height(), 1800, delta=1)

    def test_huge_sheet_imports_instead_of_failing(self):
        warning, _ = self.import_pdf(make_pdf(self.folder / "huge.pdf", (200, 150)))
        warning.assert_not_called()
        rect = self.window.workspace_view._background_item.sceneBoundingRect()
        self.assertAlmostEqual(rect.width(), 200 * 50, delta=2)
        self.assertAlmostEqual(rect.height(), 150 * 50, delta=2)


if __name__ == "__main__":
    unittest.main()
