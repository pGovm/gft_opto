"""Project layout persistence and netlist import workflows."""
import copy
import functools
import json
import pymupdf
from gft_opto.logic.protection_study import is_transfer_bus

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QListWidgetItem, QMessageBox
from gft_opto.gui import messages
from gft_opto.gui.layout_io import (
    build_scene, library_from_layout, reserve_ids, scene_data, write_layout,
)
from gft_opto.gui.routing import (
    DEFAULT_GRID_OPACITY, DEFAULT_WORKSPACE_HEIGHT, DEFAULT_WORKSPACE_WIDTH,
)
from gft_opto.gui.symbols import BUILTIN_SYMBOLS, EQUIPMENT_DEFS, make_user_equipment
from gft_opto.logic.netlist_manager import load_netlist_json
from pathlib import Path


class ProjectFilesMixin:
    @staticmethod
    def _document_content(data):
        """View preferences are saved on request, but do not dirty a project."""
        if data is None:
            return None
        return {key: value for key, value in data.items() if key != "settings"}

    @staticmethod
    def _evaluation_content(data):
        """Only drawing and study edits can make calculated results outdated."""
        return {key: value for key, value in data.items()
                if key not in {"settings", "pdf", "pdf_bounds", "scene_rect", "library"}}

    def _layout_data(self):
        rect = self.workspace_scene.sceneRect()
        background = self.workspace_view._background_item
        bounds = background.sceneBoundingRect() if background is not None else None
        pdf_bounds = ([bounds.x(), bounds.y(), bounds.width(), bounds.height()]
                      if bounds is not None else getattr(self, "_pdf_bounds", None))
        return {
            "version": 1, "project": self.project_box.text().strip() or "Untitled Project",
            "scene_rect": [rect.x(), rect.y(), rect.width(), rect.height()],
            "pdf": self.pdf_path,
            "pdf_bounds": pdf_bounds,
            "zones": copy.deepcopy(self.protection_zones),
            "library": {key: meta["definition"] for key, meta in EQUIPMENT_DEFS.items()},
            "settings": {"grid_visible": self.grid_show_cb.isChecked(),
                         "grid_opacity": self.grid_opacity_slider.value(),
                         "snap": self.grid_snap_cb.isChecked(),
                         "pdf_visible": self.pdf_show_cb.isChecked()},
            **scene_data(self.workspace_scene),
        }


    def _confirm_discard(self):
        if self._document_content(self._layout_data()) == self._document_content(self._saved_snapshot):
            return True
        answer = QMessageBox.question(
            self, "Unsaved Layout", "Save changes to this layout?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_layout()
        return answer == QMessageBox.StandardButton.Discard


    def save_layout(self, path=None, save_as=False):
        path = path or (None if save_as else self.layout_path)
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save Layout", "layout.json", "Layout Files (*.json)")
        if not path:
            return False
        if not str(path).lower().endswith(".json"):
            path = str(path) + ".json"
        try:
            data = self._layout_data()
            # Validate the same representation that Open Layout will accept.
            checked = build_scene(data)
            checked.clear()
            write_layout(path, data)
        except Exception as error:
            QMessageBox.critical(self, messages.SUBSTATION_GUI4_SAVE_FAILED, messages.SUBSTATION_GUI4_COULD_NOT_SAVE_THE_LAYOUT.format(error))
            return False
        self.layout_path = str(Path(path).resolve())
        self._saved_snapshot = data
        self._mark_modified()
        self.footer_status_label.setText(f"Layout saved: {self.layout_path}")
        return True


    def new_layout(self):
        if not self._confirm_discard():
            return
        data = {"version": 1, "project": "Untitled Project", "pdf": None,
                "scene_rect": [0, 0, DEFAULT_WORKSPACE_WIDTH, DEFAULT_WORKSPACE_HEIGHT],
                "components": [], "connections": [], "library": {},
                "settings": {"grid_visible": True, "grid_opacity": round(DEFAULT_GRID_OPACITY * 100),
                             "snap": True, "pdf_visible": True}}
        self._install_layout(data, build_scene(data), None, None)


    def open_layout(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open Layout", "", "Layout Files (*.json)")
        if not path:
            return False
        # Build the entire replacement before discarding any current work.
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            scene = build_scene(data)
            pdf = data.get("pdf")
            normalized_pdf = str((Path(path).resolve().parent / pdf).resolve()) if pdf else pdf
            pdf = normalized_pdf
            pixmap = None
            if pdf:
                try:
                    pixmap = self._read_pdf_pixmap(pdf)
                except Exception:
                    replacement, _ = QFileDialog.getOpenFileName(
                        self, messages.SUBSTATION_GUI4_LOCATE_MISSING_OR_UNREADABLE_BACKGROUND_PDF_CANCEL_TO_OPEN, "", "PDF Files (*.pdf)"
                    )
                    if replacement:
                        pdf = str(Path(replacement).resolve())
                        pixmap = self._read_pdf_pixmap(pdf)
            if not self._confirm_discard():
                return False
            # Normalize the original reference before taking the saved snapshot.
            # Selecting a different replacement PDF remains an unsaved edit.
            self._install_layout(dict(data, pdf=normalized_pdf), scene, str(Path(path).resolve()), pixmap)
            self.pdf_path = pdf
            self._mark_modified()
            self.footer_status_label.setText(
                "Layout opened; background PDF unavailable" if pdf and pixmap is None else f"Layout opened: {path}"
            )
            return True
        except Exception as error:
            QMessageBox.critical(self, messages.SUBSTATION_GUI4_OPEN_FAILED, messages.SUBSTATION_GUI4_COULD_NOT_OPEN_THE_LAYOUT.format(error))
            return False


    def import_netlist_json(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Import Netlist JSON", "", "Netlist JSON Files (*.json)"
            )
        if not path:
            return False

        try:
            netlist = load_netlist_json(path)
            layout_data = self._layout_data_from_netlist(netlist)
            staged_scene = build_scene(layout_data)
        except (
            OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError
        ) as error:
            QMessageBox.critical(
                self, "Netlist Import Failed", f"Could not import the netlist:\n{error}"
            )
            return False

        answer = QMessageBox.question(
            self,
            "Replace Current Drawing",
            "Importing this netlist replaces the current drawing and removes its "
            "background PDF, protection zones, and saved layout settings. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes or not self._confirm_discard():
            staged_scene.clear()
            return False

        self._install_layout(layout_data, staged_scene, None, None)
        self._saved_snapshot = None
        self._mark_modified()
        self.footer_status_label.setText(
            f"Netlist imported: {layout_data['project']}"
        )
        return True


    def _layout_data_from_netlist(self, netlist):
        project_name = netlist.get("project_name", "Untitled Project")
        components = []
        for index, component in enumerate(netlist["components"], start=1):
            component_type = component["type"]
            properties = component["properties"]
            if component_type in BUILTIN_SYMBOLS or component_type == "junction":
                symbol = component_type
            else:
                declared_symbol = properties.get("symbol_type")
                if declared_symbol in (*BUILTIN_SYMBOLS, "custom"):
                    symbol = declared_symbol
                elif component_type.startswith("custom_"):
                    symbol = "custom"
                else:
                    raise ValueError(
                        f"Component {component['id']!r} has unsupported type "
                        f"{component_type!r}."
                    )

            ports = component.get("port_positions") or {}
            if set(ports) != set(component["ports"]):
                raise ValueError(
                    f"Component {component['id']!r} must include a position "
                    "for every port to be imported."
                )
            name = component.get("name") or component_type
            display_id = component.get("display_id") or component["id"]
            if not isinstance(name, str) or not isinstance(display_id, str):
                raise ValueError(
                    f"Component {index} must have a valid name and display ID."
                )

            components.append({
                "id": component["id"],
                "symbol": symbol,
                "name": name,
                "display_id": display_id,
                "properties": properties,
                "position": [
                    component["position"]["x"],
                    component["position"]["y"],
                ],
                "scale": component["scale_factor"],
                "rotation": component["rotation_deg"],
                "ports": {
                    port: [point["x"], point["y"]]
                    for port, point in ports.items()
                },
            })

        connections = [
            [
                connection["from"]["component"],
                connection["from"]["port"],
                connection["to"]["component"],
                connection["to"]["port"],
            ]
            for connection in netlist["connections"]
        ]
        return {
            "version": 1,
            "project": project_name,
            "pdf": None,
            "scene_rect": [
                0, 0, DEFAULT_WORKSPACE_WIDTH, DEFAULT_WORKSPACE_HEIGHT
            ],
            "components": components,
            "connections": connections,
            "zones": [],
            "library": {},
            "settings": {
                "grid_visible": True,
                "grid_opacity": round(DEFAULT_GRID_OPACITY * 100),
                "snap": True,
                "pdf_visible": True,
            },
        }


    @staticmethod
    def _read_pdf_pixmap(path):
        with pymupdf.open(path) as document:
            pix = document.load_page(0).get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            image = QPixmap()
            if not image.loadFromData(pix.tobytes("png")):
                raise ValueError(messages.SUBSTATION_GUI4_COULD_NOT_DECODE_PDF_BACKGROUND)
            return image


    def _install_layout(self, data, staged_scene, path, pixmap):
        self._loading = True
        self._clear_zone_highlights()
        self._zone_report = None
        self.protection_zones = copy.deepcopy(data.get("zones", []))
        records = {component["id"]: component for component in data["components"]}
        self.protection_zones = [zone for zone in self.protection_zones
            if not (zone["kind"] == "Bus" and is_transfer_bus(records.get(zone["seed"], {})))]
        for zone in self.protection_zones:
            zone.pop("demonstration_complete", None)
            zone["name"] = zone["name"].removeprefix("DEMO - ")
        view = self.workspace_view
        view._cancel_pending()
        view._clear_drop_preview()
        view._clear_snap_ports()
        view._move_origins = {}
        view._resize_item = None
        view._rotate_item = None
        view._bus_resize_item = None
        self.undo_stack.clear()
        old_scene = self.workspace_scene
        old_scene.selectionChanged.disconnect(self._on_selection_changed)
        staged_scene.setParent(self)
        self.workspace_scene = staged_scene
        view.setScene(staged_scene)
        view._background_item = None
        view._grid_item = None
        view._init_grid()
        staged_scene.selectionChanged.connect(self._on_selection_changed)
        staged_scene.changed.connect(self._sync_circuit_visibility)
        self._sync_circuit_visibility()
        old_scene.deleteLater()
        self._workspace_original = pixmap
        self._pdf_bounds = data.get("pdf_bounds")
        if pixmap is not None:
            view.set_background_pixmap(pixmap, scene_width=data["scene_rect"][2], scene_height=data["scene_rect"][3],
                fit_existing_sheet=True, scene_bounds=QRectF(*self._pdf_bounds) if self._pdf_bounds else None)
            staged_scene.setSceneRect(QRectF(*data["scene_rect"]))
            view._sync_grid_to_scene()
        self.pdf_path = data.get("pdf")
        self.layout_path = path
        self.project_box.setText(data["project"])
        EQUIPMENT_DEFS.clear()
        self.equipment_list.clear()
        self.equipment_search.clear()
        for key, definition in library_from_layout(data).items():
            EQUIPMENT_DEFS[key] = {"label": definition["name"], "definition": definition,
                "factory": functools.partial(make_user_equipment, definition["symbol"], definition["name"], definition["properties"])}
            entry = QListWidgetItem(definition["name"])
            entry.setData(Qt.ItemDataRole.UserRole, key)
            self.equipment_list.addItem(entry)
        settings = data["settings"]
        self.grid_show_cb.setChecked(settings["grid_visible"])
        self.grid_snap_cb.setChecked(settings["snap"])
        self.pdf_show_cb.setChecked(settings["pdf_visible"])
        self.pdf_show_cb.setEnabled(pixmap is not None)
        self.grid_opacity_slider.setValue(settings["grid_opacity"])
        view.set_grid_visible(settings["grid_visible"])
        view.set_snap_to_grid(settings["snap"])
        view.set_pdf_visible(settings["pdf_visible"])
        view.set_grid_opacity(settings["grid_opacity"])
        reserve_ids(staged_scene)
        self.last_result = self.last_netlist = None
        self.output_box.clear()
        self.export_results_action.setEnabled(False)
        self.save_annotated_one_line_pdf_action.setEnabled(False)
        self.zone_view.blockSignals(True)
        self.zone_view.clear()
        self.zone_view.addItem("Run Evaluation to display zones")
        self.zone_view.blockSignals(False)
        self._on_selection_changed()
        self._saved_snapshot = self._layout_data()
        self._loading = False
        self._mark_modified()
        view.fitInView(staged_scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()
