"""Calculation and netlist PDF export workflows."""
import os
import tempfile
from PySide6.QtCore import QRectF
from PySide6.QtGui import QPageLayout, QPainter, QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QFileDialog, QMessageBox
from gft_opto.gui import messages
from gft_opto.gui.symbols import (
    BUILTIN_SYMBOLS, ConnectionItem, OneLineSymbolItem, scene_wire_index,
)
from html import escape
from pathlib import Path

# Netlist PDF: readable property names (units in parentheses) and setting values.
PROPERTY_LABELS = {
    "symbol_type": "Symbol", "status": "Status", "rating_kv": "Rating (kV)",
    "trip_coil_1_a": "Trip Coil 1 (A)", "trip_coil_2_a": "Trip Coil 2 (A)", "close_coil_a": "Close Coil (A)",
    "motor_inrush_a": "Motor Inrush (A)", "motor_run_a": "Motor Run (A)",
    "coil_current_basis": "Coil current basis", "mechanism_arrangement": "Mechanisms", "pole_count": "Poles",
    "trip_coil_1_supply": "Trip Coil 1 supply", "trip_coil_2_supply": "Trip Coil 2 supply",
    "close_coil_supply": "Close Coil supply", "motor_supply": "Motor supply",
}
SETTING_VALUES = {
    "whole_breaker": "Whole breaker", "per_mechanism": "Per mechanism",
    "shared": "Shared mechanism", "independent": "Independent per pole",
}
UNIT_SUFFIXES = (("_kv", "kV"), ("_a", "A"))


def property_label(key):
    if key in PROPERTY_LABELS:
        return PROPERTY_LABELS[key]
    text, unit = str(key), ""
    for suffix, name in UNIT_SUFFIXES:
        if text.endswith(suffix):
            text, unit = text[: -len(suffix)], f" ({name})"
            break
    text = text.replace("_", " ").strip()
    return (text[:1].upper() + text[1:] + unit) if text else str(key)


