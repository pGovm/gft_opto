
from gft_opto.gui import messages
# Defines DC loads and scenarios, totals their momentary currents, and supports netlist conversion and CSV/JSON results.

# ============================================================
# Momentary DC Battery Load Calculator
# Substation worst-case scenario analysis (up to 12 scenarios)
# Explicit simultaneous loads use Scenario.add_load(...).
# Configured netlists and layouts use evaluate_netlist(), which delegates to
# the GUI's protection-study engine rather than combining unrelated events.
# ============================================================

import json
import csv
import io
import sys
from datetime import datetime

# Highest current accepted for one device field or other load. Substation DC
# coils and motors draw tens of amps; anything above this is almost certainly a
# typing or units mistake, so it cannot produce a complete study.
MAX_DEVICE_CURRENT_A = 1000.0


def format_amps(value) -> str:
    """Amps for messages: one decimal place, or three significant figures when huge."""
    return f"{value:.1f}" if abs(value) < 1e6 else f"{value:.3g}"


class Load:

    def __init__(self, name, current_A, quantity=1, note=""):
        self.name      = name
        self.current_A = current_A
        self.quantity  = quantity
        self.note      = note

    def total_amps(self):
        return self.current_A * self.quantity


class Scenario:

    def __init__(self, name, voltage=125.0):
        self.name    = name
        self.voltage = voltage
        self.loads   = []

    def add_load(self, load):
        self.loads.append(load)

    def calculate(self):

        warnings = []

        if len(self.loads) == 0:
            warnings.append(messages.MOMENTARY_CALC2_NO_LOADS_ADDED_TO_THIS_SCENARIO)

        # peak current = sum of all loads firing at once (worst case)
        peak_amps = sum(load.total_amps() for load in self.loads)

        if peak_amps > 1000:
            warnings.append(
                messages.MOMENTARY_CALC2_PEAK_CURRENT_IS_A_THAT_S_OVER_1000_A.format(format_amps(peak_amps))
            )
        for load in self.loads:
            if load.current_A <= 0:
                warnings.append(messages.MOMENTARY_CALC2_HAS_ZERO_OR_NEGATIVE_AMPS_PLEASE_CHECK.format(load.name))

        load_details = []
        calculation_steps = [
            "Each load total = quantity × current per unit."
        ]
        for load in self.loads:
            total_amps = load.total_amps()
            load_details.append({
                "name"         : load.name,
                "amps_per_unit": load.current_A,
                "quantity"     : load.quantity,
                "total_amps"   : round(total_amps, 4),
                "note"         : load.note,
            })
            calculation_steps.append(
                f"{load.name}: {load.quantity:g} × {load.current_A:g} A "
                f"= {total_amps:g} A"
            )

        if load_details:
            load_totals = " + ".join(
                f"{load['total_amps']:g} A" for load in load_details
            )
            calculation_steps.append(
                f"Peak current = {load_totals} = {peak_amps:g} A."
            )
        else:
            calculation_steps.append("Peak current = 0 A (no loads).")

        return {
            "scenario_name" : self.name,
            "voltage_V"     : self.voltage,
            "loads"         : load_details,
            "peak_current_A": round(peak_amps, 4),
            "calculation_steps": calculation_steps,
            "warnings"      : warnings,
            "timestamp"     : datetime.now().isoformat(),
        }


MAX_SCENARIOS = 12

