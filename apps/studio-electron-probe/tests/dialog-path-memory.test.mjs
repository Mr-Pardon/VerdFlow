import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { DialogPathMemory } from "../src/dialog-path-memory.mjs";

test("remembers separate directories for each dialog purpose", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "verdflow-dialog-paths-"));
  try {
    const fallback = path.join(root, "fallback");
    const source = path.join(root, "models");
    const exports = path.join(root, "exports");
    await Promise.all([mkdir(fallback), mkdir(source), mkdir(exports)]);
    const storagePath = path.join(root, "dialog-paths.json");
    const memory = new DialogPathMemory({ storagePath, fallbackDirectory: fallback });

    assert.equal(await memory.defaultPath("source:fbx"), fallback);
    await memory.remember("source:fbx", path.join(source, "scene.fbx"), "file");
    await memory.remember("export:xml", path.join(exports, "planting.xml"), "file");

    const restored = new DialogPathMemory({ storagePath, fallbackDirectory: fallback });
    assert.equal(await restored.defaultPath("source:fbx"), source);
    assert.equal(
      await restored.defaultPath("export:xml", "next.xml"),
      path.join(exports, "next.xml"),
    );
    assert.equal(JSON.parse(await readFile(storagePath, "utf8")).version, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("falls back when a remembered directory no longer exists", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "verdflow-dialog-paths-"));
  try {
    const fallback = path.join(root, "fallback");
    const temporary = path.join(root, "temporary");
    await Promise.all([mkdir(fallback), mkdir(temporary)]);
    const memory = new DialogPathMemory({
      storagePath: path.join(root, "dialog-paths.json"),
      fallbackDirectory: fallback,
    });
    await memory.remember("project", temporary, "directory");
    await rm(temporary, { recursive: true });
    assert.equal(await memory.defaultPath("project"), fallback);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
