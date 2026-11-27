from pathlib import Path
import math
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from warnings import catch_warnings, simplefilter

from tests.unit.test_dts_live_connector_server import HASH, ROOT, _request, _resolve

sys.path.insert(0, str(ROOT / "adapters" / "geospatial-worker" / "src"))

from verdflow.dts_live_connector import DtsLiveConnectorServer  # noqa: E402
from verdflow.system_host import DtsLiveConnectorManager  # noqa: E402


class ActorHighlightTests(unittest.TestCase):
    def test_near_white_pair_requires_two_hues_and_change_from_base(self):
        import numpy as np
        from verdflow.geospatial_worker.actor_footprints import _highlight_mask

        dark = (5, 18, 2)
        pale_m, pale_c = (255, 243, 255), (247, 255, 255)
        samples = [
            (pale_m, pale_c, dark, True),                  # Field sample: shift 20.
            ((255, 215, 255), (215, 255, 255), dark, True),
            ((255, 255, 255), (255, 255, 255), dark, False),
            (pale_m, pale_m, dark, False),                 # Same tint in both frames.
            (pale_c, pale_m, dark, False),                 # Wrong polarity.
            (pale_m, pale_c, (238, 235, 238), False),      # Base already bright.
            (pale_m, (247, 255, 247), dark, False),        # Blue is not stable.
            ((210, 200, 210), (201, 210, 210), dark, False),
        ]
        m, c, b, expected = zip(*samples)
        mask = _highlight_mask(*(np.array(v, dtype="int16").T for v in (m, c, b)))
        np.testing.assert_array_equal(mask, expected)

    def test_radial_chroma_gradient_does_not_cut_a_hole_or_fill_a_real_one(self):
        import numpy as np
        from verdflow.geospatial_worker.actor_footprints import _highlight_mask

        y, x = np.ogrid[-64:65, -64:65]
        chroma = np.minimum(25, 10 + (x*x + y*y) // 200)
        fg = (abs(x) < 60) & (abs(y) < 48) & ~((x > 17) & (x < 30) & (y > 5) & (y < 16))
        base = np.full((3, *fg.shape), 20, dtype="int16")
        m, c = base.copy(), base.copy()
        m[:, fg] = c[:, fg] = 255
        m[1, fg] -= chroma[fg]
        c[0, fg] -= chroma[fg] - 2
        actual = _highlight_mask(m, c, base)
        np.testing.assert_array_equal(actual, fg)


class ActorStitchTests(unittest.TestCase):
    def test_stitch_only_edge_connected_parts_matches_global_union(self):
        import numpy as np
        from shapely import union_all
        from verdflow.geospatial_worker.actor_footprints import _CORE, _polygons, _tile_parts, _whole_components

        n = _CORE
        mask = np.zeros((2 * n, 2 * n), dtype=bool)
        # Ring spanning four cores, independent island inside the hole.
        mask[n-8:n+8, n-8:n+8] = True
        mask[n-5:n+5, n-5:n+5] = False
        mask[n-2:n+2, n-2:n+2] = True
        # One run meets two runs; the last island touches only a corner.
        mask[20:27, n-3:n] = True
        mask[20:22, n:n+3] = mask[25:27, n:n+3] = True
        mask[27:30, n+3:n+6] = True
        mask[50:90:2, 60:120:3] = True
        parts = [p for row in range(2) for col in range(2)
                 for p in _tile_parts(mask[row*n:(row+1)*n, col*n:(col+1)*n],
                                      (row, col), (), True, keep_all=True)]
        expected = list(_polygons(union_all([p.geometry for p in parts])))
        with patch("shapely.union_all", wraps=union_all) as merge:
            actual = list(_whole_components(parts))
        self.assertEqual(sorted(len(call.args[0]) for call in merge.call_args_list), [3, 4, 4])
        self.assertEqual(len(actual), len(expected))
        self.assertTrue(all(p.is_valid for p in actual))
        self.assertEqual(sum(len(p.interiors) for p in actual), 1)
        self.assertTrue(union_all(actual).equals(union_all(expected)))

    def test_endpoint_contact_across_seam_does_not_need_union(self):
        from shapely.geometry import box
        from verdflow.geospatial_worker.actor_footprints import _Part, _whole_components

        a, b = box(-3, -2, 0, 1), box(0, 1, 2, 4)
        parts = [_Part((0, -1), False, ((), ((-2, 1),), (), ()), a),
                 _Part((0, 0), False, (((1, 4),), (), (), ()), b)]
        with patch("shapely.union_all", side_effect=AssertionError):
            self.assertEqual(list(_whole_components([])), [])
            self.assertEqual(list(_whole_components(parts)), [a, b])

    def test_irregular_raster_stitch_matches_each_reference_polygon(self):
        import numpy as np
        from shapely import union_all
        from verdflow.geospatial_worker import actor_footprints as af

        rng = np.random.default_rng(379)
        with patch.object(af, "_CORE", 16):
            for density in (0.15, 0.5, 0.85):
                mask = rng.random((48, 64)) < density
                parts = [p for r in range(3) for c in range(4)
                         for p in af._tile_parts(mask[16*r:16*r+16, 16*c:16*c+16], (r-1, c-2), (),
                                                 True, keep_all=True)]
                wanted = list(af._polygons(union_all([p.geometry for p in parts])))
                actual = list(af._whole_components(parts))
                self.assertEqual(len(actual), len(wanted))
                by_bounds = {}
                for poly in wanted:
                    by_bounds.setdefault(poly.bounds, []).append(poly)
                for poly in actual:
                    self.assertTrue(any(poly.equals(other) for other in by_bounds[poly.bounds]))


class ActorCaptureTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.manager = DtsLiveConnectorManager(_resolve, draft_root=root)
        self.addCleanup(self.manager.close_all)
        self.selections = []
        self.server = self.enterContext(DtsLiveConnectorServer(self.manager, on_selection_ready=self.selections.append))
        self.path = "/api/v1/selections/actor-capture"

    def client(self):
        session = self.manager.create_session()
        status, _, headers = self.post("/api/v1/bootstrap/exchange", {
            "connector_session_id": session.connector_session_id, "bootstrap_nonce": session.bootstrap_nonce,
        })
        self.assertEqual(status, 200)
        self.session_id = session.connector_session_id
        return headers["Set-Cookie"].split(";", 1)[0]

    def post(self, path, document, cookie=None):
        return _request(self.server.base_url + path, document=document, cookie=cookie, origin=self.server.base_url)

    def reserve(self, points, owner, **options):
        status, result, _ = self.post(self.path, {"points": points, **options}, owner)
        self.assertEqual(status, 200, result)
        return {"capture_id": result["capture_id"]}

    def next_tile(self, body, frame, owner):
        status, result, _ = self.post(self.path + "/next", {**body, "frame": frame}, owner)
        self.assertEqual(status, 200, result)
        return result["tile"]

    def test_only_owner_can_access_tiles_and_discard(self):
        request = {"points": [[5000, 6000, 8]]}
        self.assertEqual(self.post(self.path, request)[0], 401)
        owner, other = self.client(), self.client()
        self.assertEqual(self.post(self.path, {**request, "path": "forbidden.png"}, owner)[0], 400)
        body = self.reserve(request["points"], owner)
        tile = self.next_tile(body, "magenta", owner)
        folder = Path(tile["path"]).parents[1]
        self.assertTrue(folder.is_dir())
        self.assertEqual(self.post(self.path + "/next", {**body, "frame": "magenta"}, other)[0], 400)
        self.assertEqual(self.post(self.path + "/resolve", body, other)[0], 400)
        self.assertEqual(self.post(self.path + "/frame", {**body, "frame": "magenta", "tile": 0}, other)[0], 400)
        self.assertEqual(self.post(self.path + "/frame", {**body, "frame": "../other", "tile": 0}, owner)[0], 400)
        self.assertEqual(self.post(self.path + "/next", {**body, "frame": "normal"}, owner)[0], 400)
        self.post(self.path + "/discard", body, other)
        self.assertTrue(folder.is_dir())
        self.assertEqual(self.post("/api/v1/session/close", {}, owner)[0], 200)
        self.assertFalse(folder.exists())

    def test_discard_removes_partial_output(self):
        owner = self.client()
        body = self.reserve([[0, 0, 1]], owner)
        path = Path(self.next_tile(body, "magenta", owner)["path"])
        path.write_bytes(b"incomplete")
        self.assertEqual(self.post(self.path + "/discard", body, owner)[0], 200)
        self.assertFalse(path.parents[1].exists())
        self.reserve([[0, 0, 1]], owner)

    def capture(self, points, mask_at, *, owner=None, expected_status=200, **options):
        try:
            import numpy as np
            import rasterio
            from rasterio.errors import NotGeoreferencedWarning
        except ImportError:
            self.skipTest("Optional geospatial runtime is unavailable")
        owner = owner or self.client()
        body = self.reserve(points, owner, **options)
        orders = {}
        for frame, rgb in {"magenta": (255, 216, 255), "cyan": (210, 251, 251), "normal": (46, 55, 51)}.items():
            orders[frame] = []
            while (tile := self.next_tile(body, frame, owner)) is not None:
                index, size = tile["tile"], tile["size"]
                path = Path(tile["path"])
                root = path.parents[1]
                orders[frame].append(tile["location"][:2])
                rows = (np.arange(size) + 0.5) * tile["width"] / size
                x, y = (origin + tile["width"] / 2 - rows for origin in tile["location"][:2])
                mask = mask_at(x[:, None], y[None, :], frame)
                pixels = np.full((3, size, size), 20, dtype=np.uint8)
                pixels[:, mask] = np.asarray(rgb)[:, None]

                def ready():
                    status, result, _ = self.post(self.path + "/frame", {**body, "frame": frame, "tile": index}, owner)
                    self.assertEqual(status, 200, result)
                    return result["ready"]

                self.assertFalse(ready())
                with catch_warnings():
                    simplefilter("ignore", NotGeoreferencedWarning)
                    with rasterio.open(path, "w", driver="PNG", dtype="uint8", width=size, height=size, count=3) as image:
                        image.write(pixels)
                if frame == "magenta" and index == 0:
                    contents = path.read_bytes()
                    path.write_bytes(contents[:-12])
                    self.assertFalse(ready())
                    path.write_bytes(contents)
                self.assertTrue(ready())
                self.assertTrue(ready())
                if frame == "normal":
                    self.assertFalse(path.parent.exists())
        self.assertFalse(list(root.rglob("*.png")))
        status, result, _ = self.post(self.path + "/resolve", body, owner)
        self.assertEqual(status, expected_status, result)
        self.assertFalse(root.exists())
        return orders, result

    def geometry(self, dataset, start, owner):
        return _request(self.server.base_url + f"/api/v1/selections/geometry?dataset={dataset}&start={start}",
                        method="GET", cookie=owner, origin=self.server.base_url)

    def test_small_components_use_one_tile_and_keep_only_pick(self):
        orders, result = self.capture([[0, 0, 8], [5, 5, 8], [24, 24, 8]], lambda x, y, frame:
            ((abs(x) < 20) & (abs(y) < 20)) | ((x > 30) & (x < 40) & (y > 30) & (y < 40)))
        self.assertEqual(orders, dict.fromkeys(("magenta", "cyan", "normal"), [[0, 0]]))
        self.assertEqual(result["missed"], [2])
        self.assertEqual(result["clipped"], [])
        match, = result["matches"]
        self.assertEqual(match["indices"], [0, 1])
        self.assertEqual(match["region"]["bbox"], [-20, -20, 8, 20, 20, 8])

    def test_whole_actor_includes_disconnected_regions_and_skips_empty_exact_tiles(self):
        owner = self.client()

        def mask(x, y, frame):
            near = (abs(x) < 20) & (y > -95) & (y < 20)
            hole = (abs(x) < 5) & (y > -85) & (y < -65)
            detached = (x > 20) & (x < 25) & (y > -345) & (y < -340)
            false_positive = (abs(x) < 6) & (y > -205) & (y < -200) if frame == "magenta" else False
            return (near & ~hole) | detached | false_positive

        orders, result = self.capture([[0, 0, 8]], mask, scope="actor",
                                      bounds=[[-20, -345, 7], [25, 20, 9]], owner=owner)
        self.assertEqual(len(orders["magenta"]), 3)
        self.assertEqual(orders["cyan"], orders["normal"])
        self.assertEqual(result["region_count"], 2)
        status, page, _ = self.geometry(result["dataset_id"], 0, owner)
        self.assertEqual(status, 200)
        regions = page["regions"]
        self.assertEqual(sorted(region["bbox"] for region in regions),
                         [[-20, -95, 8, 20, 20, 8], [20, -345, 8, 25, -340, 8]])
        self.assertEqual(sum(len(region["holes"]) for region in regions), 1)

        empty_orders, _ = self.capture([[0, 0, 8]], lambda x, y, _frame:
                                       ((abs(x) < 10) & (abs(y) < 10)) | ((abs(x) < 3) & (y > -345) & (y < -340)),
                                       scope="actor", bounds=[[-20, -345, 7], [25, 20, 9]])
        self.assertEqual(len(empty_orders["magenta"]), 3)
        self.assertEqual(len(empty_orders["cyan"]), 2)
        self.assertEqual(empty_orders["cyan"], empty_orders["normal"])

    def test_large_actor_registers_exact_geometry_in_pages_and_can_start_planting(self):
        from verdflow.geospatial_worker.actor_footprints import ActorFootprint, ActorFootprintTiles

        owner = self.client()
        session = self.session_id
        other = self.client()
        cx, cy = 500000.0, 3000000.0

        def ring(x, radius, size):
            return tuple((x + radius * math.cos(i * math.tau / size), cy + radius * math.sin(i * math.tau / size))
                         for i in range(size))

        first = ActorFootprint((0,), ring(cx, 20, 120001), (ring(cx, 5, 10001),))
        second = ActorFootprint((0,), ring(cx + 60, 20, 120001), ())
        with patch.object(ActorFootprintTiles, "finish", return_value=([first, second], [], [])):
            _, result = self.capture([[cx, cy, 8]], lambda x, y, _frame:
                                     (abs(x - cx) < 20) & (abs(y - cy) < 20),
                                     owner=owner, scope="actor", bounds=[[cx-20,cy-20,7], [cx+80,cy+20,9]])
        dataset = result["dataset_id"]
        self.assertEqual(set(result), {"dataset_id", "region_count"})
        self.assertEqual(result["region_count"], 2)
        self.assertEqual(self.geometry(dataset, 0, other)[0], 400)
        for ordinal, footprint in enumerate((first, second)):
            status, page, _ = self.geometry(dataset, ordinal, owner)
            self.assertEqual(status, 200)
            match, = page["regions"]
            self.assertEqual(match["ordinal"], ordinal)
            self.assertEqual(len(match["coordinates"]), len(footprint.coordinates))
            self.assertEqual(match["coordinates"][99], [*footprint.coordinates[99], 8])
            self.assertEqual([len(hole) for hole in match["holes"]], list(map(len, footprint.holes)))
        reference = {"dataset_id": dataset, "start": 0, "count": 2}
        status, selected, _ = self.post("/api/v1/operations/selection", {
            "strategy_package_hash": HASH, "profile_id": "profile", "regions": [reference],
        }, owner)
        self.assertEqual(status, 202, selected)
        port = self.manager.trusted_port(session)
        selection, = self.selections
        refs = selection.regions
        self.assertTrue(all(ref.dataset_id == dataset and not ref.coordinates for ref in refs))
        self.assertEqual(sum(item.vertex_count for item in refs), 250003)
        for region, footprint in zip(port.region_geometries(refs), (first, second), strict=True):
            self.assertEqual(len(region.coordinates), len(footprint.coordinates))
            self.assertEqual(region.coordinates[-1], (*footprint.coordinates[-1], 8))

    def test_whole_actor_requires_a_single_point_inside_the_reported_bounds(self):
        owner = self.client()
        for request in (
            {"points": [[0, 0, 8]]},
            {"points": [[0, 0, 8]], "bounds": [[10, 10, 7], [20, 20, 10]]},
            {"points": [[0, 0, 8], [0, 1, 8]], "bounds": [[-5, -5, 7], [5, 5, 10]]},
        ):
            self.assertEqual(self.post(self.path, {**request, "scope": "actor"}, owner)[0], 400)

    def test_drawn_range_skips_tiles_outside_concave_shape_and_preserves_holes(self):
        from shapely.geometry import Polygon

        owner = self.client()
        clip = [[20, -20, 8], [190, -20, 8], [190, -60, 8],
                [60, -60, 8], [60, -190, 8], [20, -190, 8]]

        def mask(x, y, _frame):
            visible = (x > -40) & (x < 220) & (y > -240) & (y < 40)
            hole = (x > 30) & (x < 40) & (y > -90) & (y < -80)
            return visible & ~hole

        orders, result = self.capture([[0, 0, 8]], mask, owner=owner, scope="actor", clip=clip,
                                      bounds=[[-250, -450, 7], [250, 450, 9]])
        self.assertEqual(orders["magenta"], [[150, 0], [0, 0], [0, -150]])
        self.assertEqual(orders["magenta"], orders["cyan"])
        self.assertEqual(orders["cyan"], orders["normal"])
        self.assertEqual(result["region_count"], 1)
        status, page, _ = self.geometry(result["dataset_id"], 0, owner)
        self.assertEqual(status, 200)
        region, = page["regions"]
        geometry = Polygon(region["coordinates"], region["holes"])
        expected = Polygon([point[:2] for point in clip], [[(30, -90), (40, -90), (40, -80), (30, -80)]])
        self.assertAlmostEqual(geometry.symmetric_difference(expected).area, 0)

    def test_invalid_or_disjoint_range_does_not_start_capture(self):
        owner = self.client()
        bounds = [[-20, -20, 7], [20, 20, 9]]
        crossed = [[-10,-10,8], [10,10,8], [-10,10,8], [10,-10,8]]
        disjoint = [[100,100,8], [110,100,8], [100,110,8]]
        for clip, message in ((crossed, "不自交"), (disjoint, "不相交")):
            status, result, _ = self.post(self.path, {"points": [[0,0,8]], "bounds": bounds,
                                                     "scope": "actor", "clip": clip}, owner)
            self.assertEqual(status, 400)
            self.assertIn(message, result["error"]["message"])
        self.reserve([[0,0,8]], owner, bounds=bounds, scope="actor")

    def test_whole_actor_does_not_return_parts_when_coverage_clips_the_highlight(self):
        _, result = self.capture([[0, 0, 8]], lambda x, y, _frame: (abs(x) < 10) & (abs(y) < 90),
                                 scope="actor", bounds=[[-10, -20, 7], [10, 20, 9]], expected_status=400)
        self.assertIn("轮廓超出", result["error"]["message"])

    def test_grows_beyond_old_width_and_rejoins_hole_across_tile_edge(self):
        def mask(x, y, frame):
            picked = (x > -20) & (x < 20) & (y > -1450) & (y < 25)
            hole = (abs(x) < 5) & (y > -85) & (y < -65)
            unpicked = (x > 25) & (x < 30) & (y > -1400) & (y < -50)
            bright_bridge = (x >= 20) & (x <= 25) & (y > -85) & (y < -65) if frame == "magenta" else False
            return (picked & ~hole) | unpicked | bright_bridge

        orders, result = self.capture([[0, 0, 8], [0, -1000, 8]], mask)
        self.assertGreater(len(orders["magenta"]), 9)
        self.assertEqual(orders["magenta"], orders["cyan"])
        self.assertEqual(orders["magenta"], orders["normal"])
        self.assertEqual(result["missed"], [])
        self.assertEqual(result["clipped"], [])
        match, = result["matches"]
        self.assertEqual(match["indices"], [0, 1])
        self.assertEqual(match["region"]["bbox"], [-20, -1450, 8, 20, 25, 8])
        hole, = match["region"]["holes"]
        self.assertEqual(min(point[1] for point in hole), -85)
        self.assertEqual(max(point[1] for point in hole), -65)
