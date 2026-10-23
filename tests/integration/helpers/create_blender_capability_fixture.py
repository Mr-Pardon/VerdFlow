from __future__ import annotations

import sys
from pathlib import Path

import bpy


def main() -> None:
    output = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    vertices = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 0.0),
        (2.0, 1.0, 0.0),
        (4.0, 0.0, 0.0),
        (5.0, 0.0, 0.0),
        (5.0, 1.0, 0.0),
        (4.0, 1.0, 0.0),
        (0.0, 3.0, 0.0),
        (1.0, 3.0, 0.0),
        (1.0, 4.0, 0.0),
        (0.0, 4.0, 0.0),
    ]
    faces = [
        (0, 1, 4, 3),
        (1, 2, 5, 4),
        (6, 7, 8, 9),
        (10, 11, 12, 13),
    ]
    mesh = bpy.data.meshes.new("Ground_Test_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(bpy.data.materials.new("Grass_Test"))
    mesh.materials.append(bpy.data.materials.new("Road_Test"))
    mesh.polygons[3].material_index = 1
    ground = bpy.data.objects.new("Ground_Test", mesh)
    bpy.context.scene.collection.objects.link(ground)

    result = bpy.ops.export_scene.fbx(
        filepath=str(output),
        use_selection=False,
        axis_forward="-Y",
        axis_up="Z",
        apply_unit_scale=True,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"FBX fixture export failed: {result}")


if __name__ == "__main__":
    main()
