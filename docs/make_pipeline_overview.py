"""Build the one-page pipeline flowchart in standard (ISO 5807) flowchart symbols.

Writes two files from one layout:
    docs/pipeline_overview.pdf     printable, US Letter landscape
    docs/pipeline_overview.drawio  editable in draw.io / diagrams.net (free, no account)

Run from the project folder:
    .venv/bin/python docs/make_pipeline_overview.py

For test status and known issues, see docs/pipeline_flowchart.pdf.
"""
from html import escape
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pymupdf

HERE = Path(__file__).resolve().parent
PDF_OUT = HERE / "pipeline_overview.pdf"
DRAWIO_OUT = HERE / "pipeline_overview.drawio"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
SOURCE = "Based on branch codex/gui-layout-fixes at 3ba726b · 18 Nov 2026"
W, H = 792, 612                     # US Letter landscape, points (draw.io uses the same units)
MARGIN = 30
FONT_SIZE = 9.5

# ----------------------------------------------------------------------------- symbols
# kind: (fill, stroke, draw.io style, key label)
SYMBOLS = {
    "terminator": ("#d8eee2", "#2e7d32", "shape=mxgraph.flowchart.terminator;", "Start / end"),
    "process":    ("#e8f5ec", "#2e7d32", "rounded=0;", "Step"),
    "predefined": ("#e8f5ec", "#2e7d32", "shape=process;backgroundOutline=1;size=0.06;", "Sub-process"),
    "decision":   ("#fff7e0", "#9a6b00", "rhombus;", "Decision"),
    "data":       ("#eef2fb", "#3559a8", "shape=parallelogram;perimeter=parallelogramPerimeter;fixedSize=1;size=12;", "Input / output"),
    "manual":     ("#f3f4f6", "#374151", "shape=trapezoid;perimeter=trapezoidPerimeter;fixedSize=1;size=12;flipV=1;", "Done by a person"),
    "document":   ("#eef2fb", "#3559a8", "shape=document;boundedLbl=1;size=0.18;", "Document"),
    "display":    ("#eef2fb", "#3559a8", "shape=mxgraph.flowchart.display;", "On-screen display"),
    "stored":     ("#eef2fb", "#3559a8", "shape=dataStorage;fixedSize=1;size=10;", "Saved file"),
    "connector":  ("#ffffff", "#374151", "ellipse;aspect=fixed;", "Connector"),
}
SKEW, WAVE = 12, 6

# ----------------------------------------------------------------------------- layout
LANES = [("Set up and draw", 30, 232), ("Run Evaluation", 232, 424), ("Engineer review", 424, 594), ("Results", 594, 762)]
LANE_TOP, LANE_BOTTOM = 70, 518
C1, C2, C3, C4 = 140, 330, 510, 680
R1, R2, R3, R4, R5, R6 = 108, 166, 230, 300, 383, 460

# id: (kind, centre x, centre y, width, height, text with \n line breaks)
NODES = {
    "start":    ("terminator", C1, R1, 110, 32, "Start"),
    "install":  ("process",    C1, R2, 150, 40, "Install and launch\nthe app"),
    "inputs":   ("data",       C1, R3, 168, 46, "Open a layout or netlist\n(.json); import the\none-line PDF"),
    "build":    ("manual",     C1, R4, 172, 58, "Build the one-line drawing:\nplace and wire equipment,\nenter currents and\nbreaker settings"),
    "validate": ("decision",   C1, R5, 150, 76, "Validate\n(optional):\nwiring OK?"),
    "a_out":    ("connector",  C1, R6, 24, 24, "A"),
    "a_in":     ("connector",  C2, R1, 24, 24, "A"),
    "run":      ("process",    C2, R2, 150, 40, "Run Evaluation"),
    "zones":    ("predefined", C2, R3, 160, 52, "Find protection zones\n(bounded by CTs;\ndrafts proposed)"),
    "totals":   ("predefined", C2, R4, 160, 52, "Total each operating\nstage per DC supply"),
    "confirmed": ("decision",  C2, R5, 150, 76, "All zones\nconfirmed?"),
    "peaks":    ("process",    C2, R6, 160, 46, "Take each DC supply’s\nhighest stage\n(final supply peaks)"),
    "review":   ("manual",     C3, R5, 160, 66, "Configure Zones:\nassign loads and\nstages, confirm\neach zone"),
    "b_out":    ("connector",  C3, R6, 24, 24, "B"),
    "b_in":     ("connector",  C4, R1, 24, 24, "B"),
    "analysis": ("display",    C4, 180, 150, 44, "Analysis Output\nand zone outlines"),
    "calc":     ("document",   C4, 264, 150, 52, "Calculation PDF\n(Export Results)"),
    "save":     ("stored",     C4, 350, 150, 44, "Save the layout\n(.json)"),
    "end":      ("terminator", C4, 432, 110, 32, "End"),
}

