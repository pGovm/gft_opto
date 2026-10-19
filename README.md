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

Use the header buttons **New Layout**, **Open Layout** (`Ctrl+O`), **Save Layout**
(`Ctrl+S`), or **Save Layout As** (`Ctrl+Shift+S`). The Project field names
the current layout. Layout JSON files preserve equipment definitions, component
properties, positions, sizes, rotations, bus taps, connections, and grid settings.
Unsaved changes are checked before closing or replacing a layout.

Imported PDFs are referenced by their file paths rather than embedded in the
layout. Keep the PDF with your project; when opening a layout whose PDF has
moved, you can locate it or open the editable drawing without its background.

Property edits commit when you press Enter or leave the field and support
Undo/Redo. Blank current fields commit as zero. Currents must be finite and
nonnegative; bus voltage must be positive or blank. Select one component at a
time to edit its properties.

## Evaluation

1. Import the one-line PDF and rebuild it with connected equipment. Enter
   the relevant coil and motor currents in the properties panel.
2. Click **Run Evaluation**. Bus and transformer zone proposals are traced
   through actual wires and junctions, stopping at candidate CT boundaries.
   Visually crossing wires without a junction are not connected. Line/manual
   zones can be added explicitly.
3. Click **Configure Protection Zones** above the analysis output. Review each
   zone's protected equipment, CT boundaries, and operating-load rows. Only CTs
   checked for that zone act as its boundaries; metering/other CTs can be left
   unchecked. Shared equipment and overlapping zones are allowed.
4. Assign each row's equipment, coil/motor, stage, DC supply, and units. A unit
   count of 1 means the entered current represents the whole operating load;
   use 3 only for a per-pole current when three poles operate. Trip Coil 2 is
   included only when explicitly assigned. The same physical coil must have
   a consistent DC supply across zones and stages.
5. Check the verification box for each zone after reviewing its assignments,
   then Save. Editing zone settings clears that zone's verification. Wiring,
   equipment, and switch-status changes require reconfirmation.

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
values are labeled and excluded from confirmed supply peaks. Protection is not
inferred from the PDF artwork or from equipment proximity. CT/relay assignments
and actual trip logic must be reviewed by the user.

Use the zone selector above the analysis output to view all zones or one zone.
Colored outlines mark the protected equipment and connections; dashed outlines
indicate zones needing review. **Validate** separately checks drawing connectivity.

Changes to the layout invalidate previous results. Run Evaluation again before
using **Save Calculation Results** to export a PDF. Draft exports are explicitly
marked incomplete. Zone configuration is saved in layout JSON; older layouts
without zone configuration still open normally.

## Regression checks

With the project dependencies installed, run:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -v
Remove-Item Env:QT_QPA_PLATFORM
```
