import assert from "node:assert/strict";
import test from "node:test";

import {
  dailyCoreLogName,
  portableLayout,
} from "../src/portable-runtime.mjs";

test("packaged layout keeps mutable data in the stable VerdFlow application directory", () => {
  const layout = portableLayout({
    executablePath: "D:\\VerdFlow\\VerdFlow Studio.exe",
    resourcesPath: "D:\\VerdFlow\\resources",
    dataRootPath: "C:\\Users\\tester\\AppData\\Local\\VerdFlow",
  });
  assert.equal(layout.rootPath, "D:\\VerdFlow");
  assert.equal(layout.userDataPath, "C:\\Users\\tester\\AppData\\Local\\VerdFlow");
  assert.equal(layout.libraryPath, "C:\\Users\\tester\\AppData\\Local\\VerdFlow\\Library");
  assert.equal(layout.logsPath, "C:\\Users\\tester\\AppData\\Local\\VerdFlow\\Logs");
  assert.equal(
    layout.pythonExecutablePath,
    "D:\\VerdFlow\\resources\\python\\python.exe",
  );
});

test("portable layout rejects resources outside the application root", () => {
  assert.throws(
    () => portableLayout({
      executablePath: "D:\\VerdFlow\\VerdFlow Studio.exe",
      resourcesPath: "D:\\Shared\\resources",
      dataRootPath: "C:\\Users\\tester\\AppData\\Local\\VerdFlow",
    }),
    /inside the application root/,
  );
});

test("core log names are stable per UTC day", () => {
  assert.equal(
    dailyCoreLogName(new Date("2026-09-09T23:59:59Z")),
    "core-2026-09-09.log",
  );
});
