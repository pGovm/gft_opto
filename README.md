# Substation Momentary DC Load Calculator

## App Overview
This desktop application builds substation one-line diagrams and evaluates
momentary DC loads for configured protection-zone scenarios. It runs locally
and does not require an internet connection. Battery capacity sizing is outside
the project scope.
This main functionalities of this app are as follows:
- Customize and make your own electrical components.
- Import the desired One-line diagram as a PDF that will show up in the background.
- Draw and connect equipment, edit electrical properties, and undo or redo edits.
- Save editable layouts and export momentary current reports.

## Layouts and properties

The top command bar groups controls into **File**, **Edit**, **View**, and
**Analysis** tabs. File contains **New**, **Open** (`Ctrl+O`), **Save**
(`Ctrl+S`), **Save As** (`Ctrl+Shift+S`), **Import PDF**, and **Export Results**.
Edit contains undo/redo, copy/paste, delete, rotate, and component creation.
View contains the drawing-layer visibility controls, grid snapping, and opacity.
Analysis contains validation, zone configuration, evaluation, and the expanded
results view. The Project field names
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
All demonstration scenarios remain unconfirmed and their totals provisional;
they do not establish an approved worst-case DC supply load. No relay wiring or
actual trip sequence is inferred from the drawings.
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

Select components in the workspace and press **Ctrl+C**, then **Ctrl+V** to
duplicate them, or use **Copy** and **Paste** on the Edit tab. Ctrl-click
to select multiple components. Copies retain electrical properties, breaker
settings, bus taps, sizes, rotations, and connections between selected components.
Connections to unselected components are excluded. Each paste is offset and
receives new IDs; renamed components get numbered labels such as "CB-A (2)"
and "CB-A (3)". Paste supports
Undo/Redo as one edit. Protection-zone assignments are not duplicated and should
be reviewed after pasting. Copy/paste inside text fields still edits text normally.

## Evaluation

1. Import the one-line PDF and rebuild it with connected equipment. Enter
   the relevant coil and motor currents in the properties panel.
2. Click **Run Evaluation**. Bus, transformer, and eligible line/feeder proposals are traced
   through actual wires and junctions, stopping at candidate CT boundaries.
   An unbranched path through one closed breaker can extend a proposal to CTs
   on the opposite side, allowing neighboring schemes to overlap at the breaker.
   A line terminal must have a CT-bounded region containing a closed breaker
   after this expansion; metering taps reaching remote bays through branches
   do not create line events. Separate transformer proposals require separating
   CT topology, rather than a shared bus/transformer region.
   Visually crossing wires without a junction are not connected. Additional
   line/manual zones can be added explicitly. The selector also shows primary/composite
   topology regions, including CT-to-CT conductors. These are display candidates,
   not extra fault events or automatically assigned operating loads.
   Explicitly open breakers, disconnects, and motor-operated switches separate
   the regions at their terminals; normally open bypasses do not join bus sections.
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
6. Check the verification box for each zone after reviewing its assignments,
   then Save. Editing zone settings clears that zone's verification. Wiring,
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

The output lists zone membership, boundaries, per-stage load breakdowns, and
each DC supply's maximum current across complete zones. Each physical coil or
motor is counted once per stage. Separate DC supplies are not combined into a
single battery demand. Missing currents, unreachable CTs, unconfirmed settings,
and uncovered bus/transformer equipment make the study incomplete; provisional
values are labeled, and final supply peaks are withheld until the complete
study is verified. Protection is not
inferred from the PDF artwork or from equipment proximity. CT/relay assignments
and actual trip logic must be reviewed by the user.

Use the zone selector above the analysis output to view all zones or one zone.
Colored outlines mark the protected equipment and connections; dashed outlines
indicate zones needing review. **Validate** separately checks drawing connectivity.

Changes to the layout invalidate previous results. Run Evaluation again before
using **Export Results** on the File tab to export a PDF. Draft exports are explicitly
marked incomplete. Zone configuration is saved in layout JSON; older layouts
without zone configuration still open normally.

## Desktop executable

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

## Regression checks

With the project dependencies installed, run:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -v
Remove-Item Env:QT_QPA_PLATFORM
```
