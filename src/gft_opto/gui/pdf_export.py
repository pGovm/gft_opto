"""Calculation and netlist PDF export workflows."""
import os
import tempfile

from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QFileDialog, QMessageBox
from gft_opto.gui import messages
from html import escape
from pathlib import Path


class PdfExportMixin:
    def _on_save_results_clicked(self):
        if self.last_result is not None and self._layout_data() != self._evaluated_snapshot:
            self._invalidate_results()
        if self.last_result is None or self.last_netlist is None:
            QMessageBox.information(
                self, "Save Calculation PDF", "Run an evaluation first."
            )
            return

        result = self.last_result
        report = result.get("protection_analysis") or {}
        study_status = "COMPLETE" if report.get("complete") else messages.SUBSTATION_GUI4_INCOMPLETE_PROVISIONAL_STUDY
        demo_notice = (
            "<p><b>DEMONSTRATION — sample currents and assumed operating actions.</b> "
            "Demonstration mode allows incomplete zone settings to be marked complete. "
            "Background loads are added to each operating stage on DC 1. "
            "Verify the currents and operating actions before engineering use.</p>"
            if report.get("demonstration_mode") else ""
        )

        loads = result.get("loads") or []
        load_rows = "".join(
            "<tr>"
            f"<td>{escape(str(load.get('zone_name', '')))}<br>"
            f"{escape(str(load.get('stage', '')))} / {escape(str(load.get('supply', '')))}</td>"
            f"<td>{escape(str(load.get('name', 'Load')))}</td>"
            f"<td>{escape(str(load.get('quantity', 1)))}</td>"
            f"<td>{escape(str(load.get('amps_per_unit', 'N/A')))} A</td>"
            f"<td>{escape(str(load.get('total_amps', 'N/A')))} A</td>"
            "</tr>"
            for load in loads
        ) or "<tr><td colspan='5'>No loads returned.</td></tr>"

        zone_summary_rows = []
        review_rows = []
        for zone in report.get("zones", []):
            status = ("COMPLETE (DEMO)" if zone.get("demonstration_complete") else
                      "COMPLETE" if zone.get("complete") else "REVIEW REQUIRED — PROVISIONAL")
            status_class = "complete" if zone.get("complete") else "incomplete"
            stage_totals = []
            for scenario in zone.get("scenarios", []):
                if not scenario.get("loads"):
                    stage_totals.append(
                        f"{escape(str(scenario.get('stage', 'Unspecified stage')))}: "
                        "<b class='incomplete'>Not calculated</b>"
                    )
                    continue
                qualifier = ("Demo" if zone.get("demonstration_complete") else
                             "Confirmed" if zone.get("complete") else "PROVISIONAL")
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
          {demo_notice}
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

          <h2>Load Breakdown</h2>
          <table>
            <tr>
                <th>Zone / stage / supply</th>
                <th>Load</th>
                <th>Quantity</th>
                <th>Current / unit</th>
                <th>Total</th>
            </tr>
            {load_rows}
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
        """The full evaluation output: each zone once, its stages and loads beneath, with stage totals."""
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
            members = ", ".join(zone.get("members", [])) or "No members"
            lines.append(f"<b>{escape(str(zone.get('name', 'Unnamed zone')))}</b> "
                         f"{escape(f'({members}) ({zone.get('kind', 'Manual')})')}")
            lines.append(escape("  CT boundaries: " + ", ".join(zone.get("boundaries", []))))
            lines.append(escape("  Equipment: " + members))
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
        component_rows = []
        for component in netlist.get("components", []):
            properties = component.get("properties") or {}
            property_text = "<br>".join(
                f"<b>{escape(str(key))}:</b> "
                f"{escape(str(value))}"
                for key, value in properties.items()
            ) or "None"
            ports = ", ".join(str(port) for port in component.get("ports", []))
            component_rows.append(
                "<tr>"
                f"<td>{escape(str(component.get('display_id', component.get('id', ''))))}</td>"
                f"<td>{escape(str(component.get('name', '')))}</td>"
                f"<td>{escape(str(component.get('type', '')))}</td>"
                f"<td>{property_text}</td>"
                f"<td>{escape(ports or 'None')}</td>"
                "</tr>"
            )
        component_table = "".join(component_rows) or (
            "<tr><td colspan='5'>No components in this netlist.</td></tr>"
        )

        connection_rows = []
        for connection in netlist.get("connections", []):
            source = connection.get("from", {})
            target = connection.get("to", {})
            source_label = (
                f"{source.get('component', '')} ({source.get('port', '')})"
            )
            target_label = (
                f"{target.get('component', '')} ({target.get('port', '')})"
            )
            connection_rows.append(
                "<tr>"
                f"<td>{escape(str(connection.get('id', '')))}</td>"
                f"<td>{escape(str(source_label))}</td>"
                f"<td>{escape(str(target_label))}</td>"
                "</tr>"
            )
        connection_table = "".join(connection_rows) or (
            "<tr><td colspan='3'>No connections in this netlist.</td></tr>"
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
            &nbsp; <b>Components:</b> {len(netlist.get('components', []))}
            &nbsp; <b>Connections:</b> {len(netlist.get('connections', []))}</p>
          <h2>Components</h2>
          <table>
            <thead><tr>
              <th>ID</th><th>Name</th><th>Type</th><th>Properties</th><th>Ports</th>
            </tr></thead>
            <tbody>{component_table}</tbody>
          </table>
          <h2>Connections</h2>
          <table>
            <thead><tr><th>ID</th><th>From</th><th>To</th></tr></thead>
            <tbody>{connection_table}</tbody>
          </table>
        </body>
        </html>
        """

        self._save_html_as_pdf(
            "Save Netlist PDF", "netlist.pdf", html, "Netlist PDF saved"
        )
