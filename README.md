# Substation Momentary DC Load Calculator

A desktop tool for working out the momentary DC current a substation's station
battery must supply when protection operates. You rebuild the station's one-line
diagram over the original PDF, the tool finds the protection zones bounded by
CTs, and it totals the trip coil, close coil and motor currents for each zone,
operating stage and DC supply.

## What it does

- **Draw the one-line:** import the one-line PDF as a background, then place and
  connect breakers, switches, CTs, buses, transformers and other equipment.
- **Enter equipment data:** coil and motor currents, open/closed status, breaker
  pole arrangement, and which DC supply feeds each coil.
- **Find protection zones:** bus, transformer and line zones are proposed
  automatically from the CT boundaries and can be reviewed and edited.
- **Calculate loads:** for each zone and stage (primary trip, reclose, motor
  inrush, ...), the currents that operate together are summed, and each DC
  supply's peak current is reported.
- **Export results:** save the layout and export a PDF calculation report.

Results stay marked as provisional until every zone has been reviewed and
verified. The tool reports momentary current only; it does not size battery
capacity (amp-hours).

## Getting started

### Windows

1. Right-click the downloaded zip and choose **Extract All**. Extract it to the
   folder where you want to keep it: the shortcut points to this folder, so
   don't move it afterwards.
2. Open the extracted folder, then the `install` folder, and double-click
   **`install.bat`**.
3. If Windows shows "Windows protected your PC", click **More info**, then
   **Run anyway**.
4. Wait for "Setup Complete!". The first run needs an internet connection and
   can take a few minutes; Python is downloaded automatically if needed.
5. Start the app from the new **GFT_OPTO** shortcut on your desktop. After setup,
   the app works offline.

### Linux

Extract the zip, open a terminal in the extracted folder and run:

```bash
bash install/install.sh
```

Then start **GFT_OPTO** from the applications menu or desktop.

### Running from source

