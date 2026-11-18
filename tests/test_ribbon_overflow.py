"""Ribbon commands remain reachable at the application's minimum width."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from PySide6.QtWidgets import QApplication, QCheckBox, QToolButton
from gft_opto.gui.substation_gui import SubstationGuiMockup


class RibbonOverflowTests(unittest.TestCase):
    def test_commands_can_be_scrolled_into_view_at_supported_widths(self):
        app = QApplication.instance() or QApplication([])
        window = SubstationGuiMockup()
        try:
            self.assertEqual(window.minimumWidth(), 1000)
            window.show()
            for width in (1000, 1280, 1600):
                window.resize(width, 900)
                app.processEvents()
                # Native Windows runners may clamp a top-level window to the
                # desktop width. Check accessibility at the actual viewport.
                self.assertGreaterEqual(window.width(), window.minimumWidth())
                for tab in range(window.ribbon.count()):
                    window.ribbon.setCurrentIndex(tab)
                    app.processEvents()
                    scroll = window.ribbon.widget(tab)
                    controls = scroll.widget().findChildren(QToolButton)
                    controls += scroll.widget().findChildren(QCheckBox)
                    for control in controls:
                        with self.subTest(width=width, tab=tab, text=control.text()):
                            scroll.ensureWidgetVisible(control, 0, 0)
                            app.processEvents()
                            top_left = control.mapTo(scroll.viewport(), control.rect().topLeft())
                            bottom_right = control.mapTo(scroll.viewport(), control.rect().bottomRight())
                            self.assertTrue(scroll.viewport().rect().contains(top_left))
                            self.assertTrue(scroll.viewport().rect().contains(bottom_right))
        finally:
            window._saved_snapshot = window._layout_data()
            window.close()
            window.deleteLater()
            app.processEvents()
