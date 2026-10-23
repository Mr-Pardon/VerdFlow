import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdir, readdir, rm, stat } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const children = new Set();
let shuttingDown = false;

const main = async () => {
  const defaultLibraryPath = path.join(
    repositoryRoot,
    "_work",
    "studio-dev",
    "library",
  );
  const configuredLibraryPath =
    process.env.VERDFLOW_DEV_LIBRARY_PATH?.trim() || null;
  const libraryPath = path.resolve(configuredLibraryPath || defaultLibraryPath);
  const pythonPath = await collectPythonSourceRoots();
  if (!configuredLibraryPath) {
    await resetIncompatibleDefaultLibrary(
      libraryPath,
      path.resolve(defaultLibraryPath),
      pythonPath,
    );
  }
  await mkdir(libraryPath, { recursive: true });

  const apiPort = await resolvePort("VERDFLOW_DEV_API_PORT", 8768);
  const studioPort = await resolvePort("VERDFLOW_DEV_STUDIO_PORT", 4173);
  const apiBaseUrl = `http://127.0.0.1:${apiPort}`;
  const studioUrl = `http://127.0.0.1:${studioPort}/`;
  const managementToken =
    process.env.VERDFLOW_LOCAL_API_MANAGEMENT_TOKEN?.trim() ||
    randomBytes(32).toString("hex");
  print("runtime", libraryPath);
  print("local-api", apiBaseUrl);
  print("studio", studioUrl);

  const localApi = spawnTracked(
    process.env.VERDFLOW_PYTHON?.trim() || "python",
    [
      "-m",
      "verdflow.local_api",
      "--library-path",
      libraryPath,
      "--host",
      "127.0.0.1",
      "--port",
      String(apiPort),
    ],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        PYTHONPATH: appendPathList(pythonPath, process.env.PYTHONPATH),
        VERDFLOW_LOCAL_API_MANAGEMENT_TOKEN: managementToken,
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  const ready = await waitForLocalApi(localApi);
  if (ready.reused === true) {
    throw new Error(
      "The selected development runtime is already owned by another Local API. " +
        "Close it or set VERDFLOW_DEV_LIBRARY_PATH to another directory.",
    );
  }
  await waitForHttp(`${apiBaseUrl}/api/v1/health`, "Local API");

  spawnPnpm(
    [
      "--filter",
      "@verdflow/studio",
      "dev",
      "--",
      "--host",
      "127.0.0.1",
      "--port",
      String(studioPort),
      "--strictPort",
    ],
    {
      ...process.env,
      VITE_VERDFLOW_API_TARGET: apiBaseUrl,
    },
  );
  await waitForHttp(studioUrl, "Studio Vite");

  const electron = spawnPnpm(
    ["--filter", "@verdflow/studio-electron-probe", "dev"],
    {
      ...process.env,
      VERDFLOW_STUDIO_URL: studioUrl,
      VERDFLOW_LOCAL_API_BASE_URL: apiBaseUrl,
      VERDFLOW_LOCAL_API_MANAGEMENT_TOKEN: managementToken,
      VERDFLOW_DESKTOP_DIAGNOSTICS:
        process.env.VERDFLOW_DESKTOP_DIAGNOSTICS || "1",
    },
  );
  const exitCode = await waitForExit(electron, "Electron");
  process.exitCode = exitCode ?? 0;
};

const resetIncompatibleDefaultLibrary = async (
  libraryPath,
  expectedDefaultPath,
  pythonPath,
) => {
  if (path.resolve(libraryPath) !== expectedDefaultPath) {
    throw new Error("Refusing to reset a development library outside _work/studio-dev");
  }
  const python = process.env.VERDFLOW_PYTHON?.trim() || "python";
  const probe = spawnSync(
    python,
    [
      "-c",
      [
        "import json, sqlite3, sys",
        "from pathlib import Path",
        "from verdflow.library_store.schema import SCHEMA_VERSION",
        "database = Path(sys.argv[1]) / 'library.db'",
        "if not database.exists(): print(json.dumps({'status': 'missing'})); raise SystemExit(0)",
        "connection = sqlite3.connect(database)",
        "try: row = connection.execute(\"SELECT value FROM schema_metadata WHERE key = 'schema_version'\").fetchone()",
        "finally: connection.close()",
        "version = int(row[0]) if row is not None else None",
        "print(json.dumps({'status': 'existing', 'version': version, 'current': SCHEMA_VERSION}))",
      ].join("\n"),
      libraryPath,
    ],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        PYTHONPATH: appendPathList(pythonPath, process.env.PYTHONPATH),
      },
      encoding: "utf8",
      windowsHide: true,
    },
  );
  if (probe.status !== 0) {
    throw new Error(
      `Could not inspect the default development library: ${
        probe.stderr?.trim() || probe.error?.message || "unknown failure"
      }`,
    );
  }
  const result = JSON.parse(probe.stdout.trim());
  if (result.status !== "existing" || result.version === result.current) return;

  print(
    "runtime-reset",
    `discarding pre-release schema ${result.version}; current is ${result.current}`,
  );
  await rm(libraryPath, { recursive: true, force: true });
};

