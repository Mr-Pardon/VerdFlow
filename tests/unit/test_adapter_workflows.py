from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.unit.test_geospatial_vertical import (
    _generated_point_set, _write_inputs, numpy, rasterio, from_origin,
)
from verdflow.contracts import (
    CapabilityRef, InputRecipe, ResolvedPlantWorkflow, SamplingBinding, SamplingSourceRef, SourceKind,
)
from verdflow.core import (
    ElevationProviderDefinition, ElevationSamplingRequest, ExportProviderDefinition,
    PlantRunExecutionRequest, PlantRunExecutionService, RegionSourceProviderDefinition, RegionSourceRequest,
)
from verdflow.core_host import AdapterWorkflowRegistry, CoreHost, CoreHostConfig, GeospatialExtensionFactory


INPUT = CapabilityRef("region_source", "vector", "1", "test.prepare", "1")
SAMPLER = CapabilityRef("elevation", "terrain", "1", "test.sample", "1")
OUTPUT = CapabilityRef("output", "target", "1", "test.export", "1")
RECIPE = InputRecipe("test.vector", "1", "test.adapter", INPUT, SAMPLER, ("polygon",),
                    "source", "test.terrain-config/1", "fail-nodata", ("test.importer",), "geotiff")


def registry(recipes=(RECIPE,), extra_inputs=()):
    return AdapterWorkflowRegistry(
        recipes=recipes,
        region_sources=(RegionSourceProviderDefinition("vector", "1", "test.prepare", "1"), *extra_inputs),
        region_extractors=(),
        samplers=(ElevationProviderDefinition("terrain", "1", "test.sample", "1",
                  sampling_source_kind="source", configuration_schema="test.terrain-config/1", failure_policy="fail-nodata"),),
        outputs=(ExportProviderDefinition("target", "1", "dts", "test.export", "1", "application/xml", "test.xml", "1"),),
    )