class MomentaryLoadCalculator:

    def __init__(self, system_name="DC Momentary Load Study"):
        self.system_name = system_name
        self.scenarios   = []

    def new_scenario(self, name, voltage=125.0):

        if len(self.scenarios) >= MAX_SCENARIOS:
            raise Exception(
                messages.MOMENTARY_CALC2_YOU_VE_REACHED_THE_MAXIMUM_OF_SCENARIOS_REMOVE_ONE.format(MAX_SCENARIOS)
            )

        scenario = Scenario(name, voltage)
        self.scenarios.append(scenario)
        return scenario

    def add_scenario(self, scenario: Scenario):
        """Attach an already-built Scenario (e.g. from scenario_from_netlist)."""
        if len(self.scenarios) >= MAX_SCENARIOS:
            raise Exception(
                messages.MOMENTARY_CALC2_YOU_VE_REACHED_THE_MAXIMUM_OF_SCENARIOS_REMOVE_ONE.format(MAX_SCENARIOS)
            )
        self.scenarios.append(scenario)
        return scenario

    def remove_scenario(self, name):
        original_count = len(self.scenarios)
        self.scenarios = [s for s in self.scenarios if s.name != name]
        return len(self.scenarios) < original_count

    def list_scenarios(self):
        return [s.name for s in self.scenarios]

    def run_all(self):

        all_results = []
        for scenario in self.scenarios:
            result = scenario.calculate()
            all_results.append(result)

        all_results.sort(key=lambda r: r["peak_current_A"], reverse=True)

        return all_results

    def run_one(self, name):
        for scenario in self.scenarios:
            if scenario.name == name:
                return scenario.calculate()
        print(f"Scenario '{name}' not found.")
        return None

    def print_summary(self, results):
        print()
        print(f"  {self.system_name}")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print()

        for i, r in enumerate(results):
            if i == 0:
                print(f"  >>> WORST CASE: {r['scenario_name']}")
                print(f"      Peak current = {r['peak_current_A']:.1f} A  <-- highest")
            else:
                print(f"      {r['scenario_name']}: {r['peak_current_A']:.1f} A")

        print()

    def save_json(self, results, filename=None):
        data = {
            "system_name"   : self.system_name,
            "created_at"    : datetime.now().isoformat(),
            "scenario_count": len(results),
            "worst_case": {
                "name"          : results[0]["scenario_name"]  if results else None,
                "peak_current_A": results[0]["peak_current_A"] if results else None,
            },
            "scenarios": results,
        }

        json_text = json.dumps(data, indent=2)

        if filename:
            with open(filename, "w") as f:
                f.write(json_text)
            print(f"  Saved JSON to: {filename}")

        return json_text

    def save_csv(self, results, filename=None):
        columns = [
            "scenario_name", "voltage_V",
            "peak_current_A", "warnings",
        ]

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()

        for r in results:
            row = dict(r)
            row["warnings"] = "; ".join(r["warnings"])
            writer.writerow({col: row[col] for col in columns})

        csv_text = buffer.getvalue()

        if filename:
            with open(filename, "w", newline="") as f:
                f.write(csv_text)
            print(f"  Saved CSV  to: {filename}")

        return csv_text


NETLIST_PROPERTY_LOAD_MAP = {
    "trip_coil_1_a" : "Trip Coil 1",
    "trip_coil_2_a" : "Trip Coil 2",
    "close_coil_a"  : "Close Coil",
    "motor_inrush_a": "Motor Inrush",
    "motor_run_a"   : "Motor Run",
}


def loads_from_component(component: dict) -> list:
    """Retired: a list of device fields does not define simultaneous actions."""
    raise ValueError(messages.MOMENTARY_CALC2_EXPLICIT_OPERATING_ACTIONS_REQUIRED)


def scenario_from_netlist(netlist: dict, name: str | None = None, voltage: float | None = None) -> Scenario:
    """Retired; use evaluate_netlist with configured zones and stages."""
    raise ValueError(messages.MOMENTARY_CALC2_EXPLICIT_OPERATING_ACTIONS_REQUIRED)


def calculator_from_netlist(netlist: dict, system_name: str | None = None) -> MomentaryLoadCalculator:
    """Retired; a protection study cannot be represented by one all-load event."""
    raise ValueError(messages.MOMENTARY_CALC2_EXPLICIT_OPERATING_ACTIONS_REQUIRED)


def calculator_from_netlist_file(path: str, system_name: str | None = None) -> MomentaryLoadCalculator:
    """Retired; use run_from_netlist_file for configured protection studies."""
    raise ValueError(messages.MOMENTARY_CALC2_EXPLICIT_OPERATING_ACTIONS_REQUIRED)


