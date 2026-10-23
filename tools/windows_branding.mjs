import { readFile, rename, rm, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

import { Data, NtExecutable, NtExecutableResource, Resource } from "resedit";

export const brandWindowsExecutable = (binary, iconBinary, version) => {
  if (!/^\d+\.\d+\.\d+(?:\.\d+)?$/.test(version)) {
    throw new Error("Windows file version must have three or four numeric components");
  }
  const numbers = version.split(".").map(Number);
  if (numbers.some((value) => value > 65535)) throw new Error("Windows version component is too large");
  while (numbers.length < 4) numbers.push(0);

  // Rebranding invalidates the upstream signature; the staged result is unsigned.
  const executable = NtExecutable.from(binary, { ignoreCert: true });
  const resources = NtExecutableResource.from(executable);
  const groups = Resource.IconGroupEntry.fromEntries(resources.entries);
  const versions = Resource.VersionInfo.fromEntries(resources.entries);
  if (groups.length !== 1 || versions.length !== 1) {
    throw new Error("Unexpected Electron icon or version resource layout");
  }
  const icons = Data.IconFile.from(iconBinary).icons.map((item) => item.data);
  if (icons.length !== 8) throw new Error("The application icon must contain all eight sizes");
  Resource.IconGroupEntry.replaceIconsForResource(resources.entries, groups[0].id, groups[0].lang, icons);
  const info = versions[0];
  info.setFileVersion(...numbers);
  info.setProductVersion(...numbers);
  for (const language of info.getAllLanguagesForStringValues()) {
    info.setStringValues(language, {
      CompanyName: "Mr_Pardon",
      FileDescription: "VerdFlow Studio",
      InternalName: "VerdFlowStudio",
      OriginalFilename: "VerdFlow Studio.exe",
      ProductName: "VerdFlow",
      FileVersion: version,
      ProductVersion: version,
      LegalCopyright: "Copyright 2026 Mr_Pardon",
    });
  }
  info.outputToResourceEntries(resources.entries);
  resources.outputResource(executable);
  return Buffer.from(executable.generate());
};

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [executablePath, iconPath, version] = process.argv.slice(2);
  if (!executablePath || !iconPath || !version) {
    throw new Error("Usage: node tools/windows_branding.mjs <staged-exe> <ico> <version>");
  }
  const output = brandWindowsExecutable(await readFile(executablePath), await readFile(iconPath), version);
  const temporary = executablePath + ".branding.tmp";
  try {
    await writeFile(temporary, output);
    await rename(temporary, executablePath);
  } finally {
    await rm(temporary, { force: true });
  }
  console.log("VerdFlow Windows icon and version resources ready (unsigned)");
}
