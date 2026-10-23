from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(p)
    for group in ("packages", "apps", "adapters")
    for p in (ROOT / group).glob("*/src")
]

from verdflow.contracts import (
    AssignmentPreviewCandidate,
    AssignmentPreviewRequest,
    AssignmentPreviewRole,
)
from verdflow.core import AssignmentPreviewService
from verdflow.local_api import LocalApiConfig, LocalApiServer
from verdflow.planting import (
    PlantingDocumentError,
    build_effective_binding_from_profile,
    resolve_asset_assignments,
)
from verdflow.system_host import SystemHost, SystemHostConfig
from test_planting_phase2a import _points, _profile, _strategy


class AssignmentPreviewTests(unittest.TestCase):
    def test_matches_formal_weights_scales_and_point_identity_without_geometry(self):
        profile = _profile()
        strategy = _strategy()
        for role in strategy["roles"]:
            role["assignment_policy"] = "weighted_pool"
        for assignment in profile["assignments"].values():
            first = assignment["candidates"][0]
            first["weight"] = 0.3
            first["scale"] = {"mode": "uniform_random", "min": 0.7, "max": 1.5}
            second = copy.deepcopy(first)
            second["asset_id"] += "_other"
            second["weight"] = 0.7
            second["scale"] = {"mode": "fixed", "value": 1.2}
            assignment["candidates"].append(second)
        points = _points()
        points["points"] = [
            {**copy.deepcopy(points["points"][i % 2]), "id": f"point-{i}"}
            for i in range(150)
        ]
        points["point_count"] = len(points["points"])
        roles = tuple(
            AssignmentPreviewRole(
                key,
                tuple(
                    AssignmentPreviewCandidate(
                        c["asset_id"],
                        c["weight"],
                        c["scale"]["mode"],
                        c["scale"].get("value", c["scale"].get("min")),
                        c["scale"].get("value", c["scale"].get("max")),
                    )
                    for c in value["candidates"]
                ),
            )
            for key, value in profile["assignments"].items()
        )
        request = AssignmentPreviewRequest(
            17, tuple((p["id"], p["role_key"]) for p in points["points"]), roles
        )
        preview = AssignmentPreviewService.resolve(request)
        formal = resolve_asset_assignments(
            points,
            build_effective_binding_from_profile(strategy, profile),
            random_seed=17,
        )
        by_role = {r.role_key: r.candidates for r in roles}
        for (_, key), (index, scale), actual in zip(
            request.points, preview.choices, formal["assignments"], strict=True
        ):
            self.assertEqual(by_role[key][index].asset_id, actual["asset_id"])
            self.assertEqual(scale, actual["target_scale_xyz"][0])
        self.assertEqual(preview, AssignmentPreviewService.resolve(request))
        self.assertEqual(
            AssignmentPreviewService.resolve(replace(request, roles=())).choices,
            tuple((-1, 1.0) for _ in request.points),
        )

    def test_bounded_input_and_nonfinite_data_are_rejected(self):
        candidate = AssignmentPreviewCandidate("asset", 1, "fixed", 1, 1)
        request = AssignmentPreviewRequest(
            17, (("p", "r"),), (AssignmentPreviewRole("r", (candidate,)),)
        )
        for bad in (
            replace(request, random_seed=True),
            replace(request, points=(("p", "r"), ("p", "r"))),
            replace(
                request,
                roles=(
                    AssignmentPreviewRole(
                        "r", (replace(candidate, weight=float("nan")),)
                    ),
                ),
            ),
            replace(
                request,
                roles=(AssignmentPreviewRole("r", (replace(candidate, scale_min=0),)),),
            ),
        ):
            with self.assertRaises(PlantingDocumentError):
                AssignmentPreviewService.resolve(bad)

    def test_local_api_does_not_need_projects_strategies_or_asset_library(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            SystemHost(
                SystemHostConfig(library_path=Path(directory) / "library")
            ) as host,
        ):
            token = "assignment-preview-test-" + "x" * 32
            with LocalApiServer(host, LocalApiConfig(management_token=token)) as api:
                body = {
                    "random_seed": 17,
                    "points": [["p", "r"]],
                    "roles": [
                        {
                            "role_key": "r",
                            "candidates": [
                                {
                                    "asset_id": "a",
                                    "weight": 1,
                                    "scale": {"mode": "fixed", "value": 1.2},
                                }
                            ],
                        }
                    ],
                }

                def send(document, auth=True):
                    headers = {"Content-Type": "application/json"}
                    if auth:
                        headers["Authorization"] = "Bearer " + token
                    req = Request(
                        api.base_url + "/api/v1/asset-assignment-previews",
                        data=json.dumps(document).encode(),
                        headers=headers,
                    )
                    try:
                        with urlopen(req, timeout=5) as response:
                            return response.status, json.load(response)
                    except HTTPError as error:
                        return error.code, json.load(error)

                self.assertEqual(send(body, False)[0], 403)
                self.assertEqual(send(body), (200, {"choices": [[0, 1.2]]}))
                self.assertEqual(send({**body, "reference_radius_m": 3})[0], 400)
                self.assertEqual(
                    send(
                        {
                            **body,
                            "roles": [
                                {
                                    "role_key": "r",
                                    "candidates": [
                                        {
                                            **body["roles"][0]["candidates"][0],
                                            "reference_radius_m": 3,
                                        }
                                    ],
                                }
                            ],
                        }
                    )[0],
                    400,
                )
                self.assertEqual(host.list_projects(), ())
                self.assertEqual(host.list_strategies(), ())


if __name__ == "__main__":
    unittest.main()
