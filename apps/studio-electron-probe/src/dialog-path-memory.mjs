import { readFile, mkdir, rename, stat, writeFile } from "node:fs/promises";
import path from "node:path";

export class DialogPathMemory {
  constructor({ storagePath, fallbackDirectory }) {
    this.storagePath = storagePath;
    this.fallbackDirectory = fallbackDirectory;
  }

  async defaultPath(key, suggestedName = null) {
    await this.#load();
    const remembered = this.values[key];
    const directory = remembered && await isDirectory(remembered)
      ? remembered
      : this.fallbackDirectory;
    return suggestedName ? path.join(directory, suggestedName) : directory;
  }

  async remember(key, selectedPath, selectionKind) {
    await this.#load();
    const directory = selectionKind === "directory"
      ? selectedPath
      : path.dirname(selectedPath);
    this.values[key] = directory;
    await mkdir(path.dirname(this.storagePath), { recursive: true });
    const temporaryPath = `${this.storagePath}.${process.pid}.${Date.now()}.tmp`;
    await writeFile(
      temporaryPath,
      `${JSON.stringify({ version: 1, paths: this.values }, null, 2)}\n`,
      "utf8",
    );
    await rename(temporaryPath, this.storagePath);
  }

  async #load() {
    if (this.loaded) return;
    this.loaded = true;
    try {
      const document = JSON.parse(await readFile(this.storagePath, "utf8"));
      if (document?.version !== 1 || !isStringRecord(document.paths)) return;
      this.values = { ...document.paths };
    } catch {
      this.values = {};
    }
  }

  storagePath;
  fallbackDirectory;
  loaded = false;
  values = {};
}

const isDirectory = async (candidate) => {
  try {
    return (await stat(candidate)).isDirectory();
  } catch {
    return false;
  }
};

const isStringRecord = (value) =>
  value !== null &&
  typeof value === "object" &&
  !Array.isArray(value) &&
  Object.values(value).every((item) => typeof item === "string");
