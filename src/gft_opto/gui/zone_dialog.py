# Lets users review protection-zone boundaries and configure the loads and operating stages for each zone.

"""Editable CT boundaries and explicit per-stage coil/motor assignments."""

from gft_opto.gui import messages
import copy
import uuid

from PySide6.QtCore import Qt, QEvent, QPoint, QItemSelectionModel
from PySide6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSpinBox, QTableWidget, QVBoxLayout, QWidget, QHeaderView, QAbstractItemView, QDoubleSpinBox,
    QTableWidgetSelectionRange, QStackedWidget, QSplitter, QScrollArea, QFrame,
)

from gft_opto.logic.protection_study import (
    LOADS, STAGES, kind, propose_zones, validate_zones, topology_signature,
    configuration_signature, analyze_topology, evaluate_zones,
)
from gft_opto.logic.breaker_settings import breaker_settings, resolve_breaker_action, SUPPLY_KEYS
from gft_opto.gui.theme import SCROLLBAR_STYLE
from gft_opto.gui.operating_loads import OperatingLoadRow


class _SelectedLoadHeader(QHeaderView):
    def __init__(self, parent):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.selected_rows = set()

    def paintSection(self, painter, rect, logical_index):
        if logical_index not in self.selected_rows:
            super().paintSection(painter, rect, logical_index)
            return
        painter.save()
        painter.fillRect(rect, QColor("#006a4e"))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(logical_index + 1))
        painter.restore()


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
        self.guidance = QLabel(
            "Detected zones are proposals. Confirm CTs used by each protection scheme and its actual trip actions.\n"
            "Rows with the same stage operate simultaneously; different stages are evaluated separately.\n"
            "Set current basis, mechanisms, pole count and coil DC supplies in the selected breaker's properties.\n"
            "Choose one-pole or all-pole operation here; supply and multiplier are calculated from the breaker.\n"
            "Nearby breakers are suggestions only and are not assigned as operating loads.\n"
            "Detected regions include wire-only and overlapping candidates; selecting one does not confirm operating loads."
        )
        self.guidance.setWordWrap(True)
        if conflicts:
            self.guidance.setText(self.guidance.text() + messages.ZONE_DIALOG_LEGACY_SUPPLY_CONFLICTS.format(", ".join(sorted(conflicts)))
                + messages.ZONE_DIALOG_REVIEW_THESE_BREAKER_S_SUPPLY_SETTINGS_BEFORE_CONFIRMING)
        self._base_guidance = self.guidance.text()
        self.demo_mode = QCheckBox("Demonstration mode (allow incomplete demo zones)")
        self.demo_mode.setChecked(bool(self.netlist.get("demonstration_mode", False)))
        outer.addWidget(self.demo_mode)
        self.demo_mode.toggled.connect(self._change_demo_mode)
        body = QHBoxLayout()
        outer.addLayout(body, 1)
        left = QVBoxLayout()
        self.verify_all = QCheckBox("Verify all zones")
        self.verify_all.setToolTip(
            "Confirm all zones that pass validation. Incomplete zones stay "
            "as drafts so they do not prevent saving the demonstration."
        )
        self.verification_summary = QLabel()
        self.verification_summary.setWordWrap(True)
        self.verification_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.zone_list = QListWidget()
        left.addWidget(self.zone_list)
        for label, callback in (("Add Zone", self.add_zone), ("Delete Zone", self.delete_zone)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            left.addWidget(button)
        body.addLayout(left, 1)
        right = QVBoxLayout()
        body.addLayout(right, 4)
        self.zone_splitter = QSplitter(Qt.Orientation.Vertical)
        self.zone_splitter.setObjectName("zoneDetailsSplitter")
        self.zone_splitter.setChildrenCollapsible(False)
        self.zone_splitter.setHandleWidth(4)
        right.addWidget(self.zone_splitter, 1)
        settings_scroll = QScrollArea()
        settings_scroll.setObjectName("zoneSettingsScroll")
        settings_scroll.viewport().setObjectName("zoneSettingsViewport")
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setMinimumHeight(90)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_panel = QWidget()
        settings_panel.setObjectName("zoneSettingsPanel")
        settings_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.addWidget(self.guidance)
        settings_scroll.setWidget(settings_panel)
        self.zone_splitter.addWidget(settings_scroll)
        loads_panel = QWidget()
        loads_layout = QVBoxLayout(loads_panel)
        loads_layout.setContentsMargins(0, 0, 0, 0)
        self.zone_splitter.addWidget(loads_panel)
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
            if kind(component) not in ("ct", "pt", "ground", "junction", "surge_arrester", "incoming_line"):
                self.seed.addItem(f"{component.get('label', ident)} [{ident}]", ident)
        form.addRow("Zone name", self.name)
        form.addRow("Zone type", self.zone_kind)
        form.addRow("Protected equipment", self.seed)
        settings_layout.addLayout(form)
        settings_layout.addWidget(QLabel("Boundary CTs (assigned to this protection scheme):"))
        self.cts = QListWidget()
        self.cts.setToolTip(messages.ZONE_DIALOG_SELECT_ONLY_THE_OUTER_CT_BOUNDARIES_FOR_THIS_SCHEME)
        self.cts.setMaximumHeight(150)
        for ident, component in self.components.items():
            if kind(component) == "ct":
                entry = QListWidgetItem(f"{component.get('label', ident)} [{ident}]")
                entry.setData(Qt.ItemDataRole.UserRole, ident)
                entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                entry.setCheckState(Qt.CheckState.Unchecked)
                self.cts.addItem(entry)
        settings_layout.addWidget(self.cts)
        loads_layout.addWidget(QLabel(messages.ZONE_DIALOG_OPERATING_LOADS_USE_A_SEPARATE_STAGE_FOR_RECLOSE_MOTOR))
        self.load_rows = []
        self._load_selection_anchor = None
        self.actions = QTableWidget(0, 6)
        self.actions.setVerticalHeader(_SelectedLoadHeader(self.actions))
        self.actions.setHorizontalHeaderLabels(["Equipment", "Load", "Stage", "DC supply", "Operation", "Multiplier"])
        self.actions.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.actions.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Delete or Backspace removes the selected load rows. Text fields in the
        # table claim these keys first, so typing still edits the text.
        for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            shortcut = QShortcut(QKeySequence(key), self.actions)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.remove_action)
        self.actions.horizontalHeader().setSectionsClickable(True)
        self.actions.verticalHeader().setSectionsClickable(True)
        self.actions.verticalHeader().viewport().installEventFilter(self)
        self.actions.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.actions.verticalHeader().setDefaultSectionSize(44)
        self.actions.ensurePolished()
        self.actions.horizontalHeader().ensurePolished()
        table_margins = self.actions.contentsMargins()
        self.actions.setMinimumHeight(
            5 * self.actions.verticalHeader().defaultSectionSize()
            + self.actions.horizontalHeader().sizeHint().height()
            + table_margins.top() + table_margins.bottom()
            + self.actions.horizontalScrollBar().sizeHint().height())
        loads_layout.addWidget(self.actions, 1)
        self.zone_splitter.setStretchFactor(0, 1)
        self.zone_splitter.setStretchFactor(1, 2)
        self.zone_splitter.setSizes([230, 300])
        row = QHBoxLayout()
        add_load = QPushButton("Add Load")
        add_load.clicked.connect(lambda: self.add_action())
        row.addWidget(add_load)
        self.delete_loads_button = QPushButton("Delete Selected Loads")
        self.delete_loads_button.setEnabled(False)
        self.delete_loads_button.clicked.connect(self.remove_action)
        self.actions.itemSelectionChanged.connect(self._sync_delete_load_button)
        row.addWidget(self.delete_loads_button)
        right.addLayout(row)
        self.confirmed = QCheckBox("I have verified this zone's CT boundaries and operating actions")
        right.addWidget(self.confirmed)
        right.addWidget(self.verify_all)
        right.addWidget(self.verification_summary)
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
        self.verify_all.toggled.connect(self._verify_all_zones)
        self.confirmed.toggled.connect(self._sync_verify_all)
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
            QScrollArea#zoneSettingsScroll, QWidget#zoneSettingsViewport,
            QWidget#zoneSettingsPanel {
                background-color: #f4f6f8; color: #1f2933; border: none;
            }
            QSplitter#zoneDetailsSplitter { background-color: #f4f6f8; }
            QSplitter#zoneDetailsSplitter::handle {
                background-color: #f4f6f8;
                border: none;
            }
            QLabel, QCheckBox { color: #1f2933; background-color: transparent; }
            QTableWidget, QListWidget, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #ffffff; color: #1f2933;
                selection-background-color: #006a4e; selection-color: #ffffff;
            }
            QDoubleSpinBox QLineEdit {
                background-color: #ffffff; color: #1f2933;
                selection-background-color: #006a4e; selection-color: #ffffff;
            }
            QWidget[loadSelected="true"], QLineEdit[loadSelected="true"],
            QComboBox[loadSelected="true"], QDoubleSpinBox[loadSelected="true"] {
                background-color: #d9eee5; color: #1f2933;
                border: 1px solid #006a4e;
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

    def _sync_verify_all(self, *_):
        if self.loading:
            return
        verified = [self.confirmed.isChecked() if index == self.active
                    else zone["confirmed"] for index, zone in enumerate(self.zones)]
        self.verify_all.blockSignals(True)
        self.verify_all.setEnabled(bool(verified))
        self.verify_all.setChecked(bool(verified) and all(verified))
        self.verify_all.blockSignals(False)

    def _verify_all_zones(self, checked):
        try:
            self.capture()
            candidate = copy.deepcopy(self.zones)
            if checked:
                for zone in candidate:
                    zone["confirmed"] = True
                    zone["topology_signature"] = topology_signature(self.netlist)
                    zone["configuration_signature"] = configuration_signature(zone)
                report = evaluate_zones(self.netlist, candidate)
                eligible = {zone["id"] for zone in report["zones"] if zone["complete"]}
                demo_ids = {zone["id"] for zone in candidate} if self.netlist.get("demonstration_mode") else set()
                eligible.update(demo_ids)
                skipped = [zone for zone in report["zones"] if zone["id"] not in eligible]
            else:
                eligible, skipped = set(), []
        except (ValueError, TypeError) as error:
            self.verify_all.blockSignals(True)
            self.verify_all.setChecked(False)
            self.verify_all.blockSignals(False)
            QMessageBox.warning(self, messages.ZONE_DIALOG_INCOMPLETE_ZONE_SETTINGS, str(error))
            return
        for zone in self.zones:
            zone["confirmed"] = zone["id"] in eligible
            if self.netlist.get("demonstration_mode"):
                zone["demonstration_complete"] = bool(checked)
        self.confirmed.blockSignals(True)
        self.confirmed.setChecked(self.zones[self.active]["confirmed"]
                                  if 0 <= self.active < len(self.zones) else False)
        self.confirmed.blockSignals(False)
        self._sync_verify_all()
        self.verification_summary.setText(
            f"Verified {len(eligible)} of {len(self.zones)} zones. "
            "Incomplete zones remain drafts and can still be saved. "
            "Hover here for details."
            if skipped else ""
        )
        self.verification_summary.setToolTip("\n\n".join(
            zone["name"] + ": " + "; ".join(zone["warnings"]) for zone in skipped))

    def _change_demo_mode(self, enabled):
        self.netlist["demonstration_mode"] = enabled
        self._verify_all_zones(False)

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
            self.zones[self.active]["candidate_buses"] = [
                ident for ident in region["core"]
                if ident in self.components and kind(self.components[ident]) == "bus"
            ]
            proposal = next(
                (
                    candidate for candidate in propose_zones(self.netlist)
                    if candidate.get("proposal_region") == region["signature"]
                ),
                None,
            )
            if proposal is not None:
                self.zones[self.active]["candidate_breakers"] = list(
                    proposal["candidate_breakers"]
                )
            else:
                self.zones[self.active].pop("candidate_breakers", None)
            eligible = [i for i in region["core"] if i in self.components
                        and kind(self.components[i]) not in ("ct", "pt", "ground", "junction", "surge_arrester", "incoming_line")]
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
        actions = [row.to_action(self.components, self.zones) for row in self.load_rows]
        self.zones[self.active].update(name=self.name.text().strip(), kind=self.zone_kind.currentText(),
            seed=self.seed.currentData() or "", confirmed=self.confirmed.isChecked(), actions=actions,
            boundary_cts=[self.cts.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.cts.count())
                          if self.cts.item(i).checkState() == Qt.CheckState.Checked])
        self.zone_list.item(self.active).setText(self.zones[self.active]["name"] or "Unnamed Zone")
        self._sync_verify_all()

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
            self.load_rows.clear()
            self.confirmed.setChecked(False)
            self.loading = False
            self._sync_verify_all()

    def select_zone(self, index):
        self.capture()
        self._load_selection_anchor = None
        self.active = index
        if index < 0:
            return
        self.loading = True
        zone = self.zones[index]
        self.name.setText(zone["name"])
        region_signature = zone.get("topology_region") or zone.get("proposal_region")
        match = next((r for r in self.detected_regions
                      if r["signature"] == region_signature), None)
        self.region_combo.setCurrentIndex(self.region_combo.findData(match["id"]) if match else 0)
        candidates = [
            f"{self.components[ident].get('label') or ident} [{ident}]"
            for ident in zone.get("candidate_breakers", [])
            if ident in self.components
        ]
        notes = []
        buses = [
            f"{self.components[ident].get('label') or ident} [{ident}]"
            for ident in zone.get("candidate_buses", [])
            if ident in self.components
        ]
        if not buses:
            selected_region = next(
                (region for region in self.detected_regions
                 if region["id"] == self.region_combo.currentData()),
                None,
            )
            if selected_region is not None:
                buses = [
                    f"{self.components[ident].get('label') or ident} [{ident}]"
                    for ident in selected_region["core"]
                    if ident in self.components and kind(self.components[ident]) == "bus"
                ]
        if zone["kind"] == "Bus" and len(buses) > 1:
            notes.append(
                "Review required: multiple buses share this detected region "
                f"({', '.join(buses)}). Topology alone does not determine whether "
                "they share a protection zone; configure separate fault events if needed."
            )
        if candidates:
            notes.append(
                "Suggested nearby breakers (not assigned): " + ", ".join(candidates)
                + ". Add only the operating loads verified for this protection scheme."
            )
        self.guidance.setText("\n".join(notes) if notes else self._base_guidance)
        self.zone_kind.setCurrentText(zone["kind"])
        if zone["seed"] and self.seed.findData(zone["seed"]) < 0:
            self.seed.addItem(messages.ZONE_DIALOG_MISSING.format(zone['seed']), zone["seed"])
        self.seed.setCurrentIndex(self.seed.findData(zone["seed"]))
        existing = {self.cts.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.cts.count())}
        for ident in zone["boundary_cts"]:
            if ident not in existing:
                entry = QListWidgetItem(messages.ZONE_DIALOG_MISSING_CT.format(ident))
                entry.setData(Qt.ItemDataRole.UserRole, ident)
                entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                entry.setCheckState(Qt.CheckState.Unchecked)
                self.cts.addItem(entry)
        for i in range(self.cts.count()):
            entry = self.cts.item(i)
            entry.setCheckState(Qt.CheckState.Checked if entry.data(Qt.ItemDataRole.UserRole) in zone["boundary_cts"] else Qt.CheckState.Unchecked)
        self.actions.setRowCount(0)
        self.load_rows.clear()
        for action in zone["actions"]:
            self.add_action(action)
        self.confirmed.setChecked(zone["confirmed"])
        self.loading = False
        self._sync_verify_all()

    def add_action(self, action=None):
        if self.active < 0:
            return
        action = action or {"component": "", "load": "trip_coil_1_a", "stage": "Primary trip", "operation": "all_poles"}
        row = self.actions.rowCount()
        self.actions.insertRow(row)
        equipment, load, stage = QComboBox(), QComboBox(), QComboBox()
        equipment.addItem("Select equipment", "")
        other_id = action["component"] if action["component"].startswith("other:") else "other:" + uuid.uuid4().hex
        equipment.addItem("Other", other_id)
        for ident, component in self.components.items():
            if kind(component) not in ("ct", "pt", "ground", "junction", "bus", "surge_arrester", "incoming_line", "line_trap", "xfmr_2w", "xfmr_3w"):
                equipment.addItem(f"{component.get('label', ident)} [{ident}]", ident)
        if equipment.findData(action["component"]) < 0:
            equipment.addItem(messages.ZONE_DIALOG_MISSING.format(action['component']), action["component"])
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
            operation.addItem("Legacy units \u2014 review", "legacy")
        operation.setCurrentIndex(operation.findData(selected))
        multiplier = QLineEdit()
        multiplier.setReadOnly(True)
        other_amps = QDoubleSpinBox()
        other_amps.setRange(0, 1000000)
        other_amps.setDecimals(3)
        other_amps.setSuffix(" A")
        other_amps.setValue(action.get("custom_load", {}).get("amps", 0))
        self.actions.setCellWidget(row, 0, equipment)
        editors = (equipment, load, stage, supply, operation, multiplier, other_amps)
        for editor in editors:
            editor.installEventFilter(self)
            for child in editor.findChildren(QLineEdit):
                child.installEventFilter(self)
        row_model = OperatingLoadRow(*editors, copy.deepcopy(action))
        self.load_rows.append(row_model)
        cells = []
        for col, original, alternate in ((1, load, other_amps), (2, stage, QWidget()),
                                         (3, supply, QWidget()), (4, operation, QWidget()),
                                         (5, multiplier, QWidget())):
            cell = QStackedWidget()
            cell.addWidget(original)
            cell.addWidget(alternate)
            self.actions.setCellWidget(row, col, cell)
            cells.append(cell)
        def update_row(*_):
            self._update_action_preview(equipment, load, operation, supply, multiplier)
            other = equipment.currentData().startswith("other:")
            for cell in cells:
                cell.setCurrentIndex(1 if other else 0)
            self.unconfirm()
        for combo in (equipment, load, operation):
            combo.currentIndexChanged.connect(update_row)
        stage.currentTextChanged.connect(self.unconfirm)
        supply.textEdited.connect(self.unconfirm)
        other_amps.valueChanged.connect(self.unconfirm)
        update_row()
        self.unconfirm()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress and hasattr(self, "actions"):
            header = self.actions.verticalHeader()
            if watched is header.viewport():
                row = header.logicalIndexAt(event.position().toPoint())
                if row >= 0 and event.button() == Qt.MouseButton.LeftButton:
                    self._select_load_rows(row, event.modifiers())
                    return True
            index = self.actions.indexAt(watched.mapTo(self.actions.viewport(), QPoint(0, 0)))
            if index.isValid():
                flags = QItemSelectionModel.SelectionFlag.NoUpdate
                if event.type() == QEvent.Type.MouseButtonPress:
                    if event.button() == Qt.MouseButton.LeftButton and event.modifiers() & (
                            Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
                        self._select_load_rows(index.row(), event.modifiers())
                        return True
                    self._load_selection_anchor = index.row()
                    flags = QItemSelectionModel.SelectionFlag.ClearAndSelect
                self.actions.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
                self.actions.selectionModel().select(index, flags | QItemSelectionModel.SelectionFlag.Rows)
        return super().eventFilter(watched, event)

    def _select_load_rows(self, row, modifiers):
        selection = self.actions.selectionModel().selection()
        self.actions.setFocus()
        self.actions.selectionModel().setCurrentIndex(
            self.actions.model().index(row, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
        self.actions.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            anchor = self._load_selection_anchor
            if anchor is None or anchor >= self.actions.rowCount():
                anchor = row
                self._load_selection_anchor = row
            if not modifiers & Qt.KeyboardModifier.ControlModifier:
                self.actions.clearSelection()
            self.actions.setRangeSelected(QTableWidgetSelectionRange(
                min(row, anchor), 0, max(row, anchor), self.actions.columnCount() - 1), True)
        else:
            index = self.actions.model().index(row, 0)
            flags = (QItemSelectionModel.SelectionFlag.Toggle
                     if modifiers & Qt.KeyboardModifier.ControlModifier
                     else QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.actions.selectionModel().select(index, flags | QItemSelectionModel.SelectionFlag.Rows)
            self._load_selection_anchor = row

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
                    raise ValueError(messages.ZONE_DIALOG_REVIEW_THE_LEGACY_MULTIPLIER_AND_SELECT_WHOLE_DEVICE)
                units = 1
            multiplier.setText(f"\u00d7{units}")
            multiplier.setToolTip("Calculated from the equipment current basis and operating action")
        except ValueError as error:
            multiplier.setText("Review")
            multiplier.setToolTip(str(error))

    def remove_action(self):
        self._confirm_delete_loads(self._selected_load_rows())

    def _selected_load_rows(self):
        return {index.row() for index in self.actions.selectionModel().selectedRows()}

    def _sync_delete_load_button(self, *_):
        selected = self._selected_load_rows()
        count = len(selected)
        self.delete_loads_button.setEnabled(count > 0)
        self.delete_loads_button.setText(f"Delete Selected Loads ({count})" if count else "Delete Selected Loads")
        header = self.actions.verticalHeader()
        header.selected_rows = selected
        header.viewport().update()
        for row in range(self.actions.rowCount()):
            for column in range(6):
                cell = self.actions.cellWidget(row, column)
                if cell is None:
                    continue
                for widget in [cell, *cell.findChildren(QWidget)]:
                    active = row in selected
                    if widget.property("loadSelected") != active:
                        widget.setProperty("loadSelected", active)
                        widget.style().unpolish(widget)
                        widget.style().polish(widget)
                        widget.update()

    def _confirm_delete_loads(self, rows):
        if not rows:
            return
        prompt = (messages.ZONE_DIALOG_DELETE_ONE_LOAD.format(next(iter(rows)) + 1)
                  if len(rows) == 1 else messages.ZONE_DIALOG_DELETE_MULTIPLE_LOADS.format(len(rows)))
        answer = QMessageBox.question(
            self, messages.ZONE_DIALOG_DELETE_LOADS_TITLE, prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        for row in sorted(rows, reverse=True):
            self.actions.removeRow(row)
            self.load_rows.pop(row)
        self._load_selection_anchor = None
        self._sync_delete_load_button()
        self.unconfirm()

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
            errors = [messages.DIAGNOSTIC_WITH_CONTEXT.format(row['name'], message) for row in report["zones"]
                      if row["id"] in confirmed_ids and not row.get("demonstration_complete")
                      for message in row["warnings"]]
            if errors:
                raise ValueError("\n".join(errors) + messages.ZONE_DIALOG_UNCHECK_CONFIRMATION_TO_SAVE_THESE_SETTINGS_AS_A_DRAFT)
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, messages.ZONE_DIALOG_INCOMPLETE_ZONE_SETTINGS, str(error))
            return
        self.zones = candidate
        super().accept()
