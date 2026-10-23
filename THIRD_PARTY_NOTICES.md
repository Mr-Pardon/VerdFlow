# Third-Party Notices

VerdFlow original source uses Apache-2.0. This does not replace the licenses of
dependencies or grant rights to external platforms, models, or user data.

## Included JavaScript Runtime Code

The prebuilt DTS WebUI bundle includes Vue and Lucide code. Studio builds also
use Vue Router. Their notices are preserved in [third-party/runtime-ui.txt](third-party/runtime-ui.txt).
Exact resolved dependency versions are recorded in pnpm-lock.yaml.

The README workflow cards also use Lucide icons. The same Lucide notice in
[third-party/runtime-ui.txt](third-party/runtime-ui.txt) applies to those icons.

## Dependencies Installed Separately

Python, Electron, Node.js, Vite, TypeScript and Python packages are not vendored
as executables in this source tree. Their upstream packages contain their own
licenses. A binary redistribution must additionally include the notices and
license material for every runtime and transitive dependency it actually ships;
this source notice alone is not a completed binary distribution license inventory.

The portable builder preserves Electron's license as `third-party/electron-LICENSE.txt`,
Chromium notices as `LICENSES.chromium.html`, and CPython and wheel license files in
their original runtime directories. It copies the installed JavaScript runtime
dependency notices and generates `third-party/README.md` and `third-party/licenses.json`
with versions, relative paths and file hashes. Missing runtime notices fail the build.
The generated inventory includes native notices supplied by wheels, but does not
replace review of upstream redistribution or corresponding-source requirements.

Blender and DTS Explorer/SDK are not included. Integrations require separately
installed software and any applicable platform permissions. Blender API scripts
are identifiable under the Blender adapter; compatibility must be assessed for
the actual combination being distributed, not inferred solely from a process boundary.

Asset catalogs contain references rather than a grant to redistribute referenced
models. Contributors and users must verify rights for their models, thumbnails,
textures, extension packages and service inputs.
