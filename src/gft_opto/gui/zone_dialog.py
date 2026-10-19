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

from gft_opto.logic.protection_zones import LOADS, STAGES, kind, propose_zones, validate_zones, topology_signature


class ZoneDialog(QDialog):
    def __init__(self, netlist, zones, parent=None):
        super().__init__(parent)
        self._apply_readable_theme()
        self.setWindowTitle("Protection Zones and Momentary Loads")
        self.resize(1050, 750)
        self.netlist = netlist
        self.zones = copy.deepcopy(zones)
        signature = topology_signature(netlist)
        for zone in self.zones:
            if zone.get("topology_signature") != signature:
                zone["confirmed"] = False
        self.active = -1
        self.loading = False
        self.components = {c["id"]: c for c in netlist["components"]}
        outer = QVBoxLayout(self)
        guidance = QLabel(
            "Detected zones are proposals. Confirm CTs used by each protection scheme and its actual trip actions.\n"
            "Rows with the same stage operate simultaneously; different stages are evaluated separately.\n"
            "Units: 1 if current is for the whole breaker; 3 only if the entered current is per pole and three poles operate.\n"
            "Set the actual DC supply for each coil. Unchecked CTs are not zone boundaries."
        )
        guidance.setWordWrap(True)
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
        self.name = QLineEdit()
        self.zone_kind = QComboBox()
        self.zone_kind.addItems(["Bus", "Transformer", "Line", "Manual"])
        self.seed = QComboBox()
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
        self.actions = QTableWidget(0, 5)
        self.actions.setHorizontalHeaderLabels(["Equipment", "Load", "Stage", "DC supply", "Units"])
        self.actions.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.actions.verticalHeader().setDefaultSectionSize(44)
        right.addWidget(self.actions, 1)
        row = QHBoxLayout()
        for label, callback in (("Add Load", lambda: self.add_action()), ("Remove Load", self.remove_action)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        right.addLayout(row)
        self.confirmed = QCheckBox("I have verified this zone's CT boundaries, actions, supply assignments, and units")
        right.addWidget(self.confirmed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.name.textEdited.connect(self.unconfirm)
        self.zone_kind.currentIndexChanged.connect(self.unconfirm)
        self.seed.currentIndexChanged.connect(self.unconfirm)
        self.cts.itemChanged.connect(self.unconfirm)
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
        """)

    def unconfirm(self, *_):
        if not self.loading:
            self.confirmed.setChecked(False)

    def capture(self):
        if not 0 <= self.active < len(self.zones):
            return
        actions = []
        for row in range(self.actions.rowCount()):
            actions.append({"component": self.actions.cellWidget(row, 0).currentData() or "",
                "load": self.actions.cellWidget(row, 1).currentData(),
                "stage": self.actions.cellWidget(row, 2).currentText().strip(),
                "supply": self.actions.cellWidget(row, 3).text().strip(),
                "units": self.actions.cellWidget(row, 4).value()})
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
        self.seed.setCurrentIndex(self.seed.findData(zone["seed"]))
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
        action = action or {"component": "", "load": "trip_coil_1_a", "stage": "Primary trip", "supply": "DC 1", "units": 1}
        row = self.actions.rowCount()
        self.actions.insertRow(row)
        equipment, load, stage = QComboBox(), QComboBox(), QComboBox()
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
        supply = QLineEdit(action["supply"])
        units = QSpinBox()
        units.setRange(1, 100)
        units.setValue(action["units"])
        for col, widget in enumerate((equipment, load, stage, supply, units)):
            self.actions.setCellWidget(row, col, widget)
        for combo in (equipment, load, stage):
            combo.currentTextChanged.connect(self.unconfirm)
        supply.textEdited.connect(self.unconfirm)
        units.valueChanged.connect(self.unconfirm)
        self.unconfirm()

    def remove_action(self):
        row = self.actions.currentRow()
        if row >= 0:
            self.actions.removeRow(row)
            self.unconfirm()

    def detect(self):
        self.capture()
        seeds = {z["seed"] for z in self.zones}
        self.zones.extend(z for z in propose_zones(self.netlist) if z["seed"] not in seeds)
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
        try:
            validate_zones(self.zones)
        except ValueError as error:
            QMessageBox.warning(self, "Incomplete Zone Settings", str(error))
            return
        for zone in self.zones:
            if zone["confirmed"]:
                zone["topology_signature"] = topology_signature(self.netlist)
        super().accept()
