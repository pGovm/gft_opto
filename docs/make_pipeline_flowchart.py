"""Build docs/pipeline_flowchart.pdf, a printable multi-page flowchart of the app pipeline.

Run from the project folder (needs Graphviz's `neato` and the project's PyMuPDF):
    .venv/bin/python docs/make_pipeline_flowchart.py

Boxes are placed on a fixed grid (at most 3 across, 4 down) so text stays a
readable size on US Letter paper; Graphviz only draws the arrows. The status
tags are written by hand from test runs: update them, STATUS_DATE and
STATUS_SOURCE whenever the code or test results change.
"""
import subprocess
import tempfile
from html import escape
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "pipeline_flowchart.pdf"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
STATUS_DATE = "18 Nov 2026"
STATUS_SOURCE = "branch codex/gui-layout-fixes at 3ba726b"
FONT = "DejaVu Sans"
BOX_WIDTH = 255          # points; every box is this wide so columns line up
COLUMN, ROW = 300, 150   # grid spacing in points

# Status styles. Each can be told apart without colour: border style + tag text.
STYLES = {
    "ok":     {"fill": "#e8f5ec", "border": "#2e7d32", "style": "rounded,filled",        "pen": 1.6, "tag": "✓ TESTED"},
    "manual": {"fill": "#e9f0fb", "border": "#1d4ed8", "style": "rounded,filled,dashed", "pen": 1.6, "tag": "◐ WORKS – NO AUTOMATED TEST"},
    "issue":  {"fill": "#fff3dc", "border": "#b45309", "style": "rounded,filled,bold",   "pen": 2.6, "tag": "⚠ ISSUE"},
    "notrun": {"fill": "#f2f3f5", "border": "#6b7280", "style": "rounded,filled,dotted", "pen": 1.8, "tag": "– NOT RUN IN THIS REVIEW"},
    "plain":  {"fill": "#ffffff", "border": "#4b5563", "style": "rounded,filled",        "pen": 1.2, "tag": ""},
}
LOCAL = "● LOCAL CHANGE – NOT YET PUSHED"


def at(col, row):
    return f'{col * COLUMN:.0f},{-row * ROW:.0f}!'


