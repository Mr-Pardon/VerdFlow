import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";
import { Data } from "resedit";

const root = fileURLToPath(new URL("../assets/branding/", import.meta.url));
const output = path.join(root, "generated", "brand");

const writeChanged = async (name, data) => {
  const destination = path.join(output, name);
  const previous = await readFile(destination).catch((error) => {
    if (error.code !== "ENOENT") throw error;
    return null;
  });
  if (!previous?.equals(data)) await writeFile(destination, data);
};

await mkdir(output, { recursive: true });
const iconSource = path.join(root, "verdflow-app-icon-1024.png");
const iconMetadata = await sharp(iconSource).metadata();
if (iconMetadata.width !== 1024 || iconMetadata.height !== 1024 || !iconMetadata.hasAlpha) {
  throw new Error("The branding source must be a transparent 1024px square PNG");
}

const icon = new Data.IconFile();
for (const size of [16, 20, 24, 32, 48, 64, 128, 256]) {
  const png = await sharp(iconSource).resize(size, size).png().toBuffer();
  icon.icons.push({ data: Data.RawIconItem.from(png, size, size, 32) });
  if (size === 32 || size === 256) await writeChanged(`icon-${size}.png`, png);
}
await writeChanged("app.ico", Buffer.from(icon.generate()));

for (const theme of ["light", "dark"]) {
  const source = path.join(root, `verdflow-logo-${theme}.png`);
  const metadata = await sharp(source).metadata();
  if (!metadata.hasAlpha || metadata.width < 768) {
    throw new Error("Logo sources must be transparent PNGs at least 768px wide");
  }
  await writeChanged(`logo-${theme}.png`, await sharp(source).resize({ width: 768 }).png().toBuffer());
}
console.log("VerdFlow branding assets ready");
