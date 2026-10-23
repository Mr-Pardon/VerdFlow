from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    MaterialClassification,
    MaterialSelectionItem,
    MaterialSelectionMode,
    MaterialSelectionRequest,
)
from verdflow.core.material_selection import (  # noqa: E402
    MaterialSelectionService,
    load_material_facts,
    load_region_material_selection,
)


class MaterialSelectionTests(unittest.TestCase):
    def test_reads_product_projection_without_exposing_artifacts(self) -> None:
        store = _Store()
        facts = load_material_facts(store, "scene_1")

        self.assertEqual(facts.scene_revision_id, "scene_1")
        self.assertEqual(
            [item.material_name for item in facts.materials],
            ["Grass", "RoadGrass", "Concrete"],
        )
        self.assertEqual(facts.materials[0].face_count, 12)
        self.assertEqual(
            facts.materials[0].textures[0].source_relative_path,
            "textures/grass.png",
        )

    def test_finalizes_exact_rule_pack_in_fact_order(self) -> None:
        store = _Store()
        runtime = _Runtime()
        service = MaterialSelectionService(store, runtime)
        result = service.finalize(
            "scene_1",
            _selection(),
            selection_mode=MaterialSelectionMode.MANUAL,
        )

        self.assertIsNone(result.rule_pack["origin"]["candidate_job_id"])
        self.assertEqual(
            result.rule_pack["rules"][0]["match"]["material_names"],
            ["RoadGrass"],
        )
        self.assertEqual(
            result.rule_pack["rules"][1]["match"]["material_names"],
            ["Grass"],
        )
        self.assertEqual(runtime.promoted, [])

    def test_recovers_material_selection_from_region_provenance(self) -> None:
        selection = load_region_material_selection(
            _Store(),
            "scene_1",
            "regions_1",
        )

        self.assertEqual(
            [item.classification for item in selection.materials],
            [
                MaterialClassification.GENERAL_GREEN,
                MaterialClassification.STREET_TREE_GREEN,
                MaterialClassification.NOT_GREEN,
            ],
        )

    def test_rejects_incomplete_unknown_and_duplicate_selection(self) -> None:
        service = MaterialSelectionService(_Store(), _Runtime())
        invalid = (
            MaterialSelectionRequest(materials=_selection().materials[:-1]),
            MaterialSelectionRequest(
                materials=_selection().materials
                + (
                    MaterialSelectionItem(
                        "Unknown",
                        MaterialClassification.NOT_GREEN,
                    ),
                )
            ),
            MaterialSelectionRequest(
                materials=_selection().materials
                + (_selection().materials[0],)
            ),
        )
        for request in invalid:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    service.finalize(
                        "scene_1",
                        request,
                        selection_mode=MaterialSelectionMode.MANUAL,
                    )


def _selection() -> MaterialSelectionRequest:
    return MaterialSelectionRequest(
        materials=(
            MaterialSelectionItem(
                "Concrete",
                MaterialClassification.NOT_GREEN,
            ),
            MaterialSelectionItem(
                "Grass",
                MaterialClassification.GENERAL_GREEN,
            ),
            MaterialSelectionItem(
                "RoadGrass",
                MaterialClassification.STREET_TREE_GREEN,
            ),
        )
    )


class _Store:
    def __init__(self) -> None:
        self.documents = {
            "manifest": {
                "facts": {"material_fact_report_artifact_id": "facts"}
            },
            "facts": {
                "materials": [
                    {
                        "name": "Grass",
                        "face_count": 12,
                        "object_count": 1,
                        "objects": ["Ground"],
                        "diffuse_color": [0.1, 0.7, 0.2, 1.0],
                        "textures": [
                            {
                                "image": "grass.png",
                                "source_relative_path": "textures/grass.png",
                            }
                        ],
                    },
                    {
                        "name": "RoadGrass",
                        "face_count": 8,
                        "object_count": 1,
                        "objects": ["Median"],
                        "diffuse_color": [],
                        "textures": [],
                    },
                    {
                        "name": "Concrete",
                        "face_count": 20,
                        "object_count": 1,
                        "objects": ["Road"],
                        "diffuse_color": [],
                        "textures": [],
                    },
                ]
            },
            "regions": {
                "regions": [
                    {
                        "greenbelt_type": "general_greenbelt",
                        "source_materials": ["Grass"],
                    },
                    {
                        "greenbelt_type": "street_greenbelt",
                        "source_materials": ["RoadGrass"],
                    },
                ]
            },
        }

    def get_scene_revision(self, scene_revision_id: str):
        if scene_revision_id != "scene_1":
            raise KeyError(scene_revision_id)
        return SimpleNamespace(
            scene_revision_id=scene_revision_id,
            scene_manifest_artifact_id="manifest",
        )

    def get_region_set(self, region_set_id: str):
        if region_set_id != "regions_1":
            raise KeyError(region_set_id)
        return SimpleNamespace(
            scene_revision_id="scene_1",
            geometry_artifact_id="regions",
        )

    @contextmanager
    def open_artifact(self, artifact_id: str):
        yield BytesIO(json.dumps(self.documents[artifact_id]).encode())

    def put_bytes(self, data: bytes, **_kwargs):
        self.documents["rule_pack"] = json.loads(data)
        return SimpleNamespace(artifact_id="rule_pack")


class _Runtime:
    def __init__(self) -> None:
        self.promoted: list[str] = []

    def promote(self, job_id: str, **_kwargs) -> None:
        self.promoted.append(job_id)


if __name__ == "__main__":
    unittest.main()
