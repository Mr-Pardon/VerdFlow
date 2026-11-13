from __future__ import annotations

import copy
from contextlib import closing
from io import BytesIO
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    CoordinateReferenceKind,
    ELEVATION_SAMPLING_COMMAND_SCHEMA,
    JobCommandRef,
    PointSetKind,
    RegionSummary,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    ElevationProviderDefinition,
    ElevationSamplingRequest,
    ElevationSamplingService,
    JobRuntime,
    PointSetImportError,
    PointSetImportService,
)
from verdflow.project_store import ProjectStore  # noqa: E402
from verdflow.planting import (  # noqa: E402
    PlantingDocumentError, PointSetReader, canonical_json_bytes, validated_point_rows,
    validate_elevation_row,
)
from verdflow.planting.validation import (  # noqa: E402
    _generated_row_check, _point_row_validator, validate_point_row,
)


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class PointSetImportServiceTests(unittest.TestCase):
    def test_point_stream_reads_bounded_chunks_and_validates_after_rows(self) -> None:
        class BoundedRead(BytesIO):
            def read(self, size: int = -1) -> bytes:
                if not 0 <= size <= 65536:
                    raise AssertionError("PointSet reader attempted an unbounded read")
                return super().read(size)

        document = self._generated_points()
        row = document["points"][0]
        document["points"] = [{**row, "id": f"p{i}", "generated_position": [-0.0, i, 0.0]} for i in range(8192)]
        document["point_count"] = 8192
        encoded = json.dumps(document, sort_keys=True).encode()
        reader = PointSetReader(BoundedRead(encoded), expected_point_count=8192)
        with self.assertRaises(RuntimeError):
            _ = reader.header
        self.assertEqual(sum(point["id"] == f"p{i}" for i, point in enumerate(reader.rows())), 8192)
        self.assertEqual(reader.header, {key: value for key, value in document.items() if key != "points"})

    def test_point_stream_rejects_invalid_suffix_or_count(self) -> None:
        encoded = json.dumps(self._generated_points(), sort_keys=True).encode()
        malformed = (
            encoded + b"{}",
            encoded[:-1],
            encoded.replace(b'"point_count": 2', b'"point_count": 3'),
            encoded.replace(b'"version": "2"', b'"version": "2", "version": "2"'),
        )
        for payload in malformed:
            with self.subTest(tail=payload[-38:]), self.assertRaises(PlantingDocumentError):
                list(PointSetReader(BytesIO(payload), expected_point_count=2).rows())
        with self.assertRaisesRegex(PlantingDocumentError, "Core record"):
            list(PointSetReader(BytesIO(encoded), expected_point_count=1).rows())

    def test_point_stream_row_separators_accept_whitespace_but_reject_trailing_comma(self) -> None:
        document = self._generated_points()
        for separators in ((",", ":"), (", ", ": "), (" ,\r\n\t ", ": ")):
            encoded = json.dumps(document, separators=separators).encode()
            self.assertEqual(list(PointSetReader(BytesIO(encoded)).rows()), document["points"])
            for tail in (b",]", b", \r\n ]", b"  ,\t]"):
                closing = encoded.index(b"}]", encoded.index(b'"points"')) + 1
                invalid = encoded[:closing] + tail + encoded[closing + 1:]
                with self.assertRaises(PlantingDocumentError):
                    list(PointSetReader(BytesIO(invalid)).rows())

    def test_point_stream_handles_split_tokens_and_legacy_values(self) -> None:
        class ShortReads(BytesIO):
            def read(self, size: int = -1) -> bytes:
                return super().read(min(size, 71))

        document = self._generated_points()
        row = document["points"][0]
        document["points"] = [
            {**row, "id": f"p{i}", "attributes": {"label": "\u6811\U0001f331", "z": -0.0}}
            for i in range(120)
        ]
        document["point_count"] = len(document["points"])
        for ensure_ascii in (False, True):
            raw = json.dumps(document, ensure_ascii=ensure_ascii).encode("utf-8")
            reader = PointSetReader(ShortReads(raw))
            self.assertEqual(list(reader.rows()), document["points"])
            self.assertEqual(reader.header["point_count"], len(document["points"]))

        document["points"][70]["attributes"]["large"] = "x" * (150 * 1024)
        reader = PointSetReader(BytesIO(json.dumps(document, ensure_ascii=False).encode("utf-8")))
        with patch.object(reader, "_read_events", side_effect=AssertionError("unexpected whole-document fallback")):
            self.assertEqual(list(reader.rows()), document["points"])

        document["points"][70]["attributes"]["large"] = "x" * (512 * 1024)
        reader = PointSetReader(BytesIO(json.dumps(document, ensure_ascii=False).encode("utf-8")))
        self.assertEqual(list(reader.rows()), document["points"])

        del document["points"][70]["attributes"]["large"]
        raw = json.dumps(document, ensure_ascii=False).encode("utf-8")
        split = raw.index(b'"point_count": 120') + len(b'"point_count": 1')

        class SplitCount(BytesIO):
            def read(self, size: int = -1) -> bytes:
                return super().read(min(size, split) if self.tell() == 0 else size)

        reader = PointSetReader(SplitCount(raw))
        self.assertEqual(sum(1 for _ in reader.rows()), 120)
        self.assertEqual(reader.header["point_count"], 120)

    def test_point_stream_rejects_duplicate_nested_fields_and_oversized_depth(self) -> None:
        document = self._generated_points()
        document["points"][1]["attributes"] = {"marker": [0]}
        raw = json.dumps(document).encode()
        for payload, message in (
            (raw.replace(b'"marker": [0]', b'"marker": [0], "marker": []'), "duplicate"),
            (raw.replace(b'"marker": [0]', b'"marker": ' + b"[" * 64 + b"0" + b"]" * 64), "budget"),
            (raw.replace(b'"marker": [0]', b'"marker": [NaN]'), "invalid JSON"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(PlantingDocumentError, message):
                list(PointSetReader(BytesIO(payload)).rows())

    def test_point_stream_keeps_fast_reader_after_a_bounded_escaped_value(self) -> None:
        document = self._generated_points()
        document["points"][0]["attributes"] = {"label": "\u6811\U0001f331"}
        reader = PointSetReader(BytesIO(json.dumps(document, ensure_ascii=True).encode()))
        with patch.object(reader, "_read_events", side_effect=AssertionError("unexpected whole-document fallback")):
            self.assertEqual(list(reader.rows()), document["points"])
        self.assertEqual(reader.header["point_count"], document["point_count"])

    def test_elevation_validation_rejects_boolean_and_nonfinite_vectors(self) -> None:
        original = self._generated_points()["points"][0]
        for field in ("generated_position", "rotation_quaternion_xyzw", "resolved_position", "position", "normal"):
            for bad in (True, float("inf"), 10**400):
                point = self._sampled_points()["points"][0]
                vector = point[field] if field in point else point["terrain_sample"][field]
                vector[0] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(PlantingDocumentError):
                    validate_elevation_row(original, point, index=0)
        point = self._sampled_points()["points"][0]
        point["generated_position"][0] = True  # Equal to the accepted parent value 1.0.
        with self.assertRaisesRegex(PlantingDocumentError, "generated_position"):
            validate_elevation_row(original, point, index=0)

    def test_generated_row_fast_check_matches_full_validation_for_v2_and_v3(self) -> None:
        for version, input_key in (("2", "region_id"), ("3", "path_id")):
            row = self._generated_points()["points"][0]
            row[input_key] = row.pop("region_id")
            cases = [row, {**row, "group_id": "", "size_intent": {"custom": [1, True]}}]
            cases.extend({key: value for key, value in row.items() if key != missing} for missing in row)
            cases.extend({**row, field: value} for field, value in (
                ("extra", 1), ("id", ""), (input_key, 1), ("role_key", "Wrong!"), ("role_key", "a\n"),
                ("group_id", False), ("size_intent", []), ("terrain_sample", {"hit": False}),
                ("generated_position", [True, 2, 3]), ("resolved_position", [1, 2]),
                ("resolved_position", [1, 2, 4]), ("rotation_quaternion_xyzw", [0, 0, 0, 0]),
                ("rotation_quaternion_xyzw", [0, 0, 0, float("inf")]),
                ("rotation_quaternion_xyzw", [0, 0, 0, 10**400]),
            ))
            self.assertIsNotNone(_generated_row_check(version))

            def accepted(candidate):
                try:
                    validate_point_row(candidate, version=version, kind="generated", index=0)
                except PlantingDocumentError:
                    return False
                return True

            for index, candidate in enumerate(cases):
                with self.subTest(version=version, case=index):
                    with patch("verdflow.planting.validation._generated_row_check", return_value=None):
                        expected = accepted(candidate)
                    self.assertEqual(accepted(candidate), expected)

    def test_generated_row_fast_check_defers_new_schema_constraints(self) -> None:
        for rule in (False, {"type": "string", "minLength": 2}):
            validator = _point_row_validator("2")
            schema = copy.deepcopy(validator.schema)
            schema["properties"]["group_id"] = rule
            with patch("verdflow.planting.validation._point_row_validator", return_value=validator.evolve(schema=schema)):
                self.assertIsNone(_generated_row_check.__wrapped__("2"))

    def test_elevation_validation_preserves_hit_and_immutable_field_checks(self) -> None:
        original = self._generated_points()["points"][0]
        for field in ("position", "normal", "target"):
            point = self._sampled_points()["points"][0]
            del point["terrain_sample"][field]
            with self.subTest(field=field), self.assertRaisesRegex(PlantingDocumentError, "requires position"):
                validate_elevation_row(original, point, index=0)

        point = self._sampled_points()["points"][0]
        original["attributes"] = {"source": "original"}
        point["attributes"] = dict(original["attributes"])
        validate_elevation_row(original, point, index=0)
        point["attributes"]["source"] = "changed"
        with self.assertRaisesRegex(PlantingDocumentError, "immutable planting fields"):
            validate_elevation_row(original, point, index=0)

    def test_point_id_verification_checks_ids_across_memory_and_disk(self) -> None:
        template = self._generated_points()["points"][0]
        rows = [{**template, "id": key} for key in ("cafe\u0301", "p2", "p3", "caf\u00e9")]
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            scratch = Path(directory) / "ids.sqlite"
            with patch("verdflow.planting.point_stream._ID_MEMORY_COUNT", 2):
                with self.assertRaisesRegex(PlantingDocumentError, "duplicate id"):
                    list(validated_point_rows(rows, version="2", kind="generated", scratch=scratch))
            self.assertFalse(scratch.exists())

    @unittest.skipUnless(os.name == "nt", "Windows SQLite path budget")
    def test_point_id_spill_works_in_a_nested_windows_project(self) -> None:
        template = self._generated_points()["points"][0]
        rows = [{**template, "id": key} for key in ("p1", "p2", "p3")]
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory).resolve()
            scratch_root = root / ("a" * 120) / ("b" * max(1, 244 - len(str(root)) - 122))
            scratch_root.mkdir(parents=True)
            scratch = scratch_root / "long-point-index.sqlite"
            self.assertGreater(len(str(scratch)), 260)
            with patch("verdflow.planting.point_stream._ID_MEMORY_COUNT", 2):
                with closing(validated_point_rows(rows, version="2", kind="generated", scratch=scratch)) as verified:
                    self.assertEqual([next(verified) for _ in rows], rows)
            self.assertEqual(list(scratch_root.iterdir()), [])

    def test_generated_role_filter_preserves_canonical_content(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(Path(directory) / "filtered.verdflow", "Filtered") as store:
                scene, region_set = self._scene_and_regions(store)
                payload = self._generated_points()
                excluded = payload["points"][1]
                excluded["role_key"] = "excluded"
                excluded["region_id"] = "outside-selected-region"
                job_id = self._complete_point_job(
                    store,
                    command_type="verdflow.planting.generate",
                    command={
                        "scene_revision_id": scene, "region_set_id": region_set,
                        "selected_region_ids": ["region_1"], "excluded_role_keys": ["excluded"],
                        "strategy": {"id": "test-grove", "version": "1.2.0", "package_hash": "sha256:test"},
                        "random_seed": 1,
                    },
                    result_kind="planting_generation", points=payload, producer_id="test-generator",
                )
                finalized = PointSetImportService(store).finalize_generated(job_id)
                expected = {**payload, "points": payload["points"][:1], "point_count": 1}
                self.assertEqual(finalized.point_count, 1)
                with store.open_artifact(finalized.point_artifact_id) as result:
                    self.assertEqual(result.read(), canonical_json_bytes(expected))
                self.assertEqual(list((store.root / "runtime").glob("point-verify-*")), [])

    def test_submits_provider_neutral_elevation_command_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "elevation-command.verdflow",
                "Elevation command",
            ) as store:
                scene_id, region_set_id = self._scene_and_regions(store)
                generated_job = self._complete_point_job(
                    store,
                    command_type="verdflow.planting.generate",
                    command={
                        "scene_revision_id": scene_id,
                        "region_set_id": region_set_id,
                        "selected_region_ids": ["region_1"],
                        "strategy": {
                            "id": "test-grove",
                            "version": "1.2.0",
                            "package_hash": "sha256:strategy",
                        },
                        "random_seed": 7,
                    },
                    result_kind="planting_generation",
                    points=self._generated_points(),
                    producer_id="test-generator",
                )
                generated = PointSetImportService(store).finalize_generated(
                    generated_job
                )
                runtime = JobRuntime(store)
                provider = ElevationProviderDefinition(
                    provider_id="test-elevation-provider",
                    provider_version="3",
                    command_type="verdflow.test.sample-elevation",
                    command_version="1",
                    max_attempts=2,
                )
                service = ElevationSamplingService(
                    store,
                    runtime,
                    (provider,),
                )
                request = ElevationSamplingRequest(
                    parent_point_set_id=generated.point_set_id,
                    provider_id=provider.provider_id,
                    options={"ray_distance": 750.0},
                )

                first = service.submit(request)
                repeated = service.submit(request)

                self.assertEqual(first.job_id, repeated.job_id)
                self.assertEqual(
                    first.command.command_type,
                    "verdflow.test.sample-elevation",
                )
                with store.open_artifact(first.command.artifact_id) as stream:
                    command = json.load(stream)
                self.assertEqual(
                    command["schema"],
                    ELEVATION_SAMPLING_COMMAND_SCHEMA,
                )
                self.assertEqual(
                    command["parent_point_set_id"],
                    generated.point_set_id,
                )
                self.assertEqual(
                    command["points_artifact_id"],
                    generated.point_artifact_id,
                )
                self.assertEqual(
                    command["provider"],
                    {"id": "test-elevation-provider", "version": "3"},
                )
                self.assertEqual(command["options"], {"ray_distance": 750.0})
                self.assertFalse(
                    any("path" in key.lower() for key in command)
                )
                with self.assertRaisesRegex(ValueError, "not registered"):
                    service.submit(
                        ElevationSamplingRequest(
                            generated.point_set_id,
                            "missing-provider",
                        )
                    )
                with self.assertRaisesRegex(ValueError, "local path"):
                    service.submit(
                        ElevationSamplingRequest(
                            generated.point_set_id,
                            provider.provider_id,
                            {"nested": {"terrain_path": "C:/scene.fbx"}},
                        )
                    )

    def test_finalizes_generated_point_set_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "generated.verdflow",
                "Generated points",
            ) as store:
                scene_id, region_set_id = self._scene_and_regions(store)
                job_id = self._complete_point_job(
                    store,
                    command_type="verdflow.planting.generate",
                    command={
                        "scene_revision_id": scene_id,
                        "region_set_id": region_set_id,
                        "selected_region_ids": ["region_1"],
                        "strategy": {
                            "id": "test-grove",
                            "version": "1.2.0",
                            "package_hash": "sha256:strategy",
                        },
                        "random_seed": 42,
                    },
                    result_kind="planting_generation",
                    points=self._generated_points(),
                    producer_id="test-generator",
                )
                service = PointSetImportService(store)

                first = service.finalize_generated(job_id)
                second = service.finalize_generated(job_id)
                run = store.get_planting_run(first.planting_run_id)
                roles = store.list_point_set_roles(first.point_set_id)

                self.assertEqual(first.point_set_id, second.point_set_id)
                self.assertEqual(first.kind, PointSetKind.GENERATED)
                self.assertEqual(first.point_count, 2)
                self.assertEqual(first.min_x, 1.0)
                self.assertEqual(first.max_z, 4.0)
                self.assertEqual(run.strategy_id, "test-grove")
                self.assertEqual(run.random_seed, 42)
                self.assertEqual(
                    [(role.role_key, role.point_count) for role in roles],
                    [("canopy_tree", 1), ("understory_shrub", 1)],
                )

    def test_finalizes_elevation_as_child_without_mutating_parent(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "elevation.verdflow",
                "Elevation points",
            ) as store:
                scene_id, region_set_id = self._scene_and_regions(store)
                generated_job = self._complete_point_job(
                    store,
                    command_type="verdflow.planting.generate",
                    command={
                        "scene_revision_id": scene_id,
                        "region_set_id": region_set_id,
                        "selected_region_ids": ["region_1"],
                        "strategy": {
                            "id": "test-grove",
                            "version": "1.2.0",
                            "package_hash": "sha256:strategy",
                        },
                        "random_seed": 7,
                    },
                    result_kind="planting_generation",
                    points=self._generated_points(),
                    producer_id="test-generator",
                )
                service = PointSetImportService(store)
                generated = service.finalize_generated(generated_job)
                sampled_points = self._sampled_points()
                elevation_job = self._complete_point_job(
                    store,
                    command_type="verdflow.blender.sample-elevation",
                    command={
                        "scene_revision_id": scene_id,
                        "parent_point_set_id": generated.point_set_id,
                    },
                    result_kind="elevation_sampling",
                    points=sampled_points,
                    producer_id="test-elevation",
                )

                sampled = service.finalize_elevation(elevation_job)
                repeated = service.finalize_elevation(elevation_job)
                versions = store.list_point_sets(generated.planting_run_id)

                self.assertEqual(sampled.point_set_id, repeated.point_set_id)
                self.assertEqual(
                    sampled.parent_point_set_id,
                    generated.point_set_id,
                )
                self.assertEqual(sampled.hit_count, 1)
                self.assertEqual(sampled.miss_count, 1)
                self.assertEqual(sampled.min_z, 4.0)
                self.assertEqual(sampled.max_z, 8.0)
                self.assertEqual(len(versions), 2)
                self.assertEqual(
                    store.get_point_set(generated.point_set_id),
                    generated,
                )

    def test_rejects_shifted_generated_points(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "shifted.verdflow",
                "Shifted points",
            ) as store:
                scene_id, region_set_id = self._scene_and_regions(store)
                payload = self._generated_points()
                payload["offset_applied"] = True
                job_id = self._complete_point_job(
                    store,
                    command_type="verdflow.planting.generate",
                    command={
                        "scene_revision_id": scene_id,
                        "region_set_id": region_set_id,
                        "selected_region_ids": ["region_1"],
                        "strategy": {
                            "id": "test-grove",
                            "version": "1.2.0",
                            "package_hash": "sha256:test",
                        },
                        "random_seed": 1,
                    },
                    result_kind="planting_generation",
                    points=payload,
                    producer_id="test-generator",
                )

                with self.assertRaisesRegex(
                    PointSetImportError,
                    "unshifted coordinates",
                ):
                    PointSetImportService(store).finalize_generated(job_id)

    def test_rejects_elevation_that_changes_role_allocation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            with ProjectStore.create(
                Path(directory) / "lineage.verdflow",
                "Point lineage",
            ) as store:
                scene_id, region_set_id = self._scene_and_regions(store)
                generated_job = self._complete_point_job(
                    store,
                    command_type="verdflow.planting.generate",
                    command={
                        "scene_revision_id": scene_id,
                        "region_set_id": region_set_id,
                        "selected_region_ids": ["region_1"],
                        "strategy": {
                            "id": "test-grove",
                            "version": "1.2.0",
                            "package_hash": "sha256:test",
                        },
                        "random_seed": 1,
                    },
                    result_kind="planting_generation",
                    points=self._generated_points(),
                    producer_id="test-generator",
                )
                service = PointSetImportService(store)
                generated = service.finalize_generated(generated_job)
                sampled = self._sampled_points()
                sampled["points"][0]["role_key"] = "changed_role"
                elevation_job = self._complete_point_job(
                    store,
                    command_type="verdflow.blender.sample-elevation",
                    command={
                        "scene_revision_id": scene_id,
                        "parent_point_set_id": generated.point_set_id,
                    },
                    result_kind="elevation_sampling",
                    points=sampled,
                    producer_id="test-elevation",
                )

                with self.assertRaisesRegex(
                    PointSetImportError,
                    "immutable planting fields",
                ):
                    service.finalize_elevation(elevation_job)

    @staticmethod
    def _scene_and_regions(store: ProjectStore) -> tuple[str, str]:
        source_artifact = store.put_bytes(
            b"fake fbx",
            media_type="model/vnd.autodesk.fbx",
        )
        source = store.register_source(
            kind=SourceKind.FBX,
            original_name="scene.fbx",
            artifact_id=source_artifact.artifact_id,
        )
        frame = store.create_coordinate_frame(
            name="Source scene world",
            handedness="right-handed",
            up_axis="+Z",
            linear_unit="meter",
            crs_kind=CoordinateReferenceKind.UNKNOWN,
        )
        manifest = store.put_bytes(
            b'{"schema":"verdflow.scene-manifest","version":"1"}',
            media_type="application/json",
        )
        scene = store.create_scene_revision(
            source_id=source.source_id,
            importer_id="test-importer",
            importer_version="1",
            coordinate_frame_id=frame.coordinate_frame_id,
            scene_manifest_artifact_id=manifest.artifact_id,
        )
        command = store.put_bytes(
            b'{"operation":"fixture-region"}',
            media_type="application/vnd.verdflow.command+json",
        )
        runtime = JobRuntime(store)
        job = runtime.submit(
            JobCommandRef(
                command_type="test.region.fixture",
                command_version="1",
                artifact_id=command.artifact_id,
            ),
            idempotency_key="point-test-region",
        )
        claimed = runtime.claim_next(
            "fixture-worker",
            command_types=("test.region.fixture",),
        )
        assert claimed is not None
        result = store.put_bytes(
            b'{"result_kind":"region_fixture"}',
            media_type="application/json",
        )
        runtime.succeed(
            claimed.lease,
            result_artifact_id=result.artifact_id,
        )
        geometry = store.put_bytes(
            b'{"regions":[]}',
            media_type="application/vnd.verdflow.regions+json",
        )
        metrics = store.put_bytes(
            b'{"regions":[]}',
            media_type="application/json",
        )
        region_set = store.create_region_set(
            scene_revision_id=scene.scene_revision_id,
            producer_job_id=job.job_id,
            extractor_id="test-extractor",
            extractor_version="1",
            input_hash="sha256:regions",
            coordinate_frame_id=frame.coordinate_frame_id,
            geometry_artifact_id=geometry.artifact_id,
            metrics_artifact_id=metrics.artifact_id,
            regions=(
                RegionSummary(
                    region_id="region_1",
                    ordinal=0,
                    label="Test region",
                    tags="",
                    classification="ordinary",
                    strategy_hint=None,
                    footprint_area_xy=100.0,
                    boundary_perimeter_xy=40.0,
                    min_x=0.0,
                    min_y=0.0,
                    min_z=0.0,
                    max_x=10.0,
                    max_y=10.0,
                    max_z=5.0,
                    metrics_json="{}",
                ),
            ),
        )
        return scene.scene_revision_id, region_set.region_set_id

    @staticmethod
    def _complete_point_job(
        store: ProjectStore,
        *,
        command_type: str,
        command: dict[str, object],
        result_kind: str,
        points: dict[str, object],
        producer_id: str,
    ) -> str:
        command_artifact = store.put_bytes(
            json.dumps(command, sort_keys=True).encode("utf-8"),
            media_type="application/vnd.verdflow.command+json",
        )
        runtime = JobRuntime(store)
        job = runtime.submit(
            JobCommandRef(
                command_type=command_type,
                command_version="1",
                artifact_id=command_artifact.artifact_id,
            ),
            idempotency_key=f"{command_type}-{store.artifact_count()}",
        )
        claimed = runtime.claim_next(
            "point-worker",
            command_types=(command_type,),
        )
        assert claimed is not None
        document = copy.deepcopy(points)
        strategy = command.get("strategy")
        if isinstance(strategy, dict):
            document["strategy"] = {
                "strategy_id": strategy["id"],
                "strategy_version": strategy["version"],
            }
        point_artifact = store.put_bytes(
            json.dumps(document, sort_keys=True).encode("utf-8"),
            media_type="application/vnd.verdflow.points+json",
            schema_id="verdflow.points",
            schema_version="2",
        )
        package_artifact = store.put_bytes(
            b"fixture strategy package",
            media_type="application/vnd.verdflow.strategy-package+zip",
            schema_id="verdflow.strategy-package",
            schema_version="1",
        )
        result = store.put_bytes(
            json.dumps(
                {
                    "result_kind": result_kind,
                    "producer": {
                        "id": producer_id,
                        "version": "1",
                    },
                    "output_artifacts": [
                        {
                            "role": "points",
                            "artifact_id": point_artifact.artifact_id,
                        },
                        {
                            "role": "strategy_package",
                            "artifact_id": package_artifact.artifact_id,
                        },
                    ],
                },
                sort_keys=True,
            ).encode("utf-8"),
            media_type="application/json",
        )
        runtime.succeed(
            claimed.lease,
            result_artifact_id=result.artifact_id,
        )
        return job.job_id

    @staticmethod
    def _generated_points() -> dict[str, object]:
        return {
            "schema": "verdflow.points",
            "version": "2",
            "strategy": {
                "strategy_id": "test-grove",
                "strategy_version": "1.2.0",
            },
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "kind": "generated",
            "resolution_policy": "generated_position",
            "point_count": 2,
            "points": [
                {
                    "id": "point_1",
                    "region_id": "region_1",
                    "role_key": "canopy_tree",
                    "generated_position": [1.0, 2.0, 3.0],
                    "terrain_sample": None,
                    "resolved_position": [1.0, 2.0, 3.0],
                    "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
                },
                {
                    "id": "point_2",
                    "region_id": "region_1",
                    "role_key": "understory_shrub",
                    "generated_position": [5.0, 6.0, 4.0],
                    "terrain_sample": None,
                    "resolved_position": [5.0, 6.0, 4.0],
                    "rotation_quaternion_xyzw": [0.0, 0.0, 0.5, 0.866],
                },
            ],
        }

    @classmethod
    def _sampled_points(cls) -> dict[str, object]:
        payload = copy.deepcopy(cls._generated_points())
        payload["kind"] = "elevation_resolved"
        payload["resolution_policy"] = "sample_hit_else_generated"
        first = payload["points"][0]
        first["terrain_sample"] = {
            "hit": True,
            "position": [1.0, 2.0, 8.0],
            "normal": [0.0, 0.0, 1.0],
            "target": "terrain",
        }
        first["resolved_position"] = [1.0, 2.0, 8.0]
        second = payload["points"][1]
        second["terrain_sample"] = {"hit": False}
        return payload


if __name__ == "__main__":
    unittest.main()
