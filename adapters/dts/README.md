# verdflow-dts-adapter

状态：Experimental  
架构分类：Integration Adapter  
维护责任：DTS output adapter maintainers

`verdflow-dts-adapter` owns the DTS output boundary. It converts immutable
VerdFlow `PointSet v2`, `AssetAssignmentSet v1`, and effective binding snapshots
into DTS `ModelPointClass` XML.

## 职责

- Resolve DTS `ModelName` from the frozen catalog asset metadata.
- Apply the user-provided XYZ offset only during final output.
- Convert point quaternion and assigned scale into DTS `Matrix3`.
- Run the `verdflow.dts.export-xml` Job and persist the XML as an Artifact.
- Render an ordered set of independently frozen planting batches into one XML
  document without reading or concatenating earlier XML exports.
- Hold transport-neutral DTS Live Session correlation state for the interactive
  single- and multi-region path.
- Plan ordered per-region elevation batches without exposing camera APIs to Core.
- Translate a trusted DTS selection collection and elevation callback stream into
  canonical Scene/RegionSet/PointSet Worker results.
- Accept indexed elevation chunks and explicit failed-region reports without changing
  frozen point lineage. Live exports require terrain hits, excluding failed regions
  from XML while retaining their canonical miss records (ADR-0007).

## 非职责

- Point generation, elevation sampling, asset selection, or randomization.
- Project lifecycle and `ExportRecord` persistence.
- Knowledge of live User Library files after assignment snapshots are frozen.
- Explorer HTTP serving, CEF SDK loading, camera movement, or strategy execution.

The pure XML renderer lives in `xml.py`; the XML Job driver lives in `worker.py`;
`live.py` owns transport-neutral correlation state and `live_worker.py` owns the
trusted in-process Core Worker boundary.

XML Worker checks immutable Artifact IDs, lineage, Core counts and saved assignment
hashes, then uses the adapter-internal persisted rendering path (ADR-0007). It does
not repeat large JSON Schema walks or assignment hashing after Core acceptance.
Exact point/assignment coverage, finite output values, model names and coordinate
offset rules are still enforced. The public raw-document renderers retain full
validation. Terrain/asset selection remains a target-neutral Planting capability;
an unchanged selection reuses its input without copying or hashing it again.

## 公共入口

```python
from verdflow.dts_adapter import (
    DtsLiveCoreWorker,
    DtsLiveSession,
    render_dts_xml,
    render_dts_xml_composition,
)
```

## 依赖边界

- 只通过 Core `JobRuntime` 与 ProjectStore 公共门面读取不可变 Artifact。
- 不读取 User Library 活目录，不做资产选择，也不重新随机化点位属性。
- DTS 字段、坐标偏移和 `Matrix3` 不得进入 Application Core。

## 验证

```powershell
python -m unittest tests.unit.test_dts_adapter -v
python -m unittest tests.unit.test_dts_live_session -v
python -m unittest tests.unit.test_dts_live_vertical_runtime -v
python -m unittest tests.unit.test_planting_generation_stage -v
```

## 当前限制

- 当前仅实现 DTS `ModelPointClass` XML v1。
- `provider_metadata.model_name` 是 DTS Catalog 的必填输出映射。
- DTS Live 的 Session、Region/Elevation Worker 与临时 CoreHost 合成纵向测试已通过；
  Explorer WebUI、真实 SDK 兼容和场景门禁仍待后续切片完成。