# (from, side, to, side, waypoints, label, label position, label alignment)
EDGES = [
    ("start", "bottom", "install", "top", [], "", None, None),
    ("install", "bottom", "inputs", "top", [], "", None, None),
    ("inputs", "bottom", "build", "top", [], "", None, None),
    ("build", "bottom", "validate", "top", [], "", None, None),
    ("validate", "bottom", "a_out", "top", [], "Yes", (C1 + 6, R5 + 54), "left"),
    ("validate", "left", "build", "left", [(46, R5), (46, R4)], "No", (50, R5 - 5), "left"),
    ("a_in", "bottom", "run", "top", [], "", None, None),
    ("run", "bottom", "zones", "top", [], "", None, None),
    ("zones", "bottom", "totals", "top", [], "", None, None),
    ("totals", "bottom", "confirmed", "top", [], "", None, None),
    ("confirmed", "bottom", "peaks", "top", [], "Yes", (C2 + 6, R5 + 50), "left"),
    ("confirmed", "right", "review", "left", [], "No", (C2 + 80, R5 - 5), "left"),
    ("review", "top", "run", "right", [(C3, R2)], "re-evaluate", (C3 + 6, (R2 + R5) / 2), "left"),
    ("peaks", "right", "b_out", "left", [], "", None, None),
    ("b_in", "bottom", "analysis", "top", [], "", None, None),
    ("analysis", "bottom", "calc", "top", [], "", None, None),
    ("calc", "bottom", "save", "top", [], "", None, None),
    ("save", "bottom", "end", "top", [], "", None, None),
]

TITLE = "GFT_OPTO — the pipeline on one page"
SUBTITLE = ("Standard flowchart symbols (key below). Each column is one phase; "
            "circles A and B continue the flow into the next column.")
FOOTNOTE = ("Any change to the drawing marks results as outdated; run the evaluation again before exporting. "
            "Stages and zones are never added together.")


def rect_of(node):
    _, cx, cy, w, h, _ = NODES[node]
    return pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def anchor(node, side):
    """Point on the symbol's outline where an arrow starts or ends."""
    kind = NODES[node][0]
    r = rect_of(node)
    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
    inset = SKEW / 2 if kind in ("data", "manual") else 0
    if side == "top":
        return pymupdf.Point(cx, r.y0)
    if side == "bottom":
        return pymupdf.Point(cx, r.y1 - (WAVE / 2 if kind == "document" else 0))
    if side == "left":
        return pymupdf.Point(r.x0 + inset, cy)
    return pymupdf.Point(r.x1 - inset, cy)


def rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