def type_label(component_type):
    """Full equipment name, as in the app's equipment library (e.g. mos -> Motor Operated Switch (MOS))."""
    if component_type in BUILTIN_SYMBOLS:
        return BUILTIN_SYMBOLS[component_type]["label"]
    if str(component_type).startswith("custom"):
        return "Custom"
    text = str(component_type).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def property_value(key, value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    if key == "symbol_type":
        return BUILTIN_SYMBOLS.get(value, {}).get("label", "Custom" if value == "custom" else str(value))
    return SETTING_VALUES.get(value, str(value))


class PdfExportMixin:
    def _annotated_drawing_rect(self):
        """Area covered by the equipment, wires and zone outlines, with a small margin.

        Fitting the page to this, rather than to the whole drawing sheet, keeps a
        small one-line from shrinking into a corner of the page.
        """
        overlays = set(map(id, getattr(self, "_zone_overlays", [])))
        rect = QRectF()
        for item in self.workspace_scene.items():
            if isinstance(item, (OneLineSymbolItem, ConnectionItem)) or id(item) in overlays:
                rect = rect.united(item.sceneBoundingRect())
        if rect.isEmpty():
            return rect
        margin = max(rect.width(), rect.height()) * 0.02
        return rect.adjusted(-margin, -margin, margin, margin)

    def _on_save_annotated_one_line_pdf(self):
        scene = self.workspace_scene
        rect = self._annotated_drawing_rect()
        if rect.isEmpty():
            QMessageBox.information(
                self, "Save Annotated One-Line PDF", "The one-line diagram is empty."
            )
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Annotated One-Line PDF",
            "annotated_one_line.pdf",
            "PDF Files (*.pdf)",
        )
        if not file_path:
            return
        if not Path(file_path).suffix:
            file_path += ".pdf"

        visibility = [(item, item.isVisible()) for item in scene.items()]
        grid_item = getattr(getattr(self, "workspace_view", None), "_grid_item", None)
        if grid_item is not None:
            grid_item.setVisible(False)
        for item in scene.items():
            if item is not grid_item:
                item.setVisible(True)
        label_states = [
            (item, item.show_name_label, item._labels_as_paths)
            for item in scene.items()
            if isinstance(item, OneLineSymbolItem)
        ]
        # Keep equipment IDs on the diagram, drawn as outlines so the PDF embeds
        # no fonts. Symbols that never show a name (ground, junction) are unchanged.
        wire_index = scene_wire_index(scene)
        for item, _, _ in label_states:
            item._labels_as_paths = True
            item.refresh_label_placement(wire_index)

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=Path(file_path).parent, suffix=".pdf", delete=False
            ) as stream:
                temporary = stream.name

            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(temporary)
            # Match the page to the drawing's shape so it fills as much as possible.
            printer.setPageOrientation(
                QPageLayout.Orientation.Landscape if rect.width() >= rect.height()
                else QPageLayout.Orientation.Portrait
            )

            painter = QPainter(printer)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            page_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            margin = int(min(page_rect.width(), page_rect.height()) * 0.035)
            area = QRectF(page_rect.adjusted(margin, margin, -margin, -margin))
            # Scale the drawing to fit and center it on the page.
            scale = min(area.width() / rect.width(), area.height() / rect.height())
            target = QRectF(0, 0, rect.width() * scale, rect.height() * scale)
            target.moveCenter(area.center())
            scene.render(painter, target, rect)
            painter.end()

            if printer.printerState() == QPrinter.PrinterState.Error or Path(temporary).stat().st_size == 0:
                raise OSError(messages.SUBSTATION_GUI4_THE_PDF_FILE_WAS_NOT_CREATED_OR_IS_EMPTY)
            os.replace(temporary, file_path)
        except OSError as error:
            QMessageBox.critical(
                self, messages.SUBSTATION_GUI4_SAVE_FAILED, messages.SUBSTATION_GUI4_COULD_NOT_CREATE_THE_PDF.format(error)
            )
            return
        finally:
            for item, show_name_label, labels_as_paths in label_states:
                item.show_name_label = show_name_label
                item._labels_as_paths = labels_as_paths
                item.refresh_label_placement(wire_index)
            for item, was_visible in visibility:
                item.setVisible(was_visible)
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()

        self.footer_status_label.setText(
            f"Annotated One-Line PDF saved: {file_path}"
        )

    def _on_save_results_clicked(self):
        if (self.last_result is not None
                and self._evaluation_content(self._layout_data()) != self._evaluation_content(self._evaluated_snapshot)):
            self._invalidate_results()
        if self.last_result is None or self.last_netlist is None:
            QMessageBox.information(
                self, "Save Calculation PDF", "Run an evaluation first."
            )
            return

        result = self.last_result
        report = result.get("protection_analysis") or {}
        study_status = "COMPLETE" if report.get("complete") else messages.SUBSTATION_GUI4_INCOMPLETE_PROVISIONAL_STUDY

        zone_summary_rows = []
        review_rows = []
        for zone in report.get("zones", []):
            status = "COMPLETE" if zone.get("complete") else "REVIEW REQUIRED — PROVISIONAL"
            status_class = "complete" if zone.get("complete") else "incomplete"
            stage_totals = []
            for scenario in zone.get("scenarios", []):
                if not scenario.get("loads"):
                    stage_totals.append(
                        f"{escape(str(scenario.get('stage', 'Unspecified stage')))}: "
                        "<b class='incomplete'>Not calculated</b>"
                    )
                    continue
                qualifier = "Confirmed" if zone.get("complete") else "PROVISIONAL"
                stage_totals.append(
                    f"{escape(str(scenario.get('stage', 'Unspecified stage')))}: "
                    f"<b class='{status_class}'>{qualifier} "
                    f"{escape(str(scenario.get('peak_current_A', 'N/A')))} A</b>"
                )
            total_html = "<br>".join(stage_totals) or "No calculated stages"
            zone_summary_rows.append(
                "<tr>"
                f"<td>{escape(str(zone.get('name', 'Unnamed zone')))}</td>"
                f"<td>{total_html}</td>"
                f"<td class='{status_class}'><b>{status}</b></td>"
                "</tr>"
            )
            zone_name = str(zone.get("name", "Unnamed zone"))
            review_rows.extend((zone_name, str(warning)) for warning in zone.get("warnings", []))
            for scenario in zone.get("scenarios", []):
                review_rows.extend(
                    (f"{zone_name} / {scenario.get('stage', 'Unspecified stage')}", str(advisory))
                    for advisory in scenario.get("advisories", [])
                )
        zone_summary_html = "".join(zone_summary_rows) or (
            "<tr><td colspan='3'>No configured zones.</td></tr>"
        )
        review_rows.extend(("Study", str(warning)) for warning in report.get("warnings", []))
        review_html = "".join(
            "<tr>"
            f"<td>{escape(zone_name)}</td>"
            f"<td>{escape(warning)}</td>"
            "</tr>"
            for zone_name, warning in dict.fromkeys(review_rows)
        ) or "<tr><td colspan='2'>None.</td></tr>"
        supply_peak_html = "".join(
            "<tr>"
            f"<td>{escape(str(supply))}</td>"
            f"<td>{escape(str(peak.get('peak_current_A', 'N/A')))} A</td>"
            f"<td>{escape(str(peak.get('zone', '')))} / {escape(str(peak.get('stage', '')))}</td>"
            "</tr>"
            for supply, peak in sorted(report.get("supply_peaks", {}).items())
        ) or (
            "<tr><td colspan='3'>Unavailable until zone assignments and currents are complete.</td></tr>"
        )
        evaluation_html = self._evaluation_output_html(result, report)
        html = f"""
        <html>
        <head>
          <style>
            body {{ font-family: sans-serif; font-size: 9pt; margin: 0; }}
            h1, h2 {{ color: #006A4E; }}
            h1 {{ margin: 0 0 6px; }}
            h2 {{ margin: 10px 0 4px; }}
            p {{ margin: 4px 0; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 6px; }}
            .summary {{ font-size: 8.5pt; }}
            th, td {{
                border: 1px solid #cbd2d9;
                padding: 4px;
                text-align: left;
                vertical-align: top;
            }}
            th {{ background: #e8f1ed; }}
            tr {{ page-break-inside: avoid; }}
            .complete {{ color: #176b3a; }}
            .incomplete {{ color: #9a4f00; background: #fff3cd; }}
            pre {{ white-space: pre-wrap; font-size: 8.5pt; margin: 0; }}
          </style>
        </head>

        <body>
          <h1>Calculation Results</h1>
          <p><b>{escape(study_status)}</b></p>
          <p><b>System:</b> {escape(str(result.get('scenario_name', 'N/A')))}
            &nbsp; <b>Voltage:</b> {escape(str(result.get('voltage_V', 'N/A')))} V</p>

          <h2>Supply Peaks</h2>
          <table class="summary">
            <tr><th>DC supply</th><th>Maximum current</th><th>Zone / stage</th></tr>
            {supply_peak_html}
          </table>

          <h2>Zone Summary</h2>
          <p>Stage totals and separate zone faults are not added together.</p>
          <table class="summary">
            <tr><th>Zone</th><th>Per-stage total across assigned supplies</th><th>Status</th></tr>
            {zone_summary_html}
          </table>

          <h2>Review</h2>
          <table class="summary">
            <tr><th>Zone / stage</th><th>Issue</th></tr>
            {review_html}
          </table>

          <h2>Evaluation Output</h2>
          <pre>{evaluation_html}</pre>
        </body>
        </html>
        """

        self._save_html_as_pdf(
            "Save Calculation PDF", "calculation_results.pdf", html, "Results saved"
        )

    @staticmethod
    def _evaluation_output_html(result, report):
        """Each zone once, its stages and loads beneath, with stage totals.

        Zone membership (equipment and CT IDs) is left to the netlist PDF.
        """
        def amps(value):
            return f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)

        lines = [
            escape(f"Momentary DC protection-zone study: {result.get('scenario_name', 'N/A')}"),
            escape("COMPLETE" if report.get("complete") else
                   messages.SUBSTATION_GUI4_INCOMPLETE_CONFIGURE_REVIEW_FLAGGED_ZONES_PARTIAL_TOTALS_ARE_NOT),
            "Each configured zone is a separate fault event. Same-stage loads are simultaneous; "
            "other stages are separate.",
            "DC supplies are reported separately. No battery amp-hour calculation.",
            "",
        ]
        lines += [escape(messages.SUBSTATION_GUI4_REVIEW_WARNING.format(warning))
                  for warning in report.get("warnings", [])]
        for zone in report.get("zones", []):
            lines.append(f"<b>{escape(str(zone.get('name', 'Unnamed zone')))}</b>")
            lines += [escape(messages.SUBSTATION_GUI4_INDENTED_REVIEW_WARNING.format(warning))
                      for warning in zone.get("warnings", [])]
            prefix = "" if zone.get("complete") else messages.SUBSTATION_GUI4_PROVISIONAL
            for scenario in zone.get("scenarios", []):
                total = amps(scenario.get("peak_current_A", "N/A"))
                lines.append(escape(f"  {prefix}{scenario.get('stage', 'Unspecified stage')}: "
                                    f"{total} A across assigned supplies"))
                lines += [escape(f"    NOTE: {advice}") for advice in scenario.get("advisories", [])]
                lines += [escape(f"    {supply}: {amps(current)} A")
                          for supply, current in scenario.get("supplies", {}).items()]
                lines += [escape(f"    {load.get('name', 'Load')}: {amps(load.get('amps_per_unit', 'N/A'))} A "
                                 f"× {load.get('quantity', 1)} = {amps(load.get('total_amps', 'N/A'))} A "
                                 f"({load.get('supply', '')})")
                          for load in scenario.get("loads", [])]
                lines.append(f"    <b>Stage total: {escape(total)} A</b>")
            lines.append("")
        lines.append("Peak per supply among confirmed, complete configured zones:")
        for supply, peak in report.get("supply_peaks", {}).items():
            lines.append(f"  {escape(str(supply))}: {escape(amps(peak.get('peak_current_A', 'N/A')))} A — "
                         f"<b>{escape(str(peak.get('zone', '')))}</b> / {escape(str(peak.get('stage', '')))}")
        if not report.get("supply_peaks"):
            lines.append(escape(messages.SUBSTATION_GUI4_NOT_AVAILABLE_UNTIL_ZONE_ASSIGNMENTS_AND_CURRENTS_ARE_COMPLETE))
        return "\n".join(lines)


    def _save_html_as_pdf(
        self, title: str, default_filename: str, html: str, success_message: str
    ):
        file_path, _ = QFileDialog.getSaveFileName(
            self, title, default_filename, "PDF Files (*.pdf)"
        )
        if not file_path:
            return

        if not Path(file_path).suffix:
            file_path += ".pdf"

        document = QTextDocument()
        document.setHtml(html)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=Path(file_path).parent, suffix=".pdf", delete=False
            ) as stream:
                temporary = stream.name
            printer = QPrinter()
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(temporary)
            document.print_(printer)
            if printer.printerState() == QPrinter.PrinterState.Error or Path(temporary).stat().st_size == 0:
                raise OSError(messages.SUBSTATION_GUI4_THE_PDF_FILE_WAS_NOT_CREATED_OR_IS_EMPTY)
            os.replace(temporary, file_path)
        except OSError as error:
            QMessageBox.critical(
                self, messages.SUBSTATION_GUI4_SAVE_FAILED, messages.SUBSTATION_GUI4_COULD_NOT_CREATE_THE_PDF.format(error)
            )
            return
        finally:
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()

        self.footer_status_label.setText(f"{success_message}: {file_path}")


    def _on_save_netlist_pdf(self):
        netlist = self._build_current_netlist()
        self.last_netlist = netlist
        # Junction dots are wire-drawing helpers, not equipment, and wiring
        # detail is left out of this equipment list.
        components = [c for c in netlist.get("components", []) if c.get("type") != "junction"]
        component_rows = []
        for component in components:
            properties = component.get("properties") or {}
            property_text = "<br>".join(
                f"<b>{escape(property_label(key))}:</b> {escape(property_value(key, value))}"
                for key, value in properties.items()
                if key != "symbol_type"  # already shown, in full, in the Type column
            ) or "None"
            ports = ", ".join(str(port) for port in component.get("ports", []))
            component_rows.append(
                "<tr>"
                f"<td>{escape(str(component.get('display_id', component.get('id', ''))))}</td>"
                f"<td>{escape(str(component.get('name', '')))}</td>"
                f"<td>{escape(type_label(component.get('type', '')))}</td>"
                f"<td>{property_text}</td>"
                f"<td>{escape(ports or 'None')}</td>"
                "</tr>"
            )
        component_table = "".join(component_rows) or (
            "<tr><td colspan='5'>No components in this netlist.</td></tr>"
        )
        html = f"""
        <html>
        <head>
          <style>
            body {{ font-family: sans-serif; font-size: 9pt; color: #1f2933; }}
            h1, h2 {{ color: #006A4E; }}
            h1 {{ margin: 0 0 6px; }}
            h2 {{ margin: 8px 0 4px; }}
            p {{ margin: 4px 0; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 8px; }}
            th, td {{ border: 1px solid #cbd2d9; padding: 4px; text-align: left; vertical-align: top; }}
            th {{ background: #e8f1ed; }}
            tr {{ page-break-inside: avoid; }}
          </style>
        </head>
        <body>
          <h1>Netlist: {escape(str(netlist.get('project_name', 'Untitled Project')))}</h1>
          <p><b>Schema version:</b> {escape(str(netlist.get('schema_version', 'N/A')))}
            &nbsp; <b>Components:</b> {len(components)}</p>
          <h2>Components</h2>
          <table>
            <thead><tr>
              <th>ID</th><th>Name</th><th>Type</th><th>Properties</th><th>Ports</th>
            </tr></thead>
            <tbody>{component_table}</tbody>
          </table>
        </body>
        </html>
        """

        self._save_html_as_pdf(
            "Save Netlist PDF", "netlist.pdf", html, "Netlist PDF saved"
        )
