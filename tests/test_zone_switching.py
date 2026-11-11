"""Switching and proposal regressions for CT-bounded operating regions."""
import copy
import unittest

from gft_opto.logic.protection_study import (
    add_missing_proposals, analyze_topology, configuration_signature,
    evaluate_zones, propose_zones, topology_signature, zone_members,
)


def component(ident, typ, **properties):
    return {"id": ident, "type": typ, "label": ident, "properties": properties}


def wire(a, b, ap="right", bp="left"):
    return {"from_id": a, "from_port": ap, "to_id": b, "to_port": bp}


def assign_candidates(zone):
    """Proposals only suggest breakers; assign them as the reviewed trip loads."""
    zone["actions"] = [{"component": ident, "load": "trip_coil_1_a", "stage": "Primary trip",
                        "supply": "DC 1", "units": 1} for ident in zone["candidate_breakers"]]
    return zone


def bus_sections(status="Normally open", switch_type="disconnect"):
    return {
        "components": [component("a", "bus"), component("b", "bus"),
                       component("tie", switch_type, status=status),
                       component("ct-a", "ct"), component("ct-b", "ct"),
                       component("cb-a", "breaker", trip_coil_1_a=10),
                       component("cb-b", "breaker", trip_coil_1_a=20)],
        "connections": [wire("a", "tie"), wire("tie", "b"),
                        wire("a", "ct-a"), wire("ct-a", "cb-a"),
                        wire("b", "ct-b"), wire("ct-b", "cb-b")],
    }


class SwitchingRegions(unittest.TestCase):
    def test_open_sections_remain_separate_and_have_separate_trip_actions(self):
        for typ in ("disconnect", "mos", "breaker", "circuit breaker"):
            for status in ("Open", "Normally open", "normally-open", "N.O."):
                with self.subTest(typ=typ, status=status):
                    net = bus_sections(status, typ)
                    zones = propose_zones(net)
                    self.assertEqual(len(zones), 2)
                    for zone in zones:
                        members, warnings = zone_members(net, zone)
                        self.assertFalse(warnings)
                        self.assertIn("tie", members)
                        self.assertFalse({"a", "b"} <= set(members))
                        self.assertEqual(zone["candidate_breakers"], ["cb-" + zone["seed"]])

    def test_closed_or_unspecified_switch_joins_sections_without_duplicates(self):
        for status in ("Closed", "Normally closed", "Maintenance", ""):
            with self.subTest(status=status):
                net = bus_sections(status)
                if not status:
                    net["components"][2]["properties"].clear()
                zones = propose_zones(net)
                self.assertEqual(len(zones), 1)
                self.assertTrue({"a", "b"} <= set(zone_members(net, zones[0])[0]))
                self.assertEqual(set(zones[0]["candidate_breakers"]), {"cb-a", "cb-b"})

    def test_open_bypass_does_not_bypass_ct_but_closed_bypass_is_flagged(self):
        net = {"components": [component("bus", "bus"), component("ct", "ct"),
                              component("cb", "breaker", trip_coil_1_a=10),
                              component("bypass", "disconnect", status="Normally open")],
               "connections": [wire("bus", "ct"), wire("ct", "cb"),
                               wire("bus", "bypass"), wire("bypass", "cb")]}
        zone = propose_zones(net)[0]
        members, warnings = zone_members(net, zone)
        self.assertNotIn("cb", members)
        self.assertFalse(warnings)
        self.assertEqual(zone["candidate_breakers"], ["cb"])
        net["components"][-1]["properties"]["status"] = "Closed"
        zone = propose_zones(net)[0]
        members, warnings = zone_members(net, zone)
        self.assertIn("cb", members)
        self.assertIn("bypassed", " ".join(warnings))

    def test_trip_suggestions_do_not_cross_open_switch_beyond_ct(self):
        net = {"components": [component("bus", "bus"), component("ct", "ct"),
                              component("isolator", "disconnect", status="Open"),
                              component("cb", "breaker", trip_coil_1_a=10)],
               "connections": [wire("bus", "ct"), wire("ct", "isolator"), wire("isolator", "cb")]}
        self.assertEqual(propose_zones(net)[0]["candidate_breakers"], [])
        net["components"][2]["properties"]["status"] = "Closed"
        self.assertEqual(propose_zones(net)[0]["candidate_breakers"], ["cb"])

    def test_ct_status_is_not_a_switch_cut_and_open_isolated_devices_survive(self):
        net = {"components": [component("bus", "bus"), component("ct", "ct", status="Open"),
                              component("spare", "disconnect", status="Open")],
               "connections": [wire("bus", "ct")]}
        primary = [r for r in analyze_topology(net) if r["level"] == "Primary"]
        self.assertEqual(len(primary), 2)
        self.assertEqual(next(r for r in primary if "bus" in r["core"])["boundaries"], ["ct"])
        self.assertEqual(next(r for r in primary if "spare" in r["core"])["members"], ["spare"])

    def test_open_breaker_alone_does_not_propose_an_ambiguous_operating_zone(self):
        net = {"components": [component("cb", "breaker", status="Open"),
                              component("left", "incoming_line"), component("right", "incoming_line")],
               "connections": [wire("left", "cb"), wire("cb", "right")]}
        self.assertEqual(propose_zones(net), [])
        self.assertEqual(len([r for r in analyze_topology(net) if r["level"] == "Primary"]), 2)

    def test_closed_bypass_still_connects_open_switch_terminal_sides(self):
        net = bus_sections()
        net["connections"].append(wire("a", "b"))
        self.assertEqual(len(propose_zones(net)), 1)

    def test_existing_alternate_bus_seed_is_preserved_without_duplicate(self):
        net = bus_sections("Closed")
        existing = propose_zones(net)[0]
        existing.update(seed="b", name="Reviewed common bus", confirmed=True, notes="keep")
        existing["topology_signature"] = topology_signature(net)
        existing["configuration_signature"] = configuration_signature(existing)
        original = copy.deepcopy(existing)
        self.assertEqual(add_missing_proposals(net, [existing]), [original])
        self.assertEqual(existing, original)

    def test_separate_ct_regions_not_deduplicated(self):
        net = bus_sections("Closed")
        net["components"][2] = component("tie", "ct")
        self.assertEqual(len(propose_zones(net)), 2)

    def test_old_detector_approval_requires_review_even_with_unchanged_wires(self):
        net = bus_sections("Closed")
        zone = assign_candidates(propose_zones(net)[0])
        zone["confirmed"] = True
        zone["topology_signature"] = topology_signature(net).replace("ct-study-v3:", "ct-study-v2:")
        zone["configuration_signature"] = configuration_signature(zone)
        report = evaluate_zones(net, [zone])
        self.assertFalse(report["complete"])
        self.assertEqual(report["supply_peaks"], {})
        self.assertIn("Reconfirm", " ".join(report["zones"][0]["warnings"]))
        zone["topology_signature"] = topology_signature(net)
        report = evaluate_zones(net, [zone])
        self.assertTrue(report["complete"], report)
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 30)


if __name__ == "__main__":
    unittest.main()