# ----------------------------------------------------------------------------- PDF drawing
def draw_symbol(page, kind, r, width=1.3):
    fill, stroke = (rgb(c) for c in SYMBOLS[kind][:2])
    x0, y0, x1, y1 = r
    cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    s = page.new_shape()
    if kind == "terminator":
        s.draw_rect(r, radius=(h / 2 / w, 0.5))
    elif kind in ("process", "predefined"):
        s.draw_rect(r)
    elif kind == "decision":
        s.draw_polyline([(cx, y0), (x1, cy), (cx, y1), (x0, cy), (cx, y0)])
    elif kind == "data":
        k = min(SKEW, w * 0.25)
        s.draw_polyline([(x0 + k, y0), (x1, y0), (x1 - k, y1), (x0, y1), (x0 + k, y0)])
    elif kind == "manual":
        k = min(SKEW, w * 0.25)
        s.draw_polyline([(x0, y0), (x1, y0), (x1 - k, y1), (x0 + k, y1), (x0, y0)])
    elif kind == "document":
        a = min(WAVE, h * 0.18)
        s.draw_line((x0, y0), (x1, y0))
        s.draw_line((x1, y0), (x1, y1 - a))
        s.draw_bezier((x1, y1 - a), (x1 - w * 0.25, y1 + a), (x0 + w * 0.25, y1 - 3 * a), (x0, y1 - a))
        s.draw_line((x0, y1 - a), (x0, y0))
    elif kind == "display":
        k = min(14, w * 0.18)
        s.draw_line((x0, cy), (x0 + k, y0))
        s.draw_line((x0 + k, y0), (x1 - k, y0))
        s.draw_bezier((x1 - k, y0), (x1 + k * 0.3, y0), (x1 + k * 0.3, y1), (x1 - k, y1))
        s.draw_line((x1 - k, y1), (x0 + k, y1))
        s.draw_line((x0 + k, y1), (x0, cy))
    elif kind == "stored":
        k = min(10, w * 0.15)
        s.draw_line((x0 + k, y0), (x1, y0))
        s.draw_bezier((x1, y0), (x1 - k * 1.2, y0), (x1 - k * 1.2, y1), (x1, y1))
        s.draw_line((x1, y1), (x0 + k, y1))
        s.draw_bezier((x0 + k, y1), (x0 - k * 0.2, y1), (x0 - k * 0.2, y0), (x0 + k, y0))
    elif kind == "connector":
        s.draw_circle((cx, cy), min(w, h) / 2)
    s.finish(color=stroke, fill=fill, width=width, closePath=True)
    if kind == "predefined":
        bar = max(4, w * 0.06)
        s.draw_line((x0 + bar, y0), (x0 + bar, y1))
        s.draw_line((x1 - bar, y0), (x1 - bar, y1))
        s.finish(color=stroke, width=width)
    s.commit()


def draw_text(page, font, kind, r, text, size=FONT_SIZE, bold=False):
    lines = text.split("\n")
    height = len(lines) * size * 1.22
    cy = (r.y0 + r.y1) / 2 - (WAVE / 2 if kind == "document" else 0)
    room = r.width * (0.62 if kind == "decision" else 0.9)
    y = cy - height / 2 + size * 0.95
    for line in lines:
        width = font.text_length(line, fontsize=size)
        if width > room:
            raise SystemExit(f"Text too wide for its {kind} symbol: {line!r} ({width:.0f} > {room:.0f} pt)")
        page.insert_text(((r.x0 + r.x1) / 2 - width / 2, y), line, fontname="dvb" if bold else "dv",
                         fontsize=size, color=rgb("#1f2933"))
        y += size * 1.22


def draw_arrow(page, points, colour=rgb("#4b5563")):
    page.draw_polyline(points, color=colour, width=1.3)
    (ax, ay), (bx, by) = points[-2], points[-1]
    length = max(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5, 1e-6)
    ux, uy = (bx - ax) / length, (by - ay) / length
    size, half = 7, 3.5
    base = (bx - ux * size, by - uy * size)
    page.draw_polyline([(bx, by), (base[0] - uy * half, base[1] + ux * half),
                        (base[0] + uy * half, base[1] - ux * half), (bx, by)],
                       color=colour, fill=colour, width=0.8, closePath=True)


