# License Material

`runtime-ui.txt` preserves notices for the prebuilt UI bundles and README icons.

`@vue/devtools-api` 6.6.4 declares MIT in its npm metadata but does not include a
license file. Its unmodified repository license is retained alongside this file.
`license-overrides.json` pins the exact package version, upstream commit, local path
and SHA-256; the portable builder accepts it only when the installed version matches.
Updating this dependency requires reviewing the corresponding upstream material again.

Portable distributions additionally generate an inventory from their staged runtimes
and installed JavaScript dependency graph. The generated `third-party/licenses.json`
and `third-party/README.md` index those files; this source directory does not contain
the Electron, Chromium, CPython or wheel binaries.
