"""Editors and serialization for an operating-load row."""
import copy
from dataclasses import dataclass

from gft_opto.logic.breaker_settings import breaker_settings, SUPPLY_KEYS
from gft_opto.logic.protection_study import kind


@dataclass
class OperatingLoadRow:
    equipment: object
    load: object
    stage: object
    supply: object
    operation: object
    multiplier: object
    current: object
    saved_action: dict

    def to_action(self, components, zones):
        action = copy.deepcopy(self.saved_action)
        action.update(component=self.equipment.currentData() or "",
                      load=self.load.currentData(), stage=self.stage.currentText().strip(),
                      operation=self.operation.currentData())
        if action["component"].startswith("other:"):
            action.update(custom_load={
                "name": self.saved_action.get("custom_load", {}).get("name") or "Other load",
                "amps": self.current.value()}, stage="Background load",
                supply="DC 1", operation="device")
        else:
            action.pop("custom_load", None)
            component = components.get(action["component"])
            if component is None or kind(component) != "breaker":
                action["supply"] = self.supply.text().strip()
            elif action["operation"] != "legacy" and breaker_settings(
                    component, zones)[SUPPLY_KEYS[action["load"]]]:
                action.pop("supply", None)
        if action["operation"] != "legacy":
            action.pop("units", None)
        return action
