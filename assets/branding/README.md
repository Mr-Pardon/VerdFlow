# VerdFlow Brand Assets

The three `verdflow-*.png` files are the approved artwork supplied by the project
author. `light` is for light surfaces (dark lettering); `dark` is for dark surfaces
(light lettering). Preserve the artwork, proportions and transparent background.

Run `corepack pnpm build:branding` to create the committed files in
`generated/brand/`. This only resizes the artwork and encodes an ICO containing
16, 20, 24, 32, 48, 64, 128 and 256px images. It does not redraw the logo.

Studio and WebUI copy the same generated directory through Vite's public asset
pipeline. The desktop shell and portable builder use the same ICO. These files
belong to application presentation and packaging, not Core, strategies or catalogs.
No model assets or external platform SDKs belong in this directory.
