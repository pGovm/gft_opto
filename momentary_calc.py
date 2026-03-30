# ============================================================
# Momentary DC Battery Load Calculator
# Substation worst-case scenario analysis (up to 12 scenarios)
# ============================================================
 
import json   
import csv  
import io   
from datetime import datetime  
 
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
            warnings.append("No loads added to this scenario!")
 
        # Peak current = sum of all loads firing at once (worst case)
        peak_amps = sum(load.total_amps() for load in self.loads)
 
        if peak_amps > 1000:
            warnings.append(
                f"Peak current is {peak_amps:.1f} A — that's over 1000 A, please double-check your inputs."
            )
        for load in self.loads:
            if load.current_A <= 0:
                warnings.append(f"'{load.name}' has zero or negative amps — please check.")
 
        load_details = []
        for load in self.loads:
            load_details.append({
                "name"         : load.name,
                "amps_per_unit": load.current_A,
                "quantity"     : load.quantity,
                "total_amps"   : round(load.total_amps(), 4),
                "note"         : load.note,
            })
 
        return {
            "scenario_name" : self.name,
            "voltage_V"     : self.voltage,
            "loads"         : load_details,
            "peak_current_A": round(peak_amps, 4),
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
                f"You've reached the maximum of {MAX_SCENARIOS} scenarios. "
                "Remove one before adding another."
            )
 
        scenario = Scenario(name, voltage)
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
 
 
def run_demo():
    calc = MomentaryLoadCalculator("GFT 230/33 kV Substation — 125 V DC Bus")
 
    # 1st scenario
    s1 = calc.new_scenario("S1 - Routine CB Switching")
    s1.add_load(Load("CB close operation", 12.0, quantity=2))
    s1.add_load(Load("CB trip operation",  10.0, quantity=2))
 
    # 2nd scenario
    s2 = calc.new_scenario("S2 - Maximum Fault Clearance")
    s2.add_load(Load("Main CB trip phase A", 18.0))
    s2.add_load(Load("Main CB trip phase B", 18.0))
    s2.add_load(Load("Main CB trip phase C", 18.0))
    s2.add_load(Load("Backup CB trip",       16.0, note="Backup protection"))
    s2.add_load(Load("Load shed breakers",   14.0, quantity=4))
    s2.add_load(Load("Auto-reclose attempt", 15.0))
 
    # 3rd scenario
    s3 = calc.new_scenario("S3 - Busbar Fault All Feeders Trip")
    s3.add_load(Load("Bus-zone CB trips",      20.0, quantity=8,
                     note="All 8 feeders trip at the same time"))
    s3.add_load(Load("Bus coupler trip",       22.0))
    s3.add_load(Load("Transfer bus isolation", 16.0, quantity=3))
 
    results = calc.run_all()
    calc.print_summary(results)
 
    calc.save_json(results, "momentary_results.json")
    calc.save_csv(results,  "momentary_results.csv")
    print()
 
if __name__ == "__main__":
    run_demo()