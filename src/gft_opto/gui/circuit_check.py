"""Open-end and short-circuit check for a drawn one-line."""

from collections import defaultdict

from PySide6.QtWidgets import QGraphicsScene

from gft_opto.gui.symbols import (
    BusItem,
    ConnectionItem,
    GroundItem,
    JunctionItem,
    OneLineSymbolItem,
)


# Port pairs that must not land on the same electrical node.
_DEVICE_PORT_PAIRS: dict[str, list[tuple[str, str]]] = {
    "xfmr_2w": [("H", "X")],
    "xfmr_3w": [("H1", "H2"), ("H1", "X"), ("H2", "X")],
    "breaker": [("left", "right")],
    "disconnect": [("left", "right")],
    "mos": [("left", "right")],
    "ct": [("left", "right")],
    "pt": [("line", "ground")],
}


class _PortUnion:
    """Disjoint-set of (symbol id, port name) electrical nodes."""

    def __init__(self):
        self._parent: dict[tuple[int, str], tuple[int, str]] = {}

    def add(self, key: tuple[int, str]):
        self._parent.setdefault(key, key)

    def find(self, key: tuple[int, str]) -> tuple[int, str]:
        parent = self._parent.get(key, key)
        if parent != key:
            parent = self.find(parent)
            self._parent[key] = parent
        else:
            self._parent.setdefault(key, key)
        return parent

    def union(self, a: tuple[int, str], b: tuple[int, str]):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _symbol_caption(item: OneLineSymbolItem) -> str:
    return item.visible_id() or item.label


def check_circuit(scene: QGraphicsScene) -> dict:
    """
    Check the drawn one-line for open terminals and short circuits.

    """
    symbols = [it for it in scene.items() if isinstance(it, OneLineSymbolItem)]
    symbol_ids = {id(it) for it in symbols}
    wires = [
        it for it in scene.items()
        if isinstance(it, ConnectionItem)
        and it.from_item is not None
        and it.to_item is not None
        and id(it.from_item) in symbol_ids
        and id(it.to_item) in symbol_ids
    ]
    if not symbols:
        return {
            "ok": False,
            "open_ends": ["No components on the canvas."],
            "shorts": [],
        }

    nodes = _PortUnion()
    for symbol in symbols:
        for port in symbol.ports():
            nodes.add((id(symbol), port))
        if isinstance(symbol, BusItem):
            ports = list(symbol.ports())
            for port in ports[1:]:
                nodes.union((id(symbol), ports[0]), (id(symbol), port))

    degree: dict[tuple[int, str], int] = defaultdict(int)
    for wire in wires:
        a = (id(wire.from_item), wire.from_port)
        b = (id(wire.to_item), wire.to_port)
        nodes.add(a)
        nodes.add(b)
        nodes.union(a, b)
        degree[a] += 1
        degree[b] += 1

    open_ends: list[str] = []
    shorts: list[str] = []

    for symbol in symbols:
        name = _symbol_caption(symbol)
        if isinstance(symbol, JunctionItem):
            count = degree.get((id(symbol), "node"), 0)
            if count == 0:
                open_ends.append(f"{name}: junction is not connected")
            elif count == 1:
                open_ends.append(f"{name}: dangling wire (only one connection)")
            continue
        if isinstance(symbol, BusItem):
            if not any(degree.get((id(symbol), port), 0) for port in symbol.ports()):
                open_ends.append(f"{name}: bus is not connected")
            continue
        for port in symbol.ports():
            if degree.get((id(symbol), port), 0) == 0:
                open_ends.append(f"{name}: {port} is not connected")

        pairs = _DEVICE_PORT_PAIRS.get(symbol.equip_type)
        if pairs is None and not isinstance(symbol, (GroundItem, JunctionItem, BusItem)):
            ports = list(symbol.ports())
            if len(ports) == 2:
                pairs = [(ports[0], ports[1])]
        for left, right in pairs or []:
            if left not in symbol.ports() or right not in symbol.ports():
                continue
            left_key = (id(symbol), left)
            right_key = (id(symbol), right)
            if degree.get(left_key, 0) == 0 or degree.get(right_key, 0) == 0:
                continue
            if nodes.find(left_key) == nodes.find(right_key):
                shorts.append(f"{name}: {left} and {right} are on the same node")

    for ground in symbols:
        if not isinstance(ground, GroundItem):
            continue
        ground_key = (id(ground), "node")
        if degree.get(ground_key, 0) == 0 or "node" not in ground.ports():
            continue
        for bus in symbols:
            if not isinstance(bus, BusItem):
                continue
            bus_ports = list(bus.ports())
            if not bus_ports or not any(degree.get((id(bus), port), 0) for port in bus_ports):
                continue
            if nodes.find(ground_key) == nodes.find((id(bus), bus_ports[0])):
                shorts.append(
                    f"{_symbol_caption(bus)} is tied directly to {_symbol_caption(ground)}"
                )

    return {
        "ok": not open_ends and not shorts,
        "open_ends": open_ends,
        "shorts": shorts,
    }

