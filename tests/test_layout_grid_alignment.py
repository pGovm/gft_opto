"""Imported positions follow the same terminal anchor as interactive placement."""
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QGraphicsScene
from gft_opto.gui.symbols import CircuitBreakerItem, OneLineSymbolItem, ConnectionItem, JunctionItem
from gft_opto.gui.layout_io import build_scene, scene_data
from gft_opto.gui.routing import default_snap_port, snap_point_to_grid


class LayoutGridAlignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def drawing(self, snap):
        scene = QGraphicsScene()
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
            scene.addItem(ConnectionItem(breaker, 'right', junction, 'node'))
            return dict(version=1, project='Grid test', scene_rect=[0,0,1000,1000],
                        settings=dict(snap=snap, grid_visible=True, grid_opacity=15, pdf_visible=False),
                        library={}, zones=[], pdf=None, **scene_data(scene))
        finally:
            OneLineSymbolItem.snap_to_grid_enabled = previous

    def test_import_snaps_rotated_scaled_symbol_and_junction(self):
        data = self.drawing(True)
        scene = build_scene(data, align_to_grid=True)
        for item in scene.items():
            if isinstance(item, OneLineSymbolItem):
                anchor = item.port_scene_pos(default_snap_port(item))
                self.assertLess((anchor - snap_point_to_grid(anchor)).manhattanLength(), 1e-8)
        result = scene_data(scene)
        self.assertEqual(result['connections'], data['connections'])
        second = build_scene(dict(data, **result), align_to_grid=True)
        self.assertEqual(scene_data(second), result)

    def test_snap_disabled_preserves_saved_coordinates(self):
        data = self.drawing(False)
        scene = build_scene(data, align_to_grid=True)
        self.assertEqual(scene_data(scene), {k:data[k] for k in ('components','connections')})

    def test_validation_preserves_saved_coordinates(self):
        data = self.drawing(True)
        scene = build_scene(data)
        self.assertEqual(scene_data(scene), {k:data[k] for k in ('components','connections')})
