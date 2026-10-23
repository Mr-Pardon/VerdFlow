from __future__ import annotations

import unittest

from tools.fixtures.selection import (
    bbox_inside,
    point_inside,
    point_matches_member,
)


class FixtureBuilderTests(unittest.TestCase):
    def test_point_window_is_inclusive(self) -> None:
        bounds = (-10.0, -20.0, 10.0, 20.0)

        self.assertTrue(
            point_inside({"source_x": -10.0, "source_y": 20.0}, bounds)
        )
        self.assertFalse(
            point_inside({"source_x": -10.01, "source_y": 20.0}, bounds)
        )

    def test_region_must_be_fully_inside_golden_window(self) -> None:
        bounds = (-10.0, -20.0, 10.0, 20.0)

        self.assertTrue(
            bbox_inside([-9.0, -19.0, 0.0, 9.0, 19.0, 1.0], bounds)
        )
        self.assertFalse(
            bbox_inside([-11.0, -19.0, 0.0, 9.0, 19.0, 1.0], bounds)
        )

    def test_member_match_accepts_hit_object_or_region_prefix(self) -> None:
        self.assertTrue(
            point_matches_member({"hit_object": "member_a_mesh_0001"}, "A")
        )
        self.assertTrue(
            point_matches_member({"region_id": "B_region_001"}, "B")
        )
        self.assertFalse(
            point_matches_member({"region_id": "A_region_001"}, "B")
        )


if __name__ == "__main__":
    unittest.main()