class AdapterWorkflowTests(unittest.TestCase):
    def test_two_recipes_share_one_sampler_and_cross_package_references(self):
        imagery = replace(RECIPE, recipe_id="test.imagery", adapter_id="another.adapter",
                          preparation=replace(INPUT, provider_id="imagery", command_type="test.imagery"), legacy_importer_ids=())
        value = registry((RECIPE, imagery), (RegionSourceProviderDefinition("imagery", "1", "test.imagery", "1"),))
        self.assertEqual(value.recipe_for_scene("test.importer").sampling, imagery.sampling)
        self.assertEqual(value.recipe_for_scene("unused", recipe_id="test.imagery"), imagery)
        self.assertEqual(value.output_for_target("dts"), OUTPUT)

    def test_registry_rejects_missing_version_duplicate_and_ambiguous_routes(self):
        for recipes in ((RECIPE, RECIPE), (replace(RECIPE, sampling=replace(SAMPLER, provider_version="2")),)):
            with self.assertRaises(ValueError):
                registry(recipes)
        value = registry((RECIPE, replace(RECIPE, recipe_id="test.other")))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            value.recipe_for_scene("test.importer")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            value.require(replace(OUTPUT, command_version="2"))
        with self.assertRaises(ValueError):
            value.output_for_target("unsupported")

    def test_terrain_slot_never_chooses_first_matching_source(self):
        store = SimpleNamespace(
            get_scene_revision=lambda _: SimpleNamespace(importer_id="test.importer", source_id="assembly", coordinate_frame_id="frame"),
            get_source=lambda source_id: SimpleNamespace(source_id=source_id, kind=SourceKind.GEOTIFF),
            list_source_assembly_members=lambda _: (
                SimpleNamespace(source_id="first"), SimpleNamespace(source_id="second"),
            ),
        )
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            registry().resolve(store, scene_revision_id="scene", target_type="dts")
        store.list_source_assembly_members = lambda _: ()
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            registry().resolve(store, scene_revision_id="scene", target_type="dts")
        with self.assertRaisesRegex(ValueError, "geometry"):
            registry().resolve(store, scene_revision_id="scene", target_type="dts", geometry_kind="polyline")

    def test_versioned_workflow_round_trip_and_legacy_request(self):
        binding = SamplingBinding(SAMPLER, SamplingSourceRef("source", "src", "artifact", "a" * 64, "frame"),
                                  "test.terrain-config/1", {"band": 1}, "fail-nodata")
        workflow = ResolvedPlantWorkflow("test.vector", "1", "sha256:" + "b" * 64, "scene", binding, OUTPUT, "dts")
        request = PlantRunExecutionRequest("scene", "regions", ("region",), "sha256:" + "c" * 64,
            17, "dts", catalog_id="catalog", elevation_provider_id="terrain", export_provider_id="target",
            offset=(0., 0., 0.), workflow=workflow)
        document = PlantRunExecutionService._document(request)
        self.assertEqual(document["version"], "6")
        restored = PlantRunExecutionService._request_from_document(json.loads(json.dumps(document)))
        self.assertEqual(restored.workflow, workflow)
        binding.configuration["band"] = 2
        self.assertEqual(restored.workflow.sampling.configuration["band"], 1)
        legacy = PlantRunExecutionService._document(replace(request, workflow=None))
        self.assertEqual(legacy["version"], "5")
        self.assertIsNone(PlantRunExecutionService._request_from_document(legacy).workflow)
        with self.assertRaises(ValueError):
            PlantRunExecutionService._request_from_document({**document, "version": "5"})
        with self.assertRaises(ValueError):
            replace(binding, configuration={"source_path": "private"}).validate()
        with self.assertRaises(ValueError):
            replace(binding, alignment="auto-reproject").validate()

    @unittest.skipIf(numpy is None or rasterio is None, "GIS dependencies unavailable")
    def test_explicit_geotiff_resampling_does_not_reexecute_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vector_path, tif = _write_inputs(root)
            second_path = root / "second.tif"
            with rasterio.open(second_path, "w", driver="GTiff", width=3, height=3, count=1,
                               dtype="float32", transform=from_origin(0, 3, 1, 1)) as dataset:
                dataset.write(numpy.full((3, 3), 42., dtype="float32"), 1)
            factory = GeospatialExtensionFactory(root / "runtime", root / "cache", python_executable=Path(sys.executable))
            config = CoreHostConfig(root / "project.verdflow", enable_blender_worker=False, enable_project_maintenance=False)
            with CoreHost.create(config, project_name="Explicit terrain", extension_factories=(factory,)) as host:
                vector = host.add_source_file(vector_path, kind=SourceKind.GEOJSON)
                terrain = host.add_source_file(tif, kind=SourceKind.GEOTIFF)
                alternate = host.add_source_file(second_path, kind=SourceKind.GEOTIFF)
                assembly = host.create_identity_assembly(original_name="Vector and terrain",
                    source_ids=(terrain.source_id, vector.source_id))
                prepared = host.prepare_region_source(RegionSourceRequest(assembly.source_id, "verdflow.geospatial"), timeout_seconds=20)
                generated = _generated_point_set(host, prepared)
                workflow = host.workflows.resolve(host.store, scene_revision_id=prepared.scene_revision.scene_revision_id, target_type="dts")
                self.assertEqual(workflow.sampling.source.source_id, terrain.source_id)
                frozen_document = workflow.to_document()
                first = host.sample_elevation(ElevationSamplingRequest(generated.point_set_id,
                    workflow.elevation_provider_id, binding=workflow.sampling), timeout_seconds=20)
                with host.store.open_artifact(first.job.command.artifact_id) as stream:
                    command = json.load(stream)
                self.assertEqual(command["version"], "2")
                self.assertEqual(command["source_id"], terrain.source_id)
                ref = replace(workflow.sampling.source, source_id=alternate.source_id,
                    artifact_id=alternate.artifact_id, content_hash=host.store.get_artifact(alternate.artifact_id).content_hash,
                    assembly_source_id=None, source_member_id=None)
                second = host.sample_elevation(ElevationSamplingRequest(generated.point_set_id,
                    workflow.elevation_provider_id, binding=replace(workflow.sampling, source=ref)), timeout_seconds=20)
                self.assertNotEqual(first.point_set.point_set_id, second.point_set.point_set_id)
                self.assertEqual(first.point_set.min_z, 12.5)
                self.assertEqual(second.point_set.min_z, 42.)
                self.assertEqual(first.point_set.parent_point_set_id, second.point_set.parent_point_set_id)
                self.assertEqual(workflow.to_document(), frozen_document)
                with host.store.open_artifact(second.point_set.point_artifact_id) as stream:
                    point = json.load(stream)["points"][0]
                self.assertEqual(point["generated_position"], [0.5, 2.5, 0.])
                self.assertEqual(point["rotation_quaternion_xyzw"], [0., 0., 0., 1.])
                self.assertEqual(point["role_key"], "plant")
                with self.assertRaisesRegex(ValueError, "content"):
                    host.application.submit_elevation_sampling(ElevationSamplingRequest(generated.point_set_id,
                        workflow.elevation_provider_id, binding=replace(workflow.sampling, source=replace(ref, content_hash="0" * 64))))
                with self.assertRaisesRegex(ValueError, "coordinate"):
                    host.application.submit_elevation_sampling(ElevationSamplingRequest(generated.point_set_id,
                        workflow.elevation_provider_id, binding=replace(workflow.sampling, source=replace(ref, coordinate_frame_id="other"))))
                with self.assertRaisesRegex(ValueError, "no longer available"):
                    host.application.submit_elevation_sampling(ElevationSamplingRequest(generated.point_set_id,
                        workflow.elevation_provider_id, binding=replace(workflow.sampling, provider=replace(workflow.sampling.provider, provider_version="2"))))
                with (patch.object(host.application, "freeze_profile", return_value=None),
                      patch.object(host.application, "freeze_role_assignments", return_value={}),
                      patch.object(host.application, "freeze_generation_args", return_value={})):
                    execution = host.submit_plant_run_execution(PlantRunExecutionRequest(
                        prepared.scene_revision.scene_revision_id, prepared.region_set.region_set_id,
                        ("region_0001_001",), "sha256:" + "c" * 64, 17, "dts", catalog_id="catalog",
                        elevation_provider_id=workflow.elevation_provider_id, export_provider_id=workflow.export_provider_id,
                        offset=(1., 2., 3.), workflow=workflow,
                    ))
                host.store.collect_garbage()
            with CoreHost.open(config, extension_factories=(factory,)) as reopened:
                self.assertEqual(reopened.store.get_source(terrain.source_id).artifact_id, terrain.artifact_id)
                self.assertEqual(ResolvedPlantWorkflow.from_document(frozen_document), workflow)
                self.assertEqual(reopened.get_plant_run_execution_request(execution.execution_id).workflow, workflow)
                self.assertEqual(reopened.store.get_artifact(alternate.artifact_id).content_hash, ref.content_hash)


if __name__ == "__main__":
    unittest.main()
