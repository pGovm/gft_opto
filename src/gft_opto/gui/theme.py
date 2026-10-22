# Provides shared visual styles, currently the visible scrollbar styling used throughout the GUI.

"""Shared widget styles that should remain readable across system themes."""

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
