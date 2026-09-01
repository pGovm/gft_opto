from momentary_calc2 import netlist_from_scene, evaluate_scene

# Fake test of netlist 
class FakeSymbol:
    def __init__(self, instance_id, equip_type, label, properties=None):
        self.instance_id = instance_id
        self.equip_type = equip_type
        self.label = label
        if properties is not None:
            self.properties = properties

class FakeConn:
    def __init__(self, from_item, from_port, to_item, to_port):
        self.from_item = from_item
        self.from_port = from_port
        self.to_item = to_item
        self.to_port = to_port

class FakeScene:
    def __init__(self, items):
        self._items = items
    def items(self):
        return self._items

mos = FakeSymbol("custom_mos_relay_1", "custom_mos_relay", "MOS Relay", properties={
    "trip_coil_1_a": 10.0, "trip_coil_2_a": 10.0, "close_coil_a": 12.0,
    "motor_inrush_a": 8.0, "motor_run_a": 2.0,
})
xfmr = FakeSymbol("xfmr_2w_1", "xfmr_2w", "Transformer (2-winding)")  # no properties
conn = FakeConn(mos, "right", xfmr, "H")

scene = FakeScene([mos, xfmr, conn])

print(netlist_from_scene(scene, system_name="Test Bay"))
print()
result = evaluate_scene(scene, system_name="Test Bay")
print("Peak A:", result["peak_current_A"])
for ld in result["loads"]:
    print(" ", ld["name"], ld["total_amps"])