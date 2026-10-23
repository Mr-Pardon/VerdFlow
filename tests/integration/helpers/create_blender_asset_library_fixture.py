from __future__ import annotations

import sys
from pathlib import Path

import bpy


def cube_mesh(name: str, size: float, height: float) -> bpy.types.Mesh:
    half = size / 2.0
    vertices = [
        (-half, -half, 0.0),
        (half, -half, 0.0),
        (half, half, 0.0),
        (-half, half, 0.0),
        (-half, -half, height),
        (half, -half, height),
        (half, half, height),
        (-half, half, height),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (4, 0, 3, 7),
    ]
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    return mesh


def add_asset(name: str, size: float, height: float) -> None:
    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    item = bpy.data.objects.new(name, cube_mesh(name, size, height))
    collection.objects.link(item)
    collection.asset_mark()
    collection.asset_data.description = f"Synthetic VerdFlow asset: {name}"


def main() -> None:
    output = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    add_asset("VF_LowShrub", 0.8, 0.6)
    add_asset("VF_MiddleShrub", 1.2, 1.5)
    add_asset("VF_Tree", 2.5, 5.0)
    ordinary = bpy.data.collections.new("VF_NotAnAsset")
    bpy.context.scene.collection.children.link(ordinary)
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)


if __name__ == "__main__":
    main()
