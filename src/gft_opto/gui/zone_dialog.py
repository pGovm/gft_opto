# Lets users review protection-zone boundaries and configure the loads and operating stages for each zone.

"""Editable CT boundaries and explicit per-stage coil/motor assignments."""
import copy
import uuid

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSpinBox, QTableWidget, QVBoxLayout, QWidget, QHeaderView,
)

from gft_opto.logic.protection_study import (
    LOADS, STAGES, kind, propose_zones, validate_zones, topology_signature,
    configuration_signature, analyze_topology, evaluate_zones, add_missing_proposals,
)
from gft_opto.logic.breaker_settings import breaker_settings, resolve_breaker_action, SUPPLY_KEYS
from gft_opto.gui.theme import SCROLLBAR_STYLE


class ZoneDialog(QDialog):
    def __init__(self, netlist, zones, parent=None):
        super().__init__(parent)
        self._apply_readable_theme()
        self.setWindowTitle("Protection Zones and Momentary Loads")
        self.resize(1050, 750)
        self.netlist = copy.deepcopy(netlist)
        netlist = self.netlist
        validate_zones(zones)
        self.zones = copy.deepcopy(zones)
        self.detected_regions = analyze_topology(netlist)
        signature = topology_signature(netlist)
        for zone in self.zones:
            if (zone.get("topology_signature") != signature or
                    zone.get("configuration_signature") != configuration_signature(zone)):
                zone["confirmed"] = False
        self.active = -1
        self.loading = False
        self.components = {c["id"]: c for c in netlist["components"]}
        conflicts = set()
        for ident, component in self.components.items():
            if kind(component) == "breaker":
                for supply_key in set(SUPPLY_KEYS.values()):
                    old_sources = {a["supply"] for z in zones for a in z["actions"]
                        if a["component"] == ident and SUPPLY_KEYS[a["load"]] == supply_key and a.get("supply")}
                    if supply_key not in component.get("properties", {}) and len(old_sources) > 1:
                        conflicts.add(ident)
        for zone in self.zones:
            if any(a["component"] in conflicts for a in zone["actions"]):
                zone["confirmed"] = False
        self.legacy_single_pole = {ident for ident, component in self.components.items()
            if kind(component) == "breaker" and "coil_current_basis" not in component.get("properties", {})
            and any(a["component"] == ident and a.get("units") == 3
                    and a["load"] in ("trip_coil_1_a", "trip_coil_2_a", "close_coil_a")
                    for z in zones for a in z["actions"])}
        for component in self.components.values():
            if kind(component) == "breaker":
                # An unresolved legacy supply is a draft problem, not a new
                # equipment property. Keep it absent until the user selects it.
                component.setdefault("properties", {}).update({
                    key: value for key, value in breaker_settings(component, self.zones).items()
                    if value != ""
                })
        outer = QVBoxLayout(self)
        guidance = QLabel(
            "Detected zones are proposals. Confirm CTs used by each protection scheme and its actual trip actions.\n"
            "Rows with the same stage operate simultaneously; different stages are evaluated separately.\n"
            "Set current basis, mechanisms, pole count and coil DC supplies in the selected breaker's properties.\n"
            "Choose one-pole or all-pole operation here; supply and multiplier are calculated from the breaker.\n"
            "Detected regions include wire-only and overlapping candidates; selecting one does not assign or confirm operating loads."
        )
        guidance.setWordWrap(True)
        if conflicts:
            guidance.setText(guidance.text() + "\nLegacy supply conflicts: " + ", ".join(sorted(conflicts))
                + ". Review these breaker's supply settings before confirming.")
        outer.addWidget(guidance)
        body = QHBoxLayout()
        outer.addLayout(body, 1)
        left = QVBoxLayout()
        self.zone_list = QListWidget()
        left.addWidget(self.zone_list)
        for label, callback in (("Detect Missing Zones", self.detect), ("Add Zone", self.add_zone), ("Delete Zone", self.delete_zone)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            left.addWidget(button)
        body.addLayout(left, 1)
        right = QVBoxLayout()
        body.addLayout(right, 4)
        form = QFormLayout()
        self.region_combo = QComboBox()
        self.region_combo.addItem("Custom seed and selected CTs", None)
        for region in self.detected_regions:
            relation = "enclosed" if region["enclosed_by"] else "outermost"
            self.region_combo.addItem(f"{region['name']} ({relation})", region["id"])
        self.region_combo.setToolTip("Select exact equipment and conductor membership. Existing load rows are retained for review.")
        form.addRow("Detected region", self.region_combo)
        self.name = QLineEdit()
        self.zone_kind = QComboBox()
        self.zone_kind.addItems(["Bus", "Transformer", "Line", "Manual"])
        self.seed = QComboBox()
        self.seed.addItem("No equipment seed (wire-only region)", "")
        for ident, component in self.components.items():
            if kind(component) not in ("ct", "pt", "ground", "junction"):
                self.seed.addItem(f"{component.get('label', ident)} [{ident}]", ident)
        form.addRow("Zone name", self.name)
        form.addRow("Zone type", self.zone_kind)
        form.addRow("Protected equipment", self.seed)
        right.addLayout(form)
        right.addWidget(QLabel("Boundary CTs (assigned to this protection scheme):"))
        self.cts = QListWidget()
        self.cts.setMaximumHeight(150)
        for ident, component in self.components.items():
            if kind(component) == "ct":
                entry = QListWidgetItem(f"{component.get('label', ident)} [{ident}]")
                entry.setData(Qt.ItemDataRole.UserRole, ident)
                entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                entry.setCheckState(Qt.CheckState.Unchecked)
                self.cts.addItem(entry)
        right.addWidget(self.cts)
        right.addWidget(QLabel("Operating loads — use a separate stage for reclose, motor operation, or breaker failure:"))
        self.actions = QTableWidget(0, 6)
        self.actions.setHorizontalHeaderLabels(["Equipment", "Load", "Stage", "DC supply", "Operation", "Multiplier"])
        self.actions.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.actions.verticalHeader().setDefaultSectionSize(44)
        right.addWidget(self.actions, 1)
        row = QHBoxLayout()
        for label, callback in (("Add Load", lambda: self.add_action()), ("Remove Load", self.remove_action)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        right.addLayout(row)
        self.confirmed = QCheckBox("I have verified this zone's CT boundaries and operating actions")
        right.addWidget(self.confirmed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.name.textEdited.connect(self.unconfirm)
        self.zone_kind.currentIndexChanged.connect(self.unconfirm)
        self.seed.currentIndexChanged.connect(self._custom_boundary_changed)
        self.cts.itemChanged.connect(self._custom_boundary_changed)
        self.region_combo.currentIndexChanged.connect(self._use_detected_region)
        self.zone_list.currentRowChanged.connect(self.select_zone)
        self.refresh_list()

    def _apply_readable_theme(self):
        # Some Windows dark palettes supply light text to otherwise white
        # table headers, spin boxes, and combo popups. Set both sides together.
        palette = QPalette(self.palette())
        colors = {
            QPalette.ColorRole.Window: "#f4f6f8",
            QPalette.ColorRole.WindowText: "#1f2933",
            QPalette.ColorRole.Base: "#ffffff",
            QPalette.ColorRole.AlternateBase: "#eef2f6",
            QPalette.ColorRole.Text: "#1f2933",
            QPalette.ColorRole.Button: "#ffffff",
            QPalette.ColorRole.ButtonText: "#1f2933",
            QPalette.ColorRole.Highlight: "#006a4e",
            QPalette.ColorRole.HighlightedText: "#ffffff",
            QPalette.ColorRole.PlaceholderText: "#52606d",
            QPalette.ColorRole.ToolTipBase: "#ffffff",
            QPalette.ColorRole.ToolTipText: "#1f2933",
        }
        for role, color in colors.items():
            palette.setColor(role, QColor(color))
        self.setPalette(palette)
        self.setStyleSheet("""
            QDialog { background-color: #f4f6f8; color: #1f2933; }
            QLabel, QCheckBox { color: #1f2933; background-color: transparent; }
            QTableWidget, QListWidget, QLineEdit, QComboBox, QSpinBox {
                background-color: #ffffff; color: #1f2933;
                selection-background-color: #006a4e; selection-color: #ffffff;
            }
            QTableCornerButton::section, QHeaderView::section {
                background-color: #e8edf2; color: #1f2933;
                border: 1px solid #cbd2d9; padding: 5px;
            }
            QHeaderView { background-color: #e8edf2; color: #1f2933; }
            QComboBox QAbstractItemView {
                background-color: #ffffff; color: #1f2933;
                selection-background-color: #006a4e; selection-color: #ffffff;
            }
            QListWidget::item:selected {
                background-color: #006a4e; color: #ffffff;
            }
            QPushButton {
                background-color: #006a4e; color: #ffffff;
                border: none; border-radius: 8px; padding: 8px 16px;
            }
            QPushButton:disabled { background-color: #d9e2ec; color: #52606d; }
        """ + SCROLLBAR_STYLE)

    def unconfirm(self, *_):
        if not self.loading:
            self.confirmed.setChecked(False)

    def _custom_boundary_changed(self, *_):
        if self.loading:
            return
        if 0 <= self.active < len(self.zones):
            self.zones[self.active].pop("topology_region", None)
        self.region_combo.blockSignals(True)
        self.region_combo.setCurrentIndex(0)
        self.region_combo.blockSignals(False)
        self.unconfirm()

    def _use_detected_region(self, *_):
        if self.loading or not 0 <= self.active < len(self.zones):
            return
        ident = self.region_combo.currentData()
        region = next((r for r in self.detected_regions if r["id"] == ident), None)
        if region is None:
            self.zones[self.active].pop("topology_region", None)
            self.unconfirm()
            return
        self.loading = True
        try:
            self.zones[self.active]["topology_region"] = copy.deepcopy(region["signature"])
            eligible = [i for i in region["core"] if i in self.components
                        and kind(self.components[i]) not in ("ct", "pt", "ground", "junction")]
            eligible.sort(key=lambda i: (kind(self.components[i]) != "bus", i))
            seed = eligible[0] if eligible else ""
            self.seed.setCurrentIndex(self.seed.findData(seed))
            for i in range(self.cts.count()):
                entry = self.cts.item(i)
                entry.setCheckState(Qt.CheckState.Checked if entry.data(Qt.ItemDataRole.UserRole)
                                    in region["boundaries"] else Qt.CheckState.Unchecked)
        finally:
            self.loading = False
        self.unconfirm()
        self.capture()

    def capture(self):
        if not 0 <= self.active < len(self.zones):
            return
        actions = []
        for row in range(self.actions.rowCount()):
            equipment = self.actions.cellWidget(row, 0)
            action = copy.deepcopy(getattr(equipment, "_saved_action", {}))
            action.update(component=equipment.currentData() or "",
                load=self.actions.cellWidget(row, 1).currentData(),
                stage=self.actions.cellWidget(row, 2).currentText().strip(),
                operation=self.actions.cellWidget(row, 4).currentData())
            # Quantities are derived for modern rows. Unresolved legacy drafts
            # retain their original quantity so review never loses information.
            if action["operation"] != "legacy":
                action.pop("units", None)
            component = self.components.get(action["component"])
            if component is None or kind(component) != "breaker":
                action["supply"] = self.actions.cellWidget(row, 3).text().strip()
            elif action["operation"] != "legacy" and breaker_settings(
                    component, self.zones)[SUPPLY_KEYS[action["load"]]]:
                action.pop("supply", None)
            actions.append(action)
        self.zones[self.active].update(name=self.name.text().strip(), kind=self.zone_kind.currentText(),
            seed=self.seed.currentData() or "", confirmed=self.confirmed.isChecked(), actions=actions,
            boundary_cts=[self.cts.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.cts.count())
                          if self.cts.item(i).checkState() == Qt.CheckState.Checked])
        self.zone_list.item(self.active).setText(self.zones[self.active]["name"] or "Unnamed Zone")

    def refresh_list(self, selected=0):
        self.zone_list.blockSignals(True)
        self.zone_list.clear()
        for zone in self.zones:
            self.zone_list.addItem(zone["name"])
        self.active = -1
        self.zone_list.blockSignals(False)
        if self.zones:
            self.zone_list.setCurrentRow(min(selected, len(self.zones) - 1))
        else:
            self.loading = True
            self.name.clear()
            self.actions.setRowCount(0)
            self.confirmed.setChecked(False)
            self.loading = False

    def select_zone(self, index):
        self.capture()
        self.active = index
        if index < 0:
            return
        self.loading = True
        zone = self.zones[index]
        self.name.setText(zone["name"])
        self.zone_kind.setCurrentText(zone["kind"])
        match = next((r for r in self.detected_regions
                      if r["signature"] == zone.get("topology_region")), None)
        self.region_combo.setCurrentIndex(self.region_combo.findData(match["id"]) if match else 0)
        if zone["seed"] and self.seed.findData(zone["seed"]) < 0:
            self.seed.addItem(f"Missing: {zone['seed']}", zone["seed"])
        self.seed.setCurrentIndex(self.seed.findData(zone["seed"]))
        existing = {self.cts.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.cts.count())}
        for ident in zone["boundary_cts"]:
            if ident not in existing:
                entry = QListWidgetItem(f"Missing CT: {ident}")
                entry.setData(Qt.ItemDataRole.UserRole, ident)
                entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                entry.setCheckState(Qt.CheckState.Unchecked)
                self.cts.addItem(entry)
        for i in range(self.cts.count()):
            entry = self.cts.item(i)
            entry.setCheckState(Qt.CheckState.Checked if entry.data(Qt.ItemDataRole.UserRole) in zone["boundary_cts"] else Qt.CheckState.Unchecked)
        self.actions.setRowCount(0)
        for action in zone["actions"]:
            self.add_action(action)
        self.confirmed.setChecked(zone["confirmed"])
        self.loading = False

    def add_action(self, action=None):
        if self.active < 0:
            return
        action = action or {"component": "", "load": "trip_coil_1_a", "stage": "Primary trip", "operation": "all_poles"}
        row = self.actions.rowCount()
        self.actions.insertRow(row)
        equipment, load, stage = QComboBox(), QComboBox(), QComboBox()
        equipment._saved_action = copy.deepcopy(action)
        equipment.addItem("Select equipment", "")
        for ident, component in self.components.items():
            if kind(component) not in ("ct", "pt", "ground", "junction", "bus"):
                equipment.addItem(f"{component.get('label', ident)} [{ident}]", ident)
        if equipment.findData(action["component"]) < 0:
            equipment.addItem(f"Missing: {action['component']}", action["component"])
        equipment.setCurrentIndex(equipment.findData(action["component"]))
        for key, label in LOADS.items():
            load.addItem(label, key)
        load.setCurrentIndex(load.findData(action["load"]))
        stage.setEditable(True)
        stage.addItems(STAGES)
        stage.setCurrentText(action["stage"])
        supply = QLineEdit(action.get("supply", "DC 1"))
        operation = QComboBox()
        for title, value in (("All poles", "all_poles"), ("One pole", "one_pole"), ("Whole device", "device")):
            operation.addItem(title, value)
        selected = action.get("operation")
        component = self.components.get(action["component"])
        if selected is None:
            selected = "device" if action["load"].startswith("motor_") else "one_pole" if action.get("units") == 1 and action["component"] in self.legacy_single_pole else "all_poles"
            if action.get("units", 1) not in (1, 3) or (action.get("units", 1) != 1 and (action["load"].startswith("motor_") or component is None or kind(component) != "breaker")):
                selected = "legacy"
        if selected == "legacy":
            operation.addItem("Legacy units — review", "legacy")
        operation.setCurrentIndex(operation.findData(selected))
        multiplier = QLineEdit()
        multiplier.setReadOnly(True)
        for col, widget in enumerate((equipment, load, stage, supply, operation, multiplier)):
            self.actions.setCellWidget(row, col, widget)
        def update_row(*_):
            self._update_action_preview(equipment, load, operation, supply, multiplier)
            self.unconfirm()
        for combo in (equipment, load, operation):
            combo.currentIndexChanged.connect(update_row)
        stage.currentTextChanged.connect(self.unconfirm)
        supply.textEdited.connect(self.unconfirm)
        self._update_action_preview(equipment, load, operation, supply, multiplier)
        self.unconfirm()

    def _update_action_preview(self, equipment, load, operation, supply, multiplier):
        component = self.components.get(equipment.currentData())
        is_breaker = component is not None and kind(component) == "breaker"
        supply.setReadOnly(is_breaker)
        motor = load.currentData().startswith("motor_")
        operation.blockSignals(True)
        if (motor or not is_breaker) and operation.currentData() != "legacy":
            operation.setCurrentIndex(operation.findData("device"))
        elif operation.currentData() == "device":
            operation.setCurrentIndex(operation.findData("all_poles"))
        operation.setEnabled((is_breaker and not motor) or operation.currentData() == "legacy")
        for index in (0, 1):
            operation.model().item(index).setEnabled(is_breaker and not motor)
        operation.model().item(2).setEnabled(motor or not is_breaker)
        operation.blockSignals(False)
        try:
            if is_breaker:
                supply.setText(breaker_settings(component, self.zones)[SUPPLY_KEYS[load.currentData()]])
                source, units = resolve_breaker_action(component,
                    {"load": load.currentData(), "operation": operation.currentData()}, self.zones)
                supply.setText(source)
                supply.setToolTip("Set this supply in the selected breaker's properties")
            else:
                if operation.currentData() == "legacy":
                    raise ValueError("Review the legacy multiplier and select Whole device.")
                units = 1
            multiplier.setText(f"×{units}")
            multiplier.setToolTip("Calculated from the equipment current basis and operating action")
        except ValueError as error:
            multiplier.setText("Review")
            multiplier.setToolTip(str(error))

    def remove_action(self):
        row = self.actions.currentRow()
        if row >= 0:
            self.actions.removeRow(row)
            self.unconfirm()

    def detect(self):
        try:
            self.capture()
            zones = add_missing_proposals(self.netlist, self.zones)
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, "Incomplete Zone Settings", str(error))
            return
        self.zones = zones
        self.refresh_list()

    def add_zone(self):
        self.capture()
        self.zones.append({"id": uuid.uuid4().hex, "name": "New Zone", "kind": "Manual",
            "seed": self.seed.currentData() or "", "boundary_cts": [], "actions": [], "confirmed": False})
        self.refresh_list(len(self.zones) - 1)

    def delete_zone(self):
        if self.active >= 0:
            del self.zones[self.active]
            self.refresh_list()

    def accept(self):
        self.capture()
        candidate = copy.deepcopy(self.zones)
        try:
            validate_zones(candidate)
            for zone in candidate:
                if zone["confirmed"]:
                    zone["topology_signature"] = topology_signature(self.netlist)
                    zone["configuration_signature"] = configuration_signature(zone)
            report = evaluate_zones(self.netlist, candidate)
            confirmed_ids = {z["id"] for z in candidate if z["confirmed"]}
            errors = [f"{row['name']}: {message}" for row in report["zones"]
                      if row["id"] in confirmed_ids for message in row["warnings"]]
            if errors:
                raise ValueError("\n".join(errors) + "\nUncheck confirmation to save these settings as a draft.")
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, "Incomplete Zone Settings", str(error))
            return
        self.zones = candidate
        super().accept()