def node(key, col, row, title, lines=(), status="plain", tag="", where="", local=False):
    s = STYLES[status]
    rows = [f'<TR><TD ALIGN="LEFT" WIDTH="{BOX_WIDTH}"><B><FONT POINT-SIZE="13">{escape(title)}</FONT></B></TD></TR>']
    rows += [f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="11" COLOR="#1f2933">{escape(l)}</FONT></TD></TR>' for l in lines]
    if where:
        rows.append(f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="9.5" COLOR="#6b7280">{escape(where)}</FONT></TD></TR>')
    text = s["tag"] + (f" · {tag}" if tag and s["tag"] else tag)
    if text:
        rows.append(f'<TR><TD ALIGN="LEFT"><B><FONT POINT-SIZE="10" COLOR="{s["border"]}">{escape(text)}</FONT></B></TD></TR>')
    if local:
        rows.append(f'<TR><TD ALIGN="LEFT"><B><FONT POINT-SIZE="9.5" COLOR="#6d28d9">{escape(LOCAL)}</FONT></B></TD></TR>')
    label = '<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="2">' + "".join(rows) + "</TABLE>>"
    return (f'{key} [pos="{at(col, row)}", label={label}, shape=box, style="{s["style"]}", '
            f'fillcolor="{s["fill"]}", color="{s["border"]}", penwidth={s["pen"]}];')


def data(key, col, row, title, lines=()):
    rows = [f'<TR><TD WIDTH="{BOX_WIDTH - 40}"><B><FONT POINT-SIZE="13">{escape(title)}</FONT></B></TD></TR>']
    rows += [f'<TR><TD><FONT POINT-SIZE="11" COLOR="#1f2933">{escape(l)}</FONT></TD></TR>' for l in lines]
    label = '<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="2">' + "".join(rows) + "</TABLE>>"
    return (f'{key} [pos="{at(col, row)}", label={label}, shape=cylinder, style=filled, '
            f'fillcolor="#eef2fb", color="#3559a8", penwidth=1.4];')


def decision(key, col, row, text):
    label = "<BR/>".join(escape(t) for t in text.split("\n"))
    return (f'{key} [pos="{at(col, row)}", label=<<B><FONT POINT-SIZE="12">{label}</FONT></B>>, shape=diamond, '
            f'style=filled, fillcolor="#ffffff", color="#4b5563", penwidth=1.4, margin="0.04,0.02"];')


def edge(a, b, text="", **attrs):
    parts = [f'label=<<FONT POINT-SIZE="11" COLOR="#374151"> {escape(text)} </FONT>>'] if text else []
    parts += [f'{k}="{v}"' for k, v in attrs.items()]
    return f"{a} -> {b} [{', '.join(parts)}];"


def graph(*body):
    return "\n".join([
        "digraph G {",
        f'graph [fontname="{FONT}", splines=true, overlap=true, esep="+10", pad=0.15, bgcolor="white", outputorder=edgesfirst];',
        f'node [fontname="{FONT}", margin="0.14,0.09"];',
        f'edge [fontname="{FONT}", color="#4b5563", arrowsize=0.9, penwidth=1.4];',
        *body, "}"])


# ----------------------------------------------------------------------------- pages
# Each page: (title, subtitle, graph). "{notes}" in a subtitle becomes the notes page number.
PAGES = [
    ("The pipeline at a glance",
     "From installing the app to the momentary DC load results. The next pages show each stage; issues are numbered and explained on page {notes}.",
     graph(
        node("install", 0, 0, "1  Install and build", ["Installers set up Python and the locked", "packages; PyInstaller builds an .exe"],
             "ok", "install.sh fixed 15 Nov"),
        node("launch", 1, 0, "2  Launch", ["gft-opto command, desktop shortcut", "or .exe opens the main window"], "ok", "starts and closes cleanly"),
        node("build", 2, 0, "3  Build the one-line drawing", ["Open a layout or netlist, import the PDF,", "place and wire equipment, enter currents"],
             "ok", "13 of 13 sample layouts"),
        node("ci", 0, 1, "Every push to GitHub", ["GitHub Actions runs all tests on", "Ubuntu and Windows"], "ok", "passing at 3ba726b"),
        node("validate", 1, 1, "4  Validate (optional)", ["Lists unconnected terminals and", "shorted devices"], "manual"),
        node("evaluate", 2, 1, "5  Run Evaluation", ["Finds protection zones, then totals", "each operating stage per DC supply"],
             "issue", "1: supply names are free text"),
        node("review", 1, 2, "6  Configure and confirm zones", ["The engineer assigns operating loads", "and confirms each zone"], "ok"),
        node("results", 2, 2, "7  Results and files", ["Analysis output, calculation PDF,", "netlist PDF, saved layout file"],
             "issue", "2: netlist PDF hidden"),
        edge("install", "launch"), edge("launch", "build"),
        edge("build", "validate", style="dashed"),
        edge("build", "evaluate", "the drawing"),
        edge("evaluate", "review", "needs review ⇄ re-evaluate", dir="both"),
        edge("evaluate", "results"),
    )),
    ("Stages 1–2: install, build and launch",
     "How the app gets onto a computer and starts. The Windows installer was not run in this review (Linux machine).",
     graph(
        node("ps1", 0, 0, "install.bat → install.ps1", ["Windows: installs uv if missing, runs the", "steps below, adds a desktop shortcut"],
             "notrun", "Windows only"),
        node("sh", 1, 0, "install.sh", ["Linux / macOS: installs uv if missing,", "then runs the steps below"], "ok", "completes"),
        node("ci", 2, 0, "GitHub Actions  (tests.yml)", ["Every push and pull request: Ubuntu", "and Windows, Python 3.12, all tests"],
             "ok", "253 tests passing at 3ba726b"),
        node("sync", 1, 1, "uv sync --locked", ["Creates .venv exactly from uv.lock:", "Python 3.12+, PySide6 6.11.2, PyMuPDF 1.28.2"], "ok"),
        node("check", 0, 2, "Import check", ["Confirms PySide6, PyMuPDF and", "the app all import"], "ok"),
        node("wheel", 1, 2, "uv build", ["Python wheel and source package,", "logo and icon included"], "ok", "installs and runs"),
        node("exe", 2, 2, "PyInstaller  gft-opto.spec", ["One-file desktop executable", "with the GFT icon and logo"], "ok", "builds in ~30 s; runs"),
        node("start", 1, 3, "Launch", ["gft-opto · python -m gft_opto.gui.substation_gui", "· desktop shortcut · executable"],
             "ok", "all start and close cleanly", where="main() in gui/substation_gui.py"),
        node("window", 2, 3, "Main window", ["Tabs: File · Edit · View · Analysis", "(toolbar scrolls in narrow windows)"],
             "ok", "header 119 px at any size"),
        edge("ps1", "sync"), edge("sh", "sync"), edge("ci", "sync", style="dashed"),
        edge("sync", "check"), edge("sync", "wheel"), edge("sync", "exe"),
        edge("check", "start", "shortcut"), edge("wheel", "start"), edge("exe", "start"),
        edge("start", "window"),
    )),
    ("Stages 3–4: build and check the one-line drawing",
     "Everything here edits one drawing. Every edit can be undone, and any edit marks earlier results as outdated.",
     graph(
        node("open", 0, 0, "Open layout (.json)", ["Checks the file, keeps saved positions,", "fills the Equipment Library"],
             "issue", "3: one library entry per part", where="gui/project_files.py, gui/layout_io.py"),
        node("netlist", 1, 0, "Import netlist (.json)", ["Replaces the drawing; zones are", "not carried over"], "ok", "bad files rejected safely"),
        node("pdfbg", 2, 0, "Import PDF background", ["Page 1 of the one-line PDF, drawn", "to scale behind the canvas"], "ok"),
        node("create", 0, 1, "Create Component", ["Symbol type, name and currents", "(fields depend on the type)"],
             "manual", "dialog itself is tested"),
        data("drawing", 1, 1, "The drawing", ["equipment with ports and", "properties, and the wires"]),
        node("validate", 2, 1, "Validate  (Analysis tab)", ["Unconnected terminals, devices shorted", "across themselves, bus tied to ground"],
             "manual", where="gui/circuit_check.py"),
        node("place", 0, 2, "Place and wire", ["Drag from library (snaps to grid); drag", "port to port; drop on a bus adds a tap,",
                                               "on a wire adds a junction"], "manual", where="gui/workspace.py"),
        node("undo", 1, 2, "Undo / Redo history", ["Every edit is one step applied to", "the drawing; title shows * until saved"], "ok"),
        node("align", 2, 2, "Align to Grid  (View tab)", ["Snaps connection points to the grid;", "one Undo restores them"], "ok"),
        node("edit", 1, 3, "Edit and properties", ["Move, rotate, resize, copy, delete; ID,", "status, currents, breaker poles,",
                                                   "mechanisms and DC supplies"], "ok", "smooth on 92-part layout"),
        edge("open", "drawing"), edge("netlist", "drawing"), edge("pdfbg", "drawing", "background"),
        edge("create", "place", "library"), edge("place", "undo"), edge("edit", "undo"), edge("align", "undo"),
        edge("undo", "drawing", "edits"),
        edge("drawing", "validate", style="dashed"),
    )),
    ("Stage 5: Run Evaluation",
     "The calculation. Nothing is final until every zone is confirmed; a final peak is a maximum across zones and stages, never a sum.",
     graph(
        data("drawing", 0, 0, "The drawing", ["equipment, wires, currents,", "breaker settings"]),
        node("pnet", 1, 0, "Protection netlist", ["Drawing → data the calculation", "reads (125 V DC)"], "ok", where="gui/evaluation_view.py"),
        decision("has", 2, 0, "Zones\nconfigured?"),
        node("topo", 0, 1, "Detected regions (display only)", ["CT regions for the zone selector;", "never added to any total"], "ok"),
        node("resolve", 1, 1, "a. Resolve each load row", ["Supply and multiplier come from the", "breaker: × poles only for independent,",
                                                            "per-mechanism breakers; motors × 1"], "ok", "matches hand calculation",
             where="logic/breaker_settings.py"),
        node("propose", 2, 1, "Propose draft zones", ["Cut wiring at CT terminals; open switches", "break it; reach across one closed breaker",
                                                       "→ Bus, Transformer, Line drafts (no loads)"], "ok",
             where="logic/protection_zones.py"),
        node("stage", 0, 2, "c. Total each operating stage", ["Same-stage rows operate together; each", "coil or motor once; Other loads added",
                                                               "to every stage on DC 1"], "issue", "1: “DC 1” ≠ “DC1”",
             where="logic/protection_study.py"),
        node("zone", 1, 2, "b. Check each zone", ["Region still matches its CTs? Wiring,", "switch or breaker setting changed",
                                                   "→ must be confirmed again"], "ok"),
        decision("done", 1, 3, "All zones confirmed,\nno warnings, every\nbus covered?"),
        node("peaks", 0, 3, "Final supply peaks", ["Highest stage current for each DC", "supply across all zones"], "ok"),
        node("prov", 2, 3, "Provisional result", ["Totals marked provisional; final", "peaks withheld → zone review (next page)"], "ok"),
        edge("drawing", "pnet"), edge("pnet", "has"),
        edge("has", "propose", "no"), edge("has", "resolve", "yes"), edge("propose", "resolve"),
        edge("pnet", "topo", style="dashed"),
        edge("resolve", "zone"), edge("zone", "stage"), edge("stage", "done"),
        edge("done", "peaks", "yes"), edge("done", "prov", "no"),
    )),
    ("Stage 6: configure and confirm zones",
     "The engineer reviews each zone. Saving re-runs the evaluation; later changes to the drawing can withdraw an approval.",
     graph(
        node("prov", 0, 0, "Provisional result", ["From Run Evaluation: zones need", "review or loads are missing"], "plain"),
        node("dialog", 1, 0, "Configure Zones window", ["Pick a detected region, or protected", "equipment and its boundary CTs"],
             "ok", where="gui/zone_dialog.py"),
        node("rows", 2, 0, "Operating load rows", ["Equipment, load, stage, operation;", "supply and multiplier come from the",
                                                   "breaker; Other = background load"], "ok"),
        node("delete", 2, 1, "Removing rows", ["− on a row number deletes that row;", "Delete key removes selected rows;",
                                               "Detect Missing Zones button removed"], "ok"),
        node("verify", 1, 1, "Verify", ["Tick “verified” per zone, or Verify all;", "optional Demonstration mode for",
                                         "sample layouts (PDF is labelled)"], "ok"),
        node("save", 0, 1, "Save zone settings", ["Stamps the approval; refuses a confirmed", "zone that still has warnings; undoable"], "ok"),
        node("again", 0, 2, "Run Evaluation again", ["Totals now final for confirmed zones"], "plain"),
        node("clears", 1, 2, "What withdraws an approval", ["Wiring, switch position or breaker", "setting changes; editing the zone’s",
                                                             "rows. Current values alone do not.",
                                                             "Checked every time it runs."], "ok"),
        edge("prov", "dialog"), edge("dialog", "rows"), edge("rows", "delete", style="dashed"),
        edge("rows", "verify"), edge("verify", "save"), edge("save", "again"),
        edge("clears", "again", style="dashed"),
    )),
    ("Stage 7: results and files",
     "What the engineer gets out. Every export below was produced for all 13 sample layouts in this review.",
     graph(
        data("result", 0, 0, "Evaluation result", ["zones, stage totals,", "supply peaks, warnings"]),
        data("drawing", 2, 0, "The drawing", ["equipment and wires"]),
        node("output", 0, 1, "Analysis Output and outlines", ["Text report and zone selector; outlines", "follow the wiring (dashed = needs",
                                                               "review); Expand Analysis"], "ok", where="gui/evaluation_view.py"),
        node("npdf", 1, 1, "Netlist PDF", ["Every component, its properties", "and every connection"], "issue",
             "2: only in disabled Export menu", where="gui/pdf_export.py"),
        node("save", 2, 1, "Save / Save As → .json", ["Checked before writing, then swapped in;", "zones and demonstration mode saved"],
             "ok", "reopens identical", where="gui/project_files.py"),
        node("calc", 0, 2, "Export Results → calculation PDF", ["Supply peaks, zone summary, and each", "zone’s stages and loads (amps × units,",
                                                                     "stage totals); DEMONSTRATION banner"], "ok", where="gui/pdf_export.py"),
        node("cli", 2, 2, "Old command-line calculator", ["python -m gft_opto.logic.momentary_calc2", "(not used by the app)"],
             "issue", "4: adds every load at once"),
        edge("result", "output"), edge("output", "calc"),
        edge("drawing", "npdf"), edge("drawing", "save"),
    )),
]

NOTES_HTML = """
<h2>Open issues</h2>
<table>
<tr><th>#</th><th>Where</th><th>Problem</th><th>Effect</th><th>Suggested fix</th></tr>
<tr><td>1</td><td>Run Evaluation</td><td>DC supply names are free text. “DC 1”, “dc 1” and “DC1” are treated as three different supplies.</td>
<td>Three 10 A trip coils on one battery show as 10 A on each of three supplies instead of 30 A on one, and the study still reports complete.</td>
<td>Normalise names (case, spaces) or pick supplies from a list; warn about near-duplicates.</td></tr>
<tr><td>2</td><td>Results and files</td><td>Netlist PDF is only in the Export Results menu, and that button stays disabled until an evaluation has run.</td>
<td>A netlist PDF of a drawing cannot be made before evaluating it.</td><td>Give Netlist PDF its own button, or keep the menu enabled.</td></tr>
<tr><td>3</td><td>Open layout</td><td>Opening a layout adds an Equipment Library entry for almost every drawn component.</td>
<td>The 92-component sample adds 92 library entries.</td><td>Group entries by symbol type and electrical properties, not by component name.</td></tr>
<tr><td>4</td><td>Old command-line calculator</td><td>momentary_calc2, run on its own, adds every current in a netlist together.</td>
<td>Gives a different, larger answer than the app. Not used by the app.</td><td>Remove it, or label it as a legacy tool.</td></tr>
</table>

<h2>Works, but no automated test</h2>
<p>These worked when checked by hand in this review; nothing in tests/ would catch them breaking.</p>
<ul>
<li>Mouse and keyboard editing on the canvas: dragging from the library, wiring, bus taps, junctions, Delete key, rotate, resize (gui/workspace.py, 28% covered).</li>
<li>Validate (gui/circuit_check.py, 8% covered).</li>
<li>Create Component adding its result to the Equipment Library.</li>
<li>The Expand Analysis window.</li>
</ul>

<h2>Fixed since the 12 Nov review</h2>
<ul>
<li>install.sh imported a misspelled module and always failed; it now completes.</li>
<li>Opening the 92-component layout: 4.9 s → 0.05 s. Dragging on it: about 8 → 34 frames per second.</li>
<li>The header used 301 px at 1080p; now 119 px at every size. The toolbar scrolls instead of overlapping in narrow windows.</li>
<li>Zones named “DEMO - …” no longer count as complete by themselves; Demonstration mode is an explicit, saved setting.</li>
<li>Other (background) loads are now added to every operating stage.</li>
<li>Opening a layout no longer moves components; Align to Grid does that on request.</li>
</ul>

<h2>Also changed on 18 Nov</h2>
<ul>
<li>Detect Missing Zones removed from the zone editor, with its code and tests.</li>
<li>Delete or Backspace in the load table removes the selected rows; text fields still edit normally.</li>
<li>Test clean-up: duplicate and obsolete tests removed, missing tests added (253 tests, all passing).</li>
</ul>
"""

NOTES_CSS = """
@font-face { font-family: dv; src: url(DejaVuSans.ttf); }
@font-face { font-family: dv; src: url(DejaVuSans-Bold.ttf); font-weight: bold; }
* { font-family: dv; }
body { font-size: 9.5pt; color: #1f2933; }
h2 { font-size: 12pt; color: #006a4e; margin: 7pt 0 3pt 0; }
p { margin: 0 0 3pt 0; }
table { border-collapse: collapse; width: 100%; }
th { background-color: #e8f1ed; text-align: left; font-weight: bold; border: 0.6pt solid #b9c4cf; padding: 3pt; }
td { border: 0.6pt solid #b9c4cf; padding: 3pt; vertical-align: top; }
ul { margin-top: 2pt; margin-bottom: 2pt; }
li { margin-bottom: 1.5pt; }
"""

# ----------------------------------------------------------------------------- page assembly
W, H = 792, 612                          # US Letter landscape, points
MARGIN = 30
CONTENT = pymupdf.Rect(MARGIN, 72, W - MARGIN, H - 62)
LEGEND = [("ok", "Tested"), ("manual", "Works, no automated test"), ("issue", "Issue (numbered, see notes)"),
          ("notrun", "Not run in this review")]


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def decorate(page, number, total, title, subtitle):
    page.insert_font(fontname="dv", fontfile=str(FONT_DIR / "DejaVuSans.ttf"))
    page.insert_font(fontname="dvb", fontfile=str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    dv = pymupdf.Font(fontfile=str(FONT_DIR / "DejaVuSans.ttf"))
    green, ink, grey = hex_rgb("#006a4e"), hex_rgb("#1f2933"), hex_rgb("#6b7280")
    page.draw_rect(pymupdf.Rect(0, 0, W, 8), color=None, fill=green)
    page.insert_text((MARGIN, 34), "GFT_OPTO pipeline — " + title, fontname="dvb", fontsize=16, color=green)
    page.insert_text((MARGIN, 52), subtitle, fontname="dv", fontsize=9.5, color=hex_rgb("#374151"))
    page.draw_line((MARGIN, 61), (W - MARGIN, 61), color=hex_rgb("#c9d1da"), width=0.6)
    # Key: border style + words, so it still reads when printed in black and white.
    x, y = MARGIN, H - 50
    page.insert_text((x, y + 9), "Key:", fontname="dvb", fontsize=8.5, color=ink)
    x += 30
    for status, text in LEGEND:
        s = STYLES[status]
        dashes = {"rounded,filled,dashed": "[3 2] 0", "rounded,filled,dotted": "[1 1.5] 0"}.get(s["style"])
        width = 2.2 if "bold" in s["style"] else 1.1
        page.draw_rect(pymupdf.Rect(x, y, x + 22, y + 12), color=hex_rgb(s["border"]), fill=hex_rgb(s["fill"]),
                       width=width, dashes=dashes, radius=0.25)
        page.insert_text((x + 28, y + 9), text, fontname="dv", fontsize=8.5, color=ink)
        x += 28 + dv.text_length(text, fontsize=8.5) + 16
    if any(escape(LOCAL) in source for _, _, source in PAGES):
        page.insert_text((x, y + 9), "● Local change, not yet pushed", fontname="dv", fontsize=8.5, color=hex_rgb("#6d28d9"))
    page.draw_line((MARGIN, H - 30), (W - MARGIN, H - 30), color=hex_rgb("#c9d1da"), width=0.6)
    page.insert_text((MARGIN, H - 17), f"Status checked {STATUS_DATE} · {STATUS_SOURCE}",
                     fontname="dv", fontsize=8, color=grey)
    footer = f"Page {number} of {total}"
    page.insert_text((W - MARGIN - dv.text_length(footer, fontsize=8), H - 17), footer, fontname="dv", fontsize=8, color=grey)


def render_notes(folder):
    """Lay out the notes as HTML pages with the same embedded font."""
    story = pymupdf.Story(html=NOTES_HTML, user_css=NOTES_CSS, archive=pymupdf.Archive(str(FONT_DIR)))
    notes_file = Path(folder) / "notes.pdf"
    writer = pymupdf.DocumentWriter(str(notes_file))
    more = True
    while more:
        device = writer.begin_page(pymupdf.Rect(0, 0, W, H))
        more, _ = story.place(pymupdf.Rect(MARGIN, 70, W - MARGIN, H - 62))
        story.draw(device)
        writer.end_page()
    writer.close()
    return pymupdf.open(notes_file)


def build():
    out = pymupdf.open()
    scales = []
    with tempfile.TemporaryDirectory() as folder:
        notes = render_notes(folder)
        total = len(PAGES) + notes.page_count
        for index, (title, subtitle, source) in enumerate(PAGES, start=1):
            dot_file = Path(folder) / f"page{index}.dot"
            pdf_file = Path(folder) / f"page{index}.pdf"
            dot_file.write_text(source, encoding="utf-8")
            subprocess.run(["neato", "-n2", "-Tpdf", str(dot_file), "-o", str(pdf_file)], check=True)
            with pymupdf.open(pdf_file) as drawn:
                src = drawn[0].rect
                scale = min(CONTENT.width / src.width, CONTENT.height / src.height, 1.0)
                scales.append(round(scale, 2))
                w, h = src.width * scale, src.height * scale
                target = pymupdf.Rect(CONTENT.x0 + (CONTENT.width - w) / 2, CONTENT.y0 + (CONTENT.height - h) / 2,
                                      CONTENT.x0 + (CONTENT.width + w) / 2, CONTENT.y0 + (CONTENT.height + h) / 2)
                page = out.new_page(width=W, height=H)
                page.show_pdf_page(target, drawn, 0)
            decorate(page, index, total, title, subtitle.format(notes=len(PAGES) + 1))
        for offset in range(notes.page_count):
            page = out.new_page(width=W, height=H)
            page.show_pdf_page(page.rect, notes, offset)
            decorate(page, len(PAGES) + 1 + offset, total, "notes" + (" (continued)" if offset else ""),
                     "Issues referenced by number on the diagrams, what has no automated test, and what changed.")
        notes.close()
    out.set_metadata({"title": "GFT_OPTO pipeline flowchart", "subject": f"Status checked {STATUS_DATE}",
                      "author": "GFT_OPTO team"})
    out.subset_fonts()  # embed only the characters used, not the whole font files
    out.save(OUTPUT, garbage=4, deflate=True)
    print(f"wrote {OUTPUT} ({total} pages); diagram scale per page: {scales}")


if __name__ == "__main__":
    build()
