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

### Portable Windows package

For a portable Windows x64 ZIP containing Python, dependencies, and the provided
one-line layouts, build on Windows:

```powershell
uv sync --locked --group package
.venv/Scripts/python.exe install/build_portable.py
```

Extract `dist/GFT-OPTO-Windows-Portable.zip` and launch **GFT OPTO.exe** inside
the extracted folder. Keep `_internal` beside the executable. Runtime use needs
no Python installation, PowerShell setup, or package downloads. Save work to a
writable folder. The package is unsigned; organizational execution policies may
require IT approval. The ZIP includes launch instructions, build information,
application source, and the installed libraries' supplied notices.

### Windows source installer

1. Right-click the downloaded zip and choose **Extract All**. Extract it to the
   folder where you want to keep it: the shortcut points to this folder, so
   don't move it afterwards.
2. Open the extracted folder, then the `install` folder, and double-click
   **`install.bat`**.
3. If organizational security blocks the installer, ask IT to approve it or
   use an approved portable package.
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
drawings and use assigned sample currents, not manufacturer data.

For your own station: **Import PDF**, draw and connect the equipment, enter the
currents in the properties panel, then use **Configure Zones** to review and
verify each zone before running the evaluation.

## User guide

### The ribbon

| Tab | Commands |
|---|---|
| **File** | New (`Ctrl+N`), Open (`Ctrl+O`), Save (`Ctrl+S`), Save As (`Ctrl+Shift+S`), Import PDF, Import Netlist, Export Results (calculation PDF or netlist PDF) |
| **Edit** | Undo, Redo, Copy, Paste, Delete, Rotate 90°, Create Component |
| **View** | Show Grid, Show PDF, Show Circuit, Show Zones, Snap to Grid, grid opacity, Align to Grid |
| **Analysis** | Validate, Configure Zones, Run Evaluation, Expand Analysis |

- Ribbon pages scroll sideways when the window is narrow.
- The **Project** field, top right, names the current layout.
- **Align to Grid** aligns the selected components, or the whole drawing if
  nothing is selected.

### Layouts

- A layout is saved as a JSON file containing the equipment, its properties,
  positions, sizes, rotations, bus taps, connections and grid settings.
  Opening it restores everything exactly as saved.
- You're asked to save unsaved changes before closing or replacing a layout.
- Opening a layout adds its components to the **Equipment Library**, with their
  electrical and breaker settings. Identical components are listed once.
  Junction dots are wire-editing helpers and are not added.
- Imported PDFs are linked by file path, not stored in the layout. Keep the PDF
  with your project. If it has moved, you can locate it or open the drawing
  without its background.

### Editing components

**Properties**

- Edits are applied when you press Enter or leave the field, and can be undone.
- Select one component at a time to edit it.
- A blank current counts as zero. Currents must be zero or positive; bus voltage
  must be positive or blank.

**Create Component fields**

| Component | Fields |
|---|---|
| Circuit Breaker, Custom | Trip Coil 1, Trip Coil 2, Close Coil, Motor Inrush, Motor Run |
| Motor Operated Switch | Motor Inrush, Motor Run |
| Transformer (2- and 3-winding), Potential Transformer | Motor Inrush, Motor Run (recorded but not included in the DC calculation) |
| Bus | Rating (kV) |
| Surge Arrester | MCOV (kV) |
| Disconnect Switch, CT, Line Trap, Incoming AC Line, Ground | None |

**Copy and paste**

- Ctrl-click to select several components, then press `Ctrl+C` and `Ctrl+V`,
  or use **Copy** and **Paste** on the Edit tab.
- Copies keep their properties, breaker settings, bus taps, sizes, rotations and
  the connections between the copied components. Connections to components
  you didn't copy are dropped.
- Each paste is offset, gets new IDs and numbered names such as "CB-A (2)", and
  undoes as one step.
- Protection-zone assignments are not copied; review them after pasting.

### Running a study

**1. Draw the one-line.** Import the PDF, rebuild the drawing with connected
equipment, and enter coil and motor currents in the properties panel.

