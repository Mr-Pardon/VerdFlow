import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import test from "node:test";

import sharp from "sharp";
import { Data, NtExecutable, NtExecutableResource, Resource } from "resedit";
import { brandWindowsExecutable } from "../windows_branding.mjs";

const root = new URL("../../", import.meta.url);
const brand = new URL("assets/branding/", root);
const generated = new URL("generated/brand/", brand);
const sizes = [16, 20, 24, 32, 48, 64, 128, 256];
const hash = (buffer) => createHash("sha256").update(buffer).digest("hex");

test("the shared assets preserve transparency and use the expected dimensions", async () => {
  for (const [name, width, height] of [
    ["icon-32.png", 32, 32], ["icon-256.png", 256, 256],
    ["logo-light.png", 768, 153], ["logo-dark.png", 768, 153],
  ]) {
    const data = await readFile(new URL(name, generated));
    const metadata = await sharp(data).metadata();
    assert.equal(metadata.width, width);
    assert.equal(metadata.height, height);
    assert.equal(metadata.hasAlpha, true);
    assert.equal(metadata.exif, undefined);
    const alpha = await sharp(data).extractChannel("alpha").raw().toBuffer();
    assert.ok(alpha.includes(0));
    assert.ok(alpha.includes(255));
  }
});

test("ICO includes every Windows scale and generation is repeatable without changing the originals", async () => {
  const names = ["verdflow-app-icon-1024.png", "verdflow-logo-light.png", "verdflow-logo-dark.png"];
  const originals = await Promise.all(names.map(async (name) => hash(await readFile(new URL(name, brand)))));
  const before = hash(await readFile(new URL("app.ico", generated)));
  await promisify(execFile)(process.execPath, [fileURLToPath(new URL("tools/build_branding.mjs", root))]);
  assert.equal(hash(await readFile(new URL("app.ico", generated))), before);
  assert.deepEqual(await Promise.all(names.map(async (name) => hash(await readFile(new URL(name, brand))))), originals);
  const icon = Data.IconFile.from(await readFile(new URL("app.ico", generated)));
  assert.deepEqual(icon.icons.map((entry) => entry.data.width), sizes);
});

test("the staged Electron executable receives the actual icon and product metadata without modifying executable code or manifest", async () => {
  const binary = await readFile(new URL("apps/studio-electron-probe/node_modules/electron/dist/electron.exe", root));
  const ico = await readFile(new URL("app.ico", generated));
  const { version } = JSON.parse(await readFile(new URL("package.json", root), "utf8"));
  const output = brandWindowsExecutable(binary, ico, version);
  const before = NtExecutable.from(binary, { ignoreCert: true });
  const after = NtExecutable.from(output);
  for (const section of before.getAllSections().filter((item) => item.info.name !== ".rsrc")) {
    const next = after.getAllSections().find((item) => item.info.name === section.info.name);
    assert.ok(next, section.info.name);
    assert.deepEqual(Buffer.from(next.data ?? []), Buffer.from(section.data ?? []), section.info.name);
  }
  const resources = NtExecutableResource.from(after);
  const group = Resource.IconGroupEntry.fromEntries(resources.entries)[0];
  const images = group.getIconItemsFromEntries(resources.entries);
  assert.deepEqual(images.map((entry) => entry.width || 256), sizes);
  assert.deepEqual(await Promise.all(images.map(async (entry) => (await sharp(Buffer.from(entry.bin)).metadata()).width)), sizes);
  assert.deepEqual(images.map((entry) => hash(Buffer.from(entry.bin))), Data.IconFile.from(ico).icons.map((entry) => hash(Buffer.from(entry.data.bin))));
  const metadata = Resource.VersionInfo.fromEntries(resources.entries)[0];
  for (const language of metadata.getAllLanguagesForStringValues()) {
    const values = metadata.getStringValues(language);
    assert.equal(values.ProductName, "VerdFlow");
    assert.equal(values.FileDescription, "VerdFlow Studio");
    assert.equal(values.ProductVersion, version);
  }
  const beforeManifests = NtExecutableResource.from(before).entries.filter((entry) => entry.type === 24);
  assert.deepEqual(resources.entries.filter((entry) => entry.type === 24), beforeManifests);
});

test("invalid Windows version values are rejected before modifying resources", () => {
  for (const version of ["preview", "1.2.3-beta", "1.2.99999", "-1.2.3"]) {
    assert.throws(() => brandWindowsExecutable(Buffer.alloc(0), Buffer.alloc(0), version), /version/i);
  }
});
