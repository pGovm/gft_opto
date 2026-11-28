"""Protection study evaluation and results presentation."""
import copy
import time

from PySide6.QtWidgets import QDialog, QPushButton, QTextEdit, QVBoxLayout
from gft_opto.gui import messages
from gft_opto.logic.protection_study import evaluate_zones, propose_zones

STUDY_TITLE = "Momentary DC protection-zone study: "

class EvaluationViewMixin:
    def _invalidate_results(self):
        self._clear_zone_highlights()
        self._zone_report = None
        self.zone_view.blockSignals(True)
        self.zone_view.clear()
        self.zone_view.addItem("Run Evaluation to display zones")
        self.zone_view.blockSignals(False)
        if self.last_result is not None:
            self.output_box.append("\nResults are outdated. Run Evaluation again before exporting.")
        self.last_result = self.last_netlist = None
        self.export_results_action.setEnabled(False)
        self.save_annotated_one_line_pdf_action.setEnabled(False)


    def _expand_zone_analysis(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Momentary Load Analysis")
        dialog.resize(950, 700)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self.output_box.toPlainText() or "Run Evaluation to generate a protection-zone study.")
        layout.addWidget(text)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()


    def _retitle_results(self, project):
        """Show a new project name on results that a rename does not change."""
        self.last_result["scenario_name"] = project
        if self.last_netlist is not None:
            self.last_netlist["system_name"] = project
        self._evaluated_snapshot["project"] = project
        lines = self.output_box.toPlainText().split("\n")
        if lines and lines[0].startswith(STUDY_TITLE):
            lines[0] = STUDY_TITLE + project
            self.output_box.setPlainText("\n".join(lines))

    def _on_run_evaluation_clicked(self):
        """Propose zones, evaluate explicit actions, and display complete or draft results."""
        started = time.perf_counter()
        self._invalidate_results()
        try:
            netlist = self._protection_netlist()
            if not netlist["components"]:
                self.output_box.setPlainText(messages.SUBSTATION_GUI4_NO_COMPONENTS_TO_EVALUATE_REBUILD_THE_ONE_LINE_AND)
                self.footer_status_label.setText(messages.SUBSTATION_GUI4_PLACE_COMPONENTS_BEFORE_EVALUATION)
                return
            if not self.protection_zones:
                self.protection_zones = propose_zones(netlist)
                self._mark_modified()
            report = evaluate_zones(netlist, self.protection_zones)
            # The catalog uses original GUI objects, including CT-only conductors.
            # Editable operating settings remain separate JSON dictionaries.
            report["topology_regions"] = self._scene_topology()
            self._zone_report = report
            self.zone_view.blockSignals(True)
            self.zone_view.clear()
            self.zone_view.addItem(messages.SUBSTATION_GUI4_ALL_CONFIGURED_ZONES_DASHED_INCOMPLETE, None)
            for index, zone in enumerate(report["zones"]):
                self.zone_view.addItem(f"{index + 1}. {zone['name']} — {'confirmed' if zone['complete'] else messages.SUBSTATION_GUI4_REVIEW_REQUIRED}", zone["id"])
            self.zone_view.addItem("All detected regions (topology only)", ("topology", None))
            for region in report["topology_regions"]:
                enclosure = " [enclosed]" if region["enclosed_by"] else " [outermost]"
                self.zone_view.addItem(region["name"] + enclosure, ("topology", region["id"]))
            if not report["zones"]:
                self.zone_view.setCurrentIndex(1)
            self.zone_view.blockSignals(False)
            self._show_zone_highlights()
            lines = [STUDY_TITLE + netlist["system_name"],
                "COMPLETE" if report["complete"] else messages.SUBSTATION_GUI4_INCOMPLETE_CONFIGURE_REVIEW_FLAGGED_ZONES_PARTIAL_TOTALS_ARE_NOT,
                "Each configured zone is a separate fault event. Same-stage loads are simultaneous; other stages are separate.",
                "DC supplies are reported separately. No battery amp-hour calculation.", ""]
            all_loads, warnings = [], list(report["warnings"])
            lines.extend(messages.SUBSTATION_GUI4_REVIEW_WARNING.format(warning) for warning in warnings)
            for zone in report["zones"]:
                members = ", ".join(zone.get("members", [])) or "No members"
                lines.extend([
                    f"{zone['name']} ({members}) ({zone['kind']})",
                    "  CT boundaries: " + ", ".join(zone["boundaries"]),
                    "  Equipment: " + members,
                ])
                for warning in zone["warnings"]:
                    lines.append(messages.SUBSTATION_GUI4_INDENTED_REVIEW_WARNING.format(warning))
                    warnings.append(messages.DIAGNOSTIC_WITH_CONTEXT.format(zone['name'], warning))
                for scenario in zone["scenarios"]:
                    prefix = "" if zone["complete"] else messages.SUBSTATION_GUI4_PROVISIONAL
                    lines.append(f"  {prefix}{scenario['stage']}: {scenario['peak_current_A']:g} A across assigned supplies")
                    for advice in scenario.get("advisories", []):
                        lines.append(f"    NOTE: {advice}")
                    for supply, current in scenario["supplies"].items():
                        lines.append(f"    {supply}: {current:g} A")
                    for load in scenario["loads"]:
                        lines.append(f"    {load['name']}: {load['amps_per_unit']:g} A × {load['quantity']} = {load['total_amps']:g} A ({load['supply']})")
                    all_loads.extend(
                        {
                            **load,
                            "zone_id": zone["id"],
                            "zone_name": zone["name"],
                        }
                        for load in scenario["loads"]
                    )
                lines.append("")
            lines.append("Detected topology regions (not automatically added to the calculation):")
            for region in report["topology_regions"]:
                relation = "enclosed" if region["enclosed_by"] else "outermost"
                lines.append(f"  {region['name']} ({relation}; {len(region['connections'])} conductors)")
            lines.append("")
            lines.append("Peak per supply among confirmed, complete configured zones:")
            for supply, peak in report["supply_peaks"].items():
                lines.append(f"  {supply}: {peak['peak_current_A']:g} A — {peak['zone']} / {peak['stage']}")
            if not report["supply_peaks"]:
                lines.append(messages.SUBSTATION_GUI4_NOT_AVAILABLE_UNTIL_ZONE_ASSIGNMENTS_AND_CURRENTS_ARE_COMPLETE)
            worst_supply = max(report["supply_peaks"],
                key=lambda supply: report["supply_peaks"][supply]["peak_current_A"], default=None)
            self.last_result = {"scenario_name": netlist["system_name"], "voltage_V": netlist["voltage_V"],
                "peak_current_A": report["supply_peaks"][worst_supply]["peak_current_A"] if worst_supply and report["complete"] else messages.SUBSTATION_GUI4_INCOMPLETE_STUDY,
                "worst_case_supply": worst_supply,
                "loads": all_loads, "warnings": warnings, "protection_analysis": report,
                "calculation_steps": ["Group actions by zone and operating stage.",
                    "Count each physical coil or motor once per stage; multiply entered current by the verified unit count.",
                    "Sum currents per DC supply within each stage, then take each supply's maximum across complete zones.",
                    messages.SUBSTATION_GUI4_STAGES_AND_SEPARATE_ZONE_FAULTS_ARE_NOT_ADDED_TOGETHER]}
            netlist["zones"] = copy.deepcopy(self.protection_zones)
            self.last_netlist = netlist
            self._evaluated_snapshot = self._layout_data()
            self.export_results_action.setEnabled(True)
            self.save_annotated_one_line_pdf_action.setEnabled(True)
            self.output_box.setPlainText("\n".join(lines))
            self.footer_status_label.setText("Zone evaluation complete" if report["complete"] else messages.SUBSTATION_GUI4_ZONE_EVALUATION_NEEDS_REVIEW_CONFIGURE_PROTECTION_ZONES)
        except Exception as error:
            self._invalidate_results()
            self.output_box.setPlainText(messages.SUBSTATION_GUI4_EVALUATION_FAILED.format(error))
            self.footer_status_label.setText(messages.SUBSTATION_GUI4_ZONE_EVALUATION_FAILED_SEE_ANALYSIS_OUTPUT)
        self.response_time_label.setText(f"Response time: {(time.perf_counter() - started) * 1000:.0f} ms")