def build_pdf():
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_font(fontname="dv", fontfile=str(FONT_DIR / "DejaVuSans.ttf"))
    page.insert_font(fontname="dvb", fontfile=str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    font = pymupdf.Font(fontfile=str(FONT_DIR / "DejaVuSans.ttf"))
    bold = pymupdf.Font(fontfile=str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    green, ink, grey = rgb("#006a4e"), rgb("#1f2933"), rgb("#6b7280")
    # Header
    page.draw_rect(pymupdf.Rect(0, 0, W, 8), color=None, fill=green)
    page.insert_text((MARGIN, 34), TITLE, fontname="dvb", fontsize=17, color=green)
    page.insert_text((MARGIN, 53), SUBTITLE, fontname="dv", fontsize=9.5, color=rgb("#374151"))
    # Lanes (one per phase)
    for index, (title, x0, x1) in enumerate(LANES):
        page.draw_rect(pymupdf.Rect(x0, LANE_TOP, x1, LANE_BOTTOM), color=rgb("#d9e0e6"),
                       fill=rgb("#f7f9fa" if index % 2 == 0 else "#fbfcfd"), width=0.6)
        page.insert_text(((x0 + x1) / 2 - bold.text_length(title, fontsize=10.5) / 2, LANE_TOP + 16), title,
                         fontname="dvb", fontsize=10.5, color=green)
    # Arrows first, so symbols sit on top of their ends
    for src, src_side, dst, dst_side, via, label, at, align in EDGES:
        points = [anchor(src, src_side), *[pymupdf.Point(p) for p in via], anchor(dst, dst_side)]
        draw_arrow(page, points)
        if label:
            x = at[0] - (font.text_length(label, fontsize=9) if align == "right" else 0)
            page.insert_text((x, at[1]), label, fontname="dvb" if label in ("Yes", "No") else "dv", fontsize=9,
                             color=rgb("#374151"))
    for node, (kind, *_rest, text) in NODES.items():
        r = rect_of(node)
        draw_symbol(page, kind, r)
        draw_text(page, bold if kind == "connector" else font, kind, r, text,
                  size=10.5 if kind == "connector" else FONT_SIZE, bold=kind == "connector")
    page.insert_text((MARGIN, LANE_BOTTOM + 16), FOOTNOTE, fontname="dv", fontsize=9, color=ink)
    # Key: two rows of five symbols
    x_start, y = MARGIN + 34, LANE_BOTTOM + 30
    page.insert_text((MARGIN, y + 11), "Key:", fontname="dvb", fontsize=9, color=ink)
    kinds = list(SYMBOLS)
    for row, chunk in enumerate((kinds[:5], kinds[5:])):
        x = x_start
        for kind in chunk:
            icon = pymupdf.Rect(x, y + row * 20, x + (16 if kind == "connector" else 30), y + row * 20 + 15)
            if kind == "connector":
                icon = pymupdf.Rect(x + 7, icon.y0, x + 22, icon.y1)
            draw_symbol(page, kind, icon, width=0.9)
            text = SYMBOLS[kind][3]
            page.insert_text((x + 36, y + row * 20 + 11), text, fontname="dv", fontsize=8.5, color=ink)
            x += 36 + font.text_length(text, fontsize=8.5) + 26
    page.draw_line((MARGIN, H - 26), (W - MARGIN, H - 26), color=rgb("#c9d1da"), width=0.6)
    page.insert_text((MARGIN, H - 13), SOURCE, fontname="dv", fontsize=8, color=grey)
    right = "Editable copy: docs/pipeline_overview.drawio · Test status and issues: docs/pipeline_flowchart.pdf"
    page.insert_text((W - MARGIN - font.text_length(right, fontsize=8), H - 13), right, fontname="dv", fontsize=8, color=grey)
    doc.set_metadata({"title": "GFT_OPTO pipeline on one page", "author": "GFT_OPTO team"})
    doc.subset_fonts()
    doc.save(PDF_OUT, garbage=4, deflate=True)


# ----------------------------------------------------------------------------- draw.io file
SIDE = {"top": (0.5, 0), "bottom": (0.5, 1), "left": (0, 0.5), "right": (1, 0.5)}


def build_drawio():
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']

    def text_cell(ident, value, x, y, w, h, style):
        cells.append(f'<mxCell id="{ident}" value={quoteattr(value)} style={quoteattr(style)} vertex="1" parent="1">'
                     f'<mxGeometry x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" as="geometry"/></mxCell>')

    base = "fontFamily=Helvetica;fontColor=#1f2933;"
    text_cell("title", TITLE, MARGIN, 14, 600, 26,
              base + "text;html=1;align=left;verticalAlign=middle;fontSize=17;fontStyle=1;fontColor=#006a4e;")
    text_cell("subtitle", escape(SUBTITLE), MARGIN, 40, 732, 18, base + "text;html=1;align=left;verticalAlign=middle;fontSize=10;")
    for index, (title, x0, x1) in enumerate(LANES):
        fill = "#f7f9fa" if index % 2 == 0 else "#fbfcfd"
        text_cell(f"lane{index}", escape(title), x0, LANE_TOP, x1 - x0, LANE_BOTTOM - LANE_TOP,
                  base + f"rounded=0;whiteSpace=wrap;html=1;fillColor={fill};strokeColor=#d9e0e6;verticalAlign=top;"
                  "fontSize=11;fontStyle=1;fontColor=#006a4e;spacingTop=4;movable=0;")
    for node, (kind, cx, cy, w, h, text) in NODES.items():
        fill, stroke, shape, _ = SYMBOLS[kind]
        value = "<br>".join(escape(line) for line in text.split("\n"))
        style = (shape + f"whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};strokeWidth=1.3;"
                 + base + ("fontSize=11;fontStyle=1;" if kind == "connector" else "fontSize=10;"))
        text_cell(node, value, cx - w / 2, cy - h / 2, w, h, style)
    for number, (src, src_side, dst, dst_side, via, label, _, _) in enumerate(EDGES, start=1):
        (ex, ey), (nx, ny) = SIDE[src_side], SIDE[dst_side]
        style = (f"edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;strokeColor=#4b5563;"
                 f"strokeWidth=1.3;fontSize=10;exitX={ex};exitY={ey};exitDx=0;exitDy=0;entryX={nx};entryY={ny};"
                 f"entryDx=0;entryDy=0;" + ("fontStyle=1;" if label in ("Yes", "No") else ""))
        points = "".join(f'<mxPoint x="{x:.1f}" y="{y:.1f}"/>' for x, y in via)
        geometry = (f'<mxGeometry relative="1" as="geometry"><Array as="points">{points}</Array></mxGeometry>'
                    if via else '<mxGeometry relative="1" as="geometry"/>')
        cells.append(f'<mxCell id="e{number}" value={quoteattr(label)} style={quoteattr(style)} edge="1" parent="1" '
                     f'source="{src}" target="{dst}">{geometry}</mxCell>')
    text_cell("footnote", escape(FOOTNOTE), MARGIN, LANE_BOTTOM + 6, 732, 16,
              base + "text;html=1;align=left;verticalAlign=middle;fontSize=9;")
    text_cell("source", escape(SOURCE), MARGIN, H - 22, 400, 14,
              base + "text;html=1;align=left;verticalAlign=middle;fontSize=8;fontColor=#6b7280;")
    model = (f'<mxGraphModel dx="1000" dy="700" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
             f'fold="1" page="1" pageScale="1" pageWidth="{W}" pageHeight="{H}" math="0" shadow="0">'
             f'<root>{"".join(cells)}</root></mxGraphModel>')
    DRAWIO_OUT.write_text('<mxfile host="app.diagrams.net"><diagram id="pipeline" name="Pipeline">'
                          f"{model}</diagram></mxfile>\n", encoding="utf-8")


if __name__ == "__main__":
    build_pdf()
    build_drawio()
    print(f"wrote {PDF_OUT.name} and {DRAWIO_OUT.name}")