def netlist_instance_ids(components) -> dict:
    """Map build_netlist component IDs back to the drawing's instance IDs.

    build_netlist names each component "<type>:<instance id>", while layouts and
    saved zones use the instance ID alone. The mapping strips exactly that
    prefix. It is empty when stripping would make two components share an ID.
    """
    if not isinstance(components, list):
        return {}
    ids = [c.get("id") for c in components if isinstance(c, dict)]
    renamed = {}
    for component in components:
        if not isinstance(component, dict):
            continue
        ident, typ = component.get("id"), component.get("type")
        if isinstance(ident, str) and isinstance(typ, str) and typ:
            prefix = f"{typ}:"
            if ident.startswith(prefix) and ident[len(prefix):].strip():
                renamed[ident] = ident[len(prefix):]
    final = [renamed.get(ident, ident) for ident in ids]
    if len(set(final)) != len(final):
        return {}
    return renamed


def evaluate_netlist(netlist: dict, zones: list[dict] | None = None) -> dict:
    """Evaluate saved layouts or netlists with the same engine as the GUI.

    Saved zones are required. No load fields are automatically assigned to events.
    Draft studies retain provisional stage totals and withhold final supply peaks.
    """
    # Lazy import: protection_study uses the explicit Load/Scenario primitives.
    import copy
    from gft_opto.logic.protection_study import evaluate_zones

    if not isinstance(netlist, dict):
        raise ValueError(messages.PROTECTION_STUDY_NETLIST_MUST_BE_AN_OBJECT)
    data = copy.deepcopy(netlist)
    if zones is None:
        zones = data.get("zones")
    if zones is None:
        raise ValueError(messages.MOMENTARY_CALC2_SAVED_ZONES_REQUIRED)
    components = data.get("components")
    # Netlists written by build_netlist prefix IDs with the type; zones do not.
    renamed = netlist_instance_ids(components) if data.get("schema_version") == 1 else {}
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                component.setdefault("type", component.get("symbol", "custom"))
                component.setdefault("label", component.get("display_id") or component.get("name") or component.get("id"))
                if component.get("id") in renamed:
                    component["id"] = renamed[component["id"]]
    connections = data.get("connections")
    if isinstance(connections, list):
        for index, connection in enumerate(connections):
            if isinstance(connection, dict) and ("from" in connection or "to" in connection):
                a, b = connection.get("from"), connection.get("to")
                if not isinstance(a, dict) or not isinstance(b, dict):
                    raise ValueError(messages.PROTECTION_STUDY_EACH_CONNECTION_NEEDS_TWO_COMPONENT_IDS_AND_TWO_TERMINALS)
                a_id, b_id = a.get("component"), b.get("component")
                connections[index] = dict(from_id=renamed.get(a_id, a_id), from_port=a.get("port"),
                                          to_id=renamed.get(b_id, b_id), to_port=b.get("port"))
    return evaluate_zones(data, zones)


def netlist_from_scene(scene, system_name: str = "Netlist Study", voltage_V: float = 125.0) -> dict:
    """
    Build a netlist dict directly from a live QGraphicsScene
    (e.g. your SubstationGuiMockup's `self.workspace_scene`).

    Only built-in Python/PySide6 objects are touched — no import of your
    GUI module is required, so this has zero coupling to gft_gui.py.
    """
    components = []
    connections = []
    seen_connections = set()

    # Snapshot once and use object identity for membership in this exact scene.
    # An off-scene endpoint can share an ID with an in-scene object after reload.
    items = list(scene.items())
    symbol_objects = {id(item) for item in items
                      if hasattr(item, "instance_id") and hasattr(item, "equip_type")}

    for item in items:
        
        if hasattr(item, "instance_id") and hasattr(item, "equip_type"):
            # Name each component by the ID drawn beside it, not its type name.
            shown = getattr(item, "visible_id", None)
            components.append({
                "id"        : item.instance_id,
                "type"      : item.equip_type,
                "label"     : shown() if callable(shown) else getattr(item, "label", item.equip_type),
                
                "properties": dict(getattr(item, "properties", {}) or {}),
            })
            continue

        
        if all(hasattr(item, attr) for attr in ("from_item", "from_port", "to_item", "to_port")):
            from_item, to_item = item.from_item, item.to_item
            if (from_item is None or to_item is None or
                    id(from_item) not in symbol_objects or id(to_item) not in symbol_objects):
                continue
            from_id = getattr(from_item, "instance_id", None)
            to_id = getattr(to_item, "instance_id", None)
            if from_id is None or to_id is None:
                continue
            # Keep ID and terminal as separate values. Concatenating with ':'
            # can collapse distinct wires when either string contains ':'.
            endpoints = ((from_id, item.from_port), (to_id, item.to_port))
            key = tuple(sorted(endpoints, key=lambda endpoint: (
                str(endpoint[0]), "" if endpoint[1] is None else str(endpoint[1]))))
            if key in seen_connections:
                continue
            seen_connections.add(key)
            connections.append({
                "from_id"  : from_id,
                "from_port": item.from_port,
                "to_id"    : to_id,
                "to_port"  : item.to_port,
            })

    return {
        "system_name": system_name,
        "voltage_V"  : voltage_V,
        "components" : components,
        "connections": connections,
    }