**2. Run Evaluation.** If the layout has no zones yet, the tool proposes bus,
transformer and line zones (see [How zones are detected](#how-zones-are-detected)).
Once zones exist, evaluation uses their saved settings. Add more zones with
**Add Zone** in Configure Zones.

**3. Configure Zones** (Analysis tab). For each zone, review the protected
equipment, CT boundaries and operating loads.

- Only the CTs ticked for a zone are its boundaries; leave metering CTs unticked.
- Zones may overlap and share equipment.
- Choose a detected region to keep its exact equipment and connections, or pick
  the protected equipment and CTs yourself.

**4. Set breaker settings.** Select each breaker; these settings apply in every
zone and can be undone.

| Setting | Options |
|---|---|
| Coil current basis | Whole breaker, or per mechanism |
| Mechanisms | Shared, or independent per pole |
| Breaker poles | 1 to 3 |
| Supplies | DC supply for Trip Coil 1, Trip Coil 2, Close Coil and the motor |

**5. Assign operating loads.** In each load row, choose the equipment, the coil
or motor, the stage, and all-pole or one-pole operation.

- The multiplier comes from the breaker settings. Three independent mechanisms
  at 10 A each give 30 A for an all-pole trip; a shared mechanism or a
  whole-breaker current counts once.
- One-pole operation needs independent mechanisms and per-mechanism coil data.
- Motor current is for the whole device and is never multiplied by the poles.
- Non-breaker devices have an editable supply in the row.
- Trip Coil 2 is only included if you assign it.
- **Other loads:** choose **Other** as the equipment and type the amps in the
  **Load** cell. This is standing background current on DC 1 and is added to
  every stage in that zone; for example, a 10 A trip stage plus a 5 A Other load
  totals 15 A. A zone with only Other loads gets a **Background load** stage.
- **Selecting rows:** click the row numbers; Shift-click selects a range and
  Ctrl-click adds or removes rows. **Delete Selected Loads**, `Delete` or
  `Backspace` removes them after asking you to confirm.

**6. Verify and save.** Tick each zone's verification box, or use **Verify all
zones**, then Save.

- **Verify all zones** confirms zones that are complete and leaves the rest as
  drafts; hover over the summary to see why.
- Editing a zone clears its verification. Changing wiring, equipment or switch
  status also requires reconfirmation.
- Older layouts approved before the switch-aware detector must be reconfirmed.
  Compatible legacy settings are carried over; unsupported legacy multipliers
  are flagged for review.

### Calculation rules

- Loads in the same stage of a zone operate at the same time and are added.
- Different stages, and faults in different zones, are separate events and are
  never added together.
- Stages such as reclose and breaker failure are only included if you configure
  them. If primary and backup loads overlap, put them in the same stage.
- A motor's inrush and running currents can't be in the same stage.
- Each physical coil or motor counts once per stage.
- Each DC supply is reported separately, never combined into one battery total.
- CTs define zone boundaries and are never loads.
- Reports assume a **125 V DC** supply, but voltage doesn't scale the currents:
  enter currents for the DC supply voltage you intend.
- The tool doesn't infer protection from the PDF artwork or from equipment that
  sits close together. CT and relay assignments and the actual trip logic must
  be reviewed by the user.

### How zones are detected

- Proposals follow actual wires and junctions and stop at CTs. Wires that only
  cross on the drawing, without a junction, are not connected.
- An unbranched path through one closed breaker can extend a zone to the CTs on
  the far side, so neighbouring zones overlap at that breaker.
- A line terminal only gets a zone if its CT-bounded region includes a closed
  breaker. Metering taps that reach other bays through branches don't create
  line zones.
- Separate transformer zones need CTs between them; a region shared by a bus and
  a transformer doesn't qualify.
- Open breakers, disconnects and motor-operated switches split regions. Set
  **Normally Open** in a component's Status dropdown for normally open bypasses.
  A missing or unknown status counts as closed.
- Transfer buses stay on the drawing but never get their own zone. They're
  recognised by their name or a saved `bus_role` of `transfer`.
- Suggested trip breakers stop at open switches and skip open breakers.
- Duplicate proposals covering the same equipment are shown once.
- The zone selector also lists detected topology regions. These are for display
  only; they aren't fault events and get no loads.

### Results and reports

- A study is **incomplete** while any current is missing, a CT can't be reached,
  a zone is unverified, or a bus or transformer isn't covered by a zone.
  Incomplete values are labelled provisional, and supply peaks are withheld.
- The **calculation PDF** lists each zone's stage totals, review issues and
  loads, and each DC supply's peak current. Draft exports are marked incomplete.
- The **netlist PDF** is a separate list of components and connections.
- Use the zone selector above the analysis output to show one zone or all.
  Coloured outlines mark each zone; dashed outlines need review.
- **Validate** checks the drawing's connectivity separately.
- Any change to the layout clears the results; run the evaluation again before
  exporting.
- Zone settings are saved in the layout. Older layouts without zones still open.

### Provided one-line layouts

- The provided layouts retain assigned sample currents for breakers and
  motor-operated switches. These values are not manufacturer ratings.
- Both trip coils are assigned together in the Primary trip stage on DC 1.
  Closing, motor inrush and motor running are separate stages.
- Devices without a suggested zone have separate Manual operating scenarios;
  these do not assert additional protection zones.
- Review CT boundaries and operating loads in Configure Zones. Verify all zones
  confirms only zones that pass validation; incomplete zones remain drafts.
  Their exported calculations are marked provisional.

### Running the tests

Windows (PowerShell):

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -v
Remove-Item Env:QT_QPA_PLATFORM
```

Linux or macOS:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

GitHub runs these tests on Windows and Linux for every push.
