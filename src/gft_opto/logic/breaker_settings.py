# Interprets breaker current basis, mechanisms, pole count, and coil supplies to calculate action multipliers.

"""Equipment settings shared by every protection-zone action for a breaker."""

SUPPLY_KEYS = {
    "trip_coil_1_a": "trip_coil_1_supply", "trip_coil_2_a": "trip_coil_2_supply",
    "close_coil_a": "close_coil_supply", "motor_inrush_a": "motor_supply",
    "motor_run_a": "motor_supply",
}
SETTING_KEYS = ("coil_current_basis", "mechanism_arrangement", "pole_count", *dict.fromkeys(SUPPLY_KEYS.values()))


def breaker_settings(component, zones=()):
    """Use explicit equipment values, or infer compatible defaults for older layouts."""
    props = component.get("properties", {})
    actions = [a for z in zones for a in z["actions"] if a["component"] == component["id"]]
    per_pole = any(a.get("units") == 3 and a["load"] in ("trip_coil_1_a", "trip_coil_2_a", "close_coil_a") for a in actions)
    settings = {"coil_current_basis": "per_mechanism" if per_pole else "whole_breaker",
                "mechanism_arrangement": "independent" if per_pole else "shared", "pole_count": 3}
    for supply_key in set(SUPPLY_KEYS.values()):
        supplies = {a["supply"] for a in actions if SUPPLY_KEYS[a["load"]] == supply_key and a.get("supply")}
        settings[supply_key] = next(iter(supplies)) if len(supplies) == 1 else "DC 1"
    settings.update({key: props[key] for key in SETTING_KEYS if key in props})
    return settings


def resolve_breaker_action(component, action, zones):
    settings = breaker_settings(component, zones)
    basis, arrangement, poles = (settings[key] for key in ("coil_current_basis", "mechanism_arrangement", "pole_count"))
    if basis not in ("whole_breaker", "per_mechanism") or arrangement not in ("shared", "independent"):
        raise ValueError("Review the breaker's current basis and mechanism arrangement.")
    if isinstance(poles, bool) or not isinstance(poles, int) or not 1 <= poles <= 3:
        raise ValueError("Breaker pole count must be between 1 and 3.")
    operation = action.get("operation", "all_poles")
    if operation not in ("all_poles", "one_pole", "device"):
        raise ValueError("Replace the legacy units with an operating action in Configure Protection Zones.")
    motor = action["load"].startswith("motor_")
    if motor:
        units = 1
    elif operation == "one_pole":
        if arrangement != "independent" or basis != "per_mechanism":
            raise ValueError("One-pole operation requires independent mechanisms and a per-mechanism coil current.")
        units = 1
    else:
        units = poles if arrangement == "independent" and basis == "per_mechanism" else 1
    supply = settings[SUPPLY_KEYS[action["load"]]]
    if not isinstance(supply, str) or not supply.strip():
        raise ValueError("Set this coil's DC supply in the selected breaker's properties.")
    return supply.strip(), units