def evaluate_scene(scene, system_name: str = "Netlist Study", voltage_V: float = 125.0,
                   zones: list[dict] | None = None) -> dict:
    """Evaluate explicit scene zone assignments; return a protection-study report."""
    netlist = netlist_from_scene(scene, system_name=system_name, voltage_V=voltage_V)
    return evaluate_netlist(netlist, zones=zones)


def _read_json(path):
    from pathlib import Path
    with Path(path).open(encoding="utf-8") as stream:
        try:
            return json.load(stream)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path} is not a valid JSON file ({error}).") from None


def _zones_from_file(path) -> list:
    data = _read_json(path)
    zones = data.get("zones") if isinstance(data, dict) else data
    if not isinstance(zones, list):
        raise ValueError(messages.MOMENTARY_CALC2_ZONES_FILE_INVALID.format(path))
    if not zones:
        raise ValueError(messages.MOMENTARY_CALC2_NO_ZONES_IN_FILE.format(path))
    return zones


def results_paths(path, output_dir=None) -> tuple:
    """Result files named after the input, in its folder unless one is given."""
    from pathlib import Path
    source = Path(path)
    folder = Path(output_dir) if output_dir else source.parent
    stem = source.stem or "momentary"
    return folder / f"{stem}_results.json", folder / f"{stem}_results.csv"


def run_from_netlist_file(path, zones_path=None, output_dir=None):
    """Export per-zone/stage/supply calculations without an all-load aggregate.

    Results go beside the input as <name>_results.json and <name>_results.csv,
    or into ``output_dir``. Zones come from the input file, or from
    ``zones_path`` (a saved layout or a JSON list of zones).
    """
    netlist = _read_json(path)
    if zones_path is not None:
        zones = _zones_from_file(zones_path)
    else:
        zones = netlist.get("zones") if isinstance(netlist, dict) else None
        if zones is None:
            raise ValueError(messages.MOMENTARY_CALC2_NO_ZONES_IN_FILE.format(path))
    report = evaluate_netlist(netlist, zones)
    json_path, csv_path = results_paths(path, output_dir)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    replaced = [p for p in (json_path, csv_path) if p.exists()]
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["zone", "stage", "supply", "current_A", "zone_complete", "study_complete"])
        for zone in report["zones"]:
            for scenario in zone["scenarios"]:
                for supply, amps in scenario["supplies"].items():
                    writer.writerow([zone["name"], scenario["stage"], supply, amps,
                                     zone["complete"], report["complete"]])
    print("COMPLETE" if report["complete"] else "INCOMPLETE - provisional stage totals only")
    for supply, peak in report["supply_peaks"].items():
        print(f"{supply}: {peak['peak_current_A']:g} A - {peak['zone']} / {peak['stage']}")
    for result in (json_path, csv_path):
        print(f"{'Replaced' if result in replaced else 'Saved'} {result}")
    return report


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m gft_opto.logic.momentary_calc2",
        description=("Calculate the momentary DC current of each configured protection zone, "
                     "using the same engine as the application."),
    )
    parser.add_argument("input", help="a saved layout (.json) with configured zones, or an exported netlist")
    parser.add_argument("--zones", metavar="LAYOUT",
                        help="use the zones saved in this layout (needed when the input is a netlist)")
    parser.add_argument("--output-dir", metavar="FOLDER",
                        help="where to write <input name>_results.json and .csv (default: the input's folder)")
    args = parser.parse_args(argv)
    try:
        run_from_netlist_file(args.input, zones_path=args.zones, output_dir=args.output_dir)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