With [uv](https://docs.astral.sh/uv/) installed, from the project folder:

```bash
uv sync --locked
uv run gft-opto
```

## Try it

1. Click **Open** and choose `tests/layouts/two_zone_demo.json`.
2. On the **Analysis** tab, click **Run Evaluation**.
3. Click **Export Results** on the **File** tab to save the PDF report.

The other sample layouts in `tests/layouts` are rebuilt from real one-line
drawings and use randomised demonstration currents, not manufacturer data.

For your own station: **Import PDF**, draw and connect the equipment, enter the
currents in the properties panel, then use **Configure Zones** to review and
verify each zone before running the evaluation.

## User guide

Detailed reference for every feature, the evaluation rules, building the
Windows executable and running the tests.

### Layouts and properties

The top command bar groups controls into **File**, **Edit**, **View**, and
**Analysis** tabs. File contains **New**, **Open** (`Ctrl+O`), **Save**
(`Ctrl+S`), **Save As** (`Ctrl+Shift+S`), **Import PDF**, **Import Netlist**, and **Export Results**.
Edit contains undo/redo, copy/paste, delete, rotate, and component creation.
View contains the drawing-layer visibility controls, grid snapping, and opacity.
**Align to Grid** aligns the selected components, or the whole drawing when
nothing is selected. It supports Undo/Redo. Opening a saved layout preserves
its exact component positions.
Analysis contains validation, zone configuration, evaluation, and the expanded
results view. Ribbon pages scroll horizontally when the window is narrow.
The logo and program title appear on the left, with the Project field on the right.
The Project field names
the current layout. Layout JSON files preserve equipment definitions, component
properties, positions, sizes, rotations, bus taps, connections, and grid settings.
Unsaved changes are checked before closing or replacing a layout.
The 11 provided one-line JSON layouts contain randomized demonstration currents
for breakers and motor-operated switches. These are sample values, not manufacturer ratings.
For a demonstration, open one of these layouts, click **Run Evaluation**, then
**Export Results**. Saved `DEMO` scenarios include the random currents and
per-stage totals in the calculation PDF. Both trip coils operate together in
the demonstration's Primary trip stage on DC 1; closing, motor inrush, and
motor running are separate stages. Nearby breakers use automatically proposed
zone boundaries. Devices without a suggested zone action have separate Manual
independent-operation scenarios, which do not assert additional protection zones.
Demonstration scenarios initially open as unconfirmed drafts. In **Configure Zones**,
enable **Demonstration mode** and use **Verify all zones** to mark scenarios complete
for demonstration purposes even when boundary CTs or operating loads are missing.
The PDF labels these scenarios **COMPLETE (DEMO)** and retains a demonstration
notice. Empty scenarios contribute no calculated loads. Demo completion represents
assumed settings, rather than engineering approval of the operating sequence.
Demonstration mode is an explicit project setting saved in the layout and can
be changed in Configure Zones. Older demo layouts migrate their `DEMO - ...`
names to this setting on first opening. Subsequent renaming has no effect on mode.
Opening a layout also populates the Equipment Library from the components in
the drawing, preserving their saved electrical and breaker settings alongside
any existing templates. Identical definitions are reused; junction dots remain
wire-editing helpers rather than equipment-library entries.

Imported PDFs are referenced by their file paths rather than embedded in the
layout. Keep the PDF with your project; when opening a layout whose PDF has
moved, you can locate it or open the editable drawing without its background.

Property edits commit when you press Enter or leave the field and support
Undo/Redo. Blank current fields commit as zero. Currents must be finite and
nonnegative; bus voltage must be positive or blank. Select one component at a
time to edit its properties.

**Create Component** shows fields according to the selected symbol. Breakers
and custom components offer coil and motor currents. Motor-operated switches,
transformers, and potential transformers offer motor inrush and running
currents; transformer and PT currents are recorded but are not included in the
DC calculation. Buses offer a voltage rating. Ground and other passive symbols
have no operating-current fields.

Select components in the workspace and press **Ctrl+C**, then **Ctrl+V** to
duplicate them, or use **Copy** and **Paste** on the Edit tab. Ctrl-click
to select multiple components. Copies retain electrical properties, breaker
settings, bus taps, sizes, rotations, and connections between selected components.
Connections to unselected components are excluded. Each paste is offset and
receives new IDs; renamed components get numbered labels such as "CB-A (2)"
and "CB-A (3)". Paste supports
Undo/Redo as one edit. Protection-zone assignments are not duplicated and should
be reviewed after pasting. Copy/paste inside text fields still edits text normally.

### Evaluation

1. Import the one-line PDF and rebuild it with connected equipment. Enter
   the relevant coil and motor currents in the properties panel.
2. Click **Run Evaluation**. When the configured zone list is empty, bus,
   transformer, and eligible line/feeder proposals are traced
   through actual wires and junctions, stopping at candidate CT boundaries.
   An unbranched path through one closed breaker can extend a proposal to CTs
   on the opposite side, allowing neighboring schemes to overlap at the breaker.
   A line terminal must have a CT-bounded region containing a closed breaker
   after this expansion; metering taps reaching remote bays through branches
   do not create line events. Separate transformer proposals require separating
   CT topology, rather than a shared bus/transformer region.
   Visually crossing wires without a junction are not connected. Additional
   zones can be added explicitly with **Add Zone** in Configure Zones. Once zones
   exist, evaluation uses their saved assignments. The selector also shows primary/composite
   topology regions, including CT-to-CT conductors. These are display candidates,
   not extra fault events or automatically assigned operating loads.
   Explicitly open breakers, disconnects, and motor-operated switches separate
   the regions at their terminals; normally open bypasses do not join bus sections.
   **Normally Open** is available in the selected component's Status dropdown.
   Transfer buses remain on the drawing, but their standalone zones are excluded
   from proposals, configuration, evaluation, and topology overlays. Transfer buses
   are recognized by their name or a saved `bus_role` of `transfer`.
   Missing or unrecognized status values retain the connected behavior. Trip
   suggestions stop at open switches and omit open breakers. Existing operating
   assignments remain available for explicit review. Proposals of the same kind
   covering the same equipment and conductors are shown once.
3. Click **Configure Zones** on the Analysis tab. Review each
   zone's protected equipment, CT boundaries, and operating-load rows. Only CTs
   checked for that zone act as its boundaries; metering/other CTs can be left
   unchecked. Shared equipment and overlapping zones are allowed. Choose a
   detected region to retain its exact terminal and conductor membership, or
   select custom protected equipment and CTs.
4. Select each breaker in the workspace and set its coil-current basis (whole
   breaker or per mechanism), mechanism arrangement (shared or independent),
   pole count, and DC supplies for Trip Coil 1, Trip Coil 2, the close coil,
   and the motor. These equipment settings apply across all zones and support
   Undo/Redo. The properties panel scrolls to show the additional fields.
5. In each zone row, choose the equipment, coil/motor, stage, and all-pole or
   one-pole operation. Breaker supplies and multipliers are read-only here:
   three independent mechanisms at 10 A each produce 30 A for all-pole
   operation, while a shared mechanism or whole-breaker current uses a factor
   of 1. One-pole operation requires independent mechanisms and per-mechanism
   coil data. Motor current is always whole-device current, not multiplied by
   breaker poles. Non-breaker devices retain an editable supply in the zone row.
   Trip Coil 2 is included only when explicitly assigned.
   For miscellaneous current, choose **Other** in the Equipment dropdown and
   enter amps directly in the **Load** cell. The remaining operating fields are
   hidden for that row. These entries are saved as **Other load** on **DC 1**
   and represent standing background demand. Their current is added to every
   configured operating stage in that zone. A 10 A trip stage plus a 5 A Other
   load totals 15 A. A zone containing only Other loads has a **Background load**
   stage. Legacy miscellaneous entries use the same background behavior.
   Click row numbers to select loads. Shift-click selects a range and Ctrl-click
   adds or removes individual rows. **Delete Selected Loads** asks for confirmation
   before removing the selection. Press **Delete** or **Backspace** to
   remove every load row with a selected cell; inside a text field these keys
   edit the text as usual.
6. Check the verification box for each zone after reviewing its assignments,
   or use **Verify all zones** below it, then Save. Bulk verification confirms
   valid zones and leaves incomplete ordinary zones as drafts, with details in
   the verification summary's tooltip. The demo exception is described above.
   Editing zone settings clears that zone's verification. Wiring,
   equipment, and switch-status changes require reconfirmation. Older layouts
   approved before the switch-aware detector also require reconfirmation. They
   still open; compatible legacy supply/multiplier settings are carried into
   breaker settings when saving the zone editor. Unsupported legacy multipliers
   are flagged for review rather than silently changed.

Rows sharing a stage operate simultaneously. Different stages and faults in
different zones are evaluated separately. Configure primary trip, reclose,
motor inrush/run, and breaker-failure stages explicitly; there is no inferred
breaker-failure or reclose sequence. If primary and backup loads overlap, put
all overlapping loads in the same stage. A motor's inrush and running currents
cannot represent the same simultaneous operating state.

The GUI uses an assumed **125 V DC** control supply in its reports. The current
calculation sums the entered amps and derived quantities. AC bus voltage ratings
and the reported DC voltage do not scale those currents. Enter component currents
appropriate to the intended DC supply voltage.

The Calculation PDF summarizes each zone's per-stage totals, review issues,
assigned loads, and each DC supply's maximum current across complete zones. Its
Evaluation Output retains per-stage supply totals and load calculations without
the equipment-member lists or topology descriptions. CTs define zone boundaries;
they are not included as calculated loads. The Netlist PDF remains the separate
component and connectivity inventory. Each physical coil or motor is counted
once per stage. Separate DC supplies are not combined into a single battery
demand. Missing currents, unreachable CTs, unconfirmed settings, and uncovered
bus/transformer equipment make the study incomplete; provisional values are
labeled, and final supply peaks are withheld until the complete study is
verified. Protection is not inferred from the PDF artwork or from equipment
proximity. CT/relay assignments and actual trip logic must be reviewed by the
user.

Use the zone selector above the analysis output to view all zones or one zone.
Colored outlines mark the protected equipment and connections; dashed outlines
indicate zones needing review. **Validate** separately checks drawing connectivity.

Changes to the layout invalidate previous results. Run Evaluation again before
using **Export Results** on the File tab to export a PDF. Draft exports are explicitly
marked incomplete. Zone configuration is saved in layout JSON; older layouts
without zone configuration still open normally.

### Desktop executable

Close the application, then rebuild the Windows executable from the project
folder. The `package` group installs PyInstaller when needed:

```powershell
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
uv run --group package pyinstaller --noconfirm gft-opto.spec
```

The result is `dist/gft-opto.exe`. A desktop shortcut pointing to that file
opens the rebuilt version. `uv run gft-opto` runs the source directly and does
not rebuild the executable.

The executable and app window use the GFT logo from `src/gft_opto/assets/gft_icon.ico`.
For an existing desktop shortcut with a manually selected icon, open its
**Properties → Change Icon** and select that icon file or the rebuilt executable.

### Regression checks

With the project dependencies installed, run:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -v
Remove-Item Env:QT_QPA_PLATFORM
```

On Linux or macOS:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

GitHub Actions runs this suite on Windows with the native Qt platform and on
Linux with the offscreen Qt platform. Calculation, import/export, load editing,
demo-mode persistence, and grid-alignment regressions are covered.
