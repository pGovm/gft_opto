"""Opening keeps saved positions; Align to Grid snaps terminals only when asked."""
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QGraphicsScene
from gft_opto.gui.substation_gui import SubstationGuiMockup
from gft_opto.gui.symbols import CircuitBreakerItem, OneLineSymbolItem, ConnectionItem, JunctionItem
from gft_opto.gui.layout_io import build_scene, scene_data
from gft_opto.gui.routing import default_snap_port, snap_point_to_grid


class LayoutGridAlignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def place_off_grid(self, scene):
        """A rotated, scaled breaker wired to a junction, both off the grid."""
        previous = OneLineSymbolItem.snap_to_grid_enabled
        OneLineSymbolItem.snap_to_grid_enabled = False
        try:
            breaker = CircuitBreakerItem()
            scene.addItem(breaker)
            breaker.set_scale_factor(1.5)
            breaker.set_rotation_deg(90)
            breaker.setPos(113, 217)
            junction = JunctionItem()
            scene.addItem(junction)
            junction.setPos(113, 403)
            wire = ConnectionItem(breaker, 'right', junction, 'node')
            scene.addItem(wire)
            return breaker, junction, wire
        finally:
            OneLineSymbolItem.snap_to_grid_enabled = previous

    def drawing(self):
        scene = QGraphicsScene()
        self.place_off_grid(scene)
        return dict(version=1, project='Grid test', scene_rect=[0,0,1000,1000],
                    settings=dict(snap=True, grid_visible=True, grid_opacity=15, pdf_visible=False),
                    library={}, zones=[], pdf=None, **scene_data(scene))

    def test_align_to_grid_snaps_rotated_scaled_symbol_and_junction(self):
        window = SubstationGuiMockup()
        try:
            breaker, junction, wire = self.place_off_grid(window.workspace_scene)
            original = [(item, QPointF(item.pos())) for item in (breaker, junction)]
            window.align_grid_action.trigger()
            for item in (breaker, junction):
                anchor = item.port_scene_pos(default_snap_port(item))
                self.assertLess((anchor - snap_point_to_grid(anchor)).manhattanLength(), 1e-8)
            # The wire follows both moved terminals.
            route = wire.route_points()
            self.assertEqual(route[0], breaker.port_scene_pos('right'))
            self.assertEqual(route[-1], junction.port_scene_pos('node'))
            # One Undo restores every moved symbol exactly.
            window.undo_stack.undo()
            for item, position in original:
                self.assertEqual(item.pos(), position)
            window.undo_stack.redo()
            # Already aligned: running it again adds no edit.
            steps = window.undo_stack.count()
            window.align_grid_action.trigger()
            self.assertEqual(window.undo_stack.count(), steps)
        finally:
            window._saved_snapshot = window._layout_data()
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_validation_preserves_saved_coordinates(self):
        data = self.drawing()
        scene = build_scene(data)
        self.assertEqual(scene_data(scene), {k:data[k] for k in ('components','connections')})
