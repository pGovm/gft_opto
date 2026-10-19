# Battery Sizing Calculator

## App Overview
This application was designed to optimize the battery size calculation for any given substation. It is compatible with all major OS' and is run locally on the computer and does not require an internet connection.
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

**Run Evaluation** uses the components on the current drawing. It sums all
entered trip-coil, close-coil, motor-inrush, and motor-running currents under
the assumption that they operate simultaneously. It does not use connectivity
or switch status to select loads, and it does not yet calculate battery capacity
in amp-hours. **Validate** separately checks drawing connectivity.

Changes to the layout invalidate previous results. Run Evaluation again before
using **Save Calculation Results** to export a PDF.

## Regression checks

With the project dependencies installed, run:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv/Scripts/python.exe -m unittest discover -s tests -p test_gui_workflows.py -v
```
