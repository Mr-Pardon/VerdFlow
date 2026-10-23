import path from "node:path";

export const portableLayout = ({ executablePath, resourcesPath, dataRootPath }) => {
  if (
    !path.isAbsolute(executablePath) ||
    !path.isAbsolute(resourcesPath) ||
    !path.isAbsolute(dataRootPath)
  ) {
    throw new TypeError("Portable runtime paths must be absolute");
  }
  const rootPath = path.dirname(path.resolve(executablePath));
  const resolvedResources = path.resolve(resourcesPath);
  if (path.dirname(resolvedResources) !== rootPath) {
    throw new TypeError("Portable resources must be inside the application root");
  }
  const userDataPath = path.resolve(dataRootPath);
  return Object.freeze({
    rootPath,
    resourcesPath: resolvedResources,
    rendererEntryPath: path.join(resolvedResources, "studio", "index.html"),
    pythonExecutablePath: path.join(resolvedResources, "python", "python.exe"),
    userDataPath,
    electronDataPath: path.join(userDataPath, "Electron"),
    libraryPath: path.join(userDataPath, "Library"),
    logsPath: path.join(userDataPath, "Logs"),
  });
};

export const dailyCoreLogName = (date = new Date()) => {
  const day = date.toISOString().slice(0, 10);
  return `core-${day}.log`;
};
