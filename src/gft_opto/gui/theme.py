# Provides consistent UI fonts, readable colors, and shared scrollbar styles.

"""Shared widget styles that should remain readable across system themes."""

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette


def ui_font(point_size=10, weight=QFont.Weight.Normal):
    """Use an installed system UI font, including for painted symbol labels."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    font.setItalic(False)
    return font


def readable_palette(base):
    """Keep white surfaces and their text consistent with a dark system theme."""
    palette = QPalette(base)
    for role, color in {
        QPalette.ColorRole.Window: "#f4f6f8",
        QPalette.ColorRole.WindowText: "#1f2933",
        QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#eef2f6",
        QPalette.ColorRole.Text: "#1f2933",
        QPalette.ColorRole.Button: "#ffffff",
        QPalette.ColorRole.ButtonText: "#1f2933",
        QPalette.ColorRole.Highlight: "#006a4e",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: "#64748b",
        QPalette.ColorRole.ToolTipBase: "#ffffff",
        QPalette.ColorRole.ToolTipText: "#1f2933",
    }.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#64748b"))
    return palette


ZONE_COLORS = {
    "Bus": "#0072b2",
    "Transformer": "#d55e00",
    "Line": "#009e73",
    "Manual": "#cc79a7",
}
TOPOLOGY_ZONE_COLOR = "#64748b"

SCROLLBAR_STYLE = """
    QScrollBar:vertical {
        background: #e4e9ef; width: 14px; margin: 0;
        border: none; border-radius: 7px;
    }
    QScrollBar:horizontal {
        background: #e4e9ef; height: 14px; margin: 0;
        border: none; border-radius: 7px;
    }
    QScrollBar::handle:vertical {
        background: #64748b; min-height: 28px;
        border: 2px solid #e4e9ef; border-radius: 6px;
    }
    QScrollBar::handle:horizontal {
        background: #64748b; min-width: 28px;
        border: 2px solid #e4e9ef; border-radius: 6px;
    }
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
        background: #475569;
    }
    QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {
        background: #006a4e;
    }
    QScrollBar::handle:vertical:disabled, QScrollBar::handle:horizontal:disabled {
        background: #b0bdca;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0; border: none; background: transparent;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0; border: none; background: transparent;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
        background: transparent;
    }
    QAbstractScrollArea::corner { background: #e4e9ef; border: none; }
"""