const collectPythonSourceRoots = async () => {
  const roots = [];
  for (const group of ["apps", "adapters", "packages"]) {
    const groupPath = path.join(repositoryRoot, group);
    for (const entry of await readdir(groupPath, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const sourcePath = path.join(groupPath, entry.name, "src");
      if (await isDirectory(sourcePath)) roots.push(sourcePath);
    }
  }
  if (roots.length === 0) {
    throw new Error("No VerdFlow Python source roots were found");
  }
  return roots;
};

const resolvePort = async (environmentName, preferredPort) => {
  const configured = process.env[environmentName]?.trim();
  if (configured) {
    const port = Number(configured);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new Error(`${environmentName} must be a valid TCP port`);
    }
    if (!(await portIsAvailable(port))) {
      throw new Error(`${environmentName} port ${port} is already in use`);
    }
    return port;
  }
  for (let port = preferredPort; port < preferredPort + 20; port += 1) {
    if (await portIsAvailable(port)) return port;
  }
  return await ephemeralPort();
};

const portIsAvailable = (port) =>
  new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once("error", () => resolve(false));
    server.listen({ host: "127.0.0.1", port }, () => {
      server.close(() => resolve(true));
    });
  });

const ephemeralPort = () =>
  new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0 }, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : null;
      server.close(() =>
        port ? resolve(port) : reject(new Error("Could not allocate a port")),
      );
    });
  });

const spawnPnpm = (arguments_, environment) => {
  if (process.platform === "win32") {
    return spawnTracked(
      process.env.ComSpec || "cmd.exe",
      ["/d", "/s", "/c", "corepack", "pnpm", ...arguments_],
      { cwd: repositoryRoot, env: environment, stdio: "inherit" },
    );
  }
  return spawnTracked("corepack", ["pnpm", ...arguments_], {
    cwd: repositoryRoot,
    env: environment,
    stdio: "inherit",
  });
};

const spawnTracked = (command, arguments_, options) => {
  const child = spawn(command, arguments_, options);
  children.add(child);
  child.once("exit", () => children.delete(child));
  child.once("error", (error) => {
    process.stderr.write(`[studio-dev] ${command} failed to start: ${error.message}\n`);
  });
  return child;
};

const waitForLocalApi = (child) =>
  new Promise((resolve, reject) => {
    let settled = false;
    let buffer = "";
    const timeout = setTimeout(
      () => finish(new Error("Local API startup timed out")),
      20_000,
    );
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      error ? reject(error) : resolve(value);
    };

    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        process.stdout.write(`[local-api] ${line}\n`);
        try {
          const document = JSON.parse(line);
          if (document.status === "ready") finish(null, document);
          if (document.status === "error") {
            finish(new Error(document.error || "Local API startup failed"));
          }
        } catch {
          // Python diagnostics remain visible but do not define readiness.
        }
      }
    });
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) =>
      process.stderr.write(`[local-api] ${chunk}`),
    );
    child.once("exit", (code) => {
      if (!settled) finish(new Error(`Local API exited before ready (${code})`));
    });
    child.once("error", finish);
  });

const waitForHttp = async (url, label) => {
  const deadline = Date.now() + 20_000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_000) });
      if (response.ok) {
        print(label, "ready");
        return;
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`${label} did not become ready: ${readableError(lastError)}`);
};

const waitForExit = (child, label) =>
  new Promise((resolve, reject) => {
    child.once("exit", resolve);
    child.once("error", (error) =>
      reject(new Error(`${label}: ${error.message}`)),
    );
  });

const appendPathList = (paths, existing) =>
  [...paths, ...(existing ? [existing] : [])].join(path.delimiter);

const isDirectory = async (target) => {
  try {
    return (await stat(target)).isDirectory();
  } catch {
    return false;
  }
};

const print = (label, value) => {
  process.stdout.write(`[studio-dev] ${label}: ${value}\n`);
};

const readableError = (error) =>
  error instanceof Error ? error.message : String(error || "unknown failure");

const terminate = (child) => {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
  } else {
    child.kill("SIGTERM");
  }
};

const shutdown = () => {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of [...children].reverse()) terminate(child);
};

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    shutdown();
    process.exit(signal === "SIGINT" ? 130 : 143);
  });
}

try {
  await main();
} catch (error) {
  process.stderr.write(`[studio-dev] startup failed: ${readableError(error)}\n`);
  process.exitCode = 1;
} finally {
  shutdown();
}
