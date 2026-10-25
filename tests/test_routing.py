"""OWNER: C. Unit tests for the pure routing logic (controller/routing.py). No Ryu, no root.

Run:  PYTHONPATH=. python3 -m unittest tests.test_routing -v
"""
import unittest

from common import constants as C
from controller import routing


class RoutingTest(unittest.TestCase):

    def test_topology_detection(self):
        self.assertEqual(routing.topology_kind([1]), C.TOPO_SINGLE)
        self.assertEqual(routing.topology_kind([]), C.TOPO_SINGLE)
        self.assertEqual(routing.topology_kind([1, 2, 3]), C.TOPO_TRIANGLE)
        self.assertEqual(routing.topology_kind([2]), C.TOPO_TRIANGLE)

    def test_edge_ports_single(self):
        self.assertEqual(routing.edge_ports(C.TOPO_SINGLE, 1), {1, 2, 3, 4, 5})
        self.assertEqual(routing.edge_ports(C.TOPO_SINGLE, 2), set())

    def test_edge_ports_triangle_exclude_inter_switch_links(self):
        self.assertEqual(routing.edge_ports(C.TOPO_TRIANGLE, 1), {1, 4})      # h1 and the root interface
        self.assertEqual(routing.edge_ports(C.TOPO_TRIANGLE, 2), {1})         # h2
        self.assertEqual(routing.edge_ports(C.TOPO_TRIANGLE, 3), {1, 2})      # h3, h4

    def test_same_switch_needs_no_hops(self):
        self.assertEqual(routing.next_hops(1, 1), [])

    def test_direct_links(self):
        self.assertEqual(routing.next_hops(1, 2), [(1, 2)])
        self.assertEqual(routing.next_hops(2, 1), [(2, 2)])
        self.assertEqual(routing.next_hops(1, 3), [(1, 3)])
        self.assertEqual(routing.next_hops(2, 3), [(2, 3)])
        self.assertEqual(routing.next_hops(3, 2), [(3, 4)])

    def test_reroutes_when_a_link_is_down(self):
        self.assertEqual(routing.next_hops(2, 3, [(2, 3)]), [(2, 2), (1, 3)])
        self.assertEqual(routing.next_hops(2, 3, [(3, 2)]), [(2, 2), (1, 3)])   # either order
        self.assertEqual(routing.next_hops(1, 2, [(1, 2)]), [(1, 3), (3, 4)])

    def test_unreachable_returns_none(self):
        self.assertIsNone(routing.next_hops(2, 1, [(1, 2), (2, 3)]))
        self.assertIsNone(routing.next_hops(1, 9))

    def test_single_topology_has_no_links(self):
        self.assertEqual(routing.next_hops(1, 1, kind=C.TOPO_SINGLE), [])
        self.assertIsNone(routing.next_hops(1, 2, kind=C.TOPO_SINGLE))


if __name__ == "__main__":
    unittest.main()
