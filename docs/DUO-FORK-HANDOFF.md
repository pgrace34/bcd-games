# BCD Duo fork: handoff notes

Last updated 2026-11-07 (build 636). This file is for whoever picks up the fork next, whether that's a person or a new
Claude session: what the fork is, how it builds and ships, and what's in progress right now.

## What this is

- Repository: `pgrace34/bcd-games`, branch **`iphone-duo`** (all fork work happens there).
- Remotes: `origin` = pgrace34/bcd-games, `upstream` = thebcd69/bcd-games (BCD).
- What it adds to BCD: DanielTangnes' iPhone Duo (foldable) changes, merged with the latest upstream BCD, plus a
  split-screen gamepad, cover-screen layout fixes, a 30-second quit-stall fix (`[duo-quit]`) and its own SideStore
  source.
- Files the fork changed carry a `BCD Duo fork (pgrace34/bcd-games)` comment with a date.

## How it builds and ships

- **Pushing to `iphone-duo` starts a full IPA build** (`.github/workflows/build-ipa.yml`, about an hour). Put
  `[skip ci]` in the commit message for doc-only changes.
- The build number comes from the last line of `docs/local-build/BUILD-NUMBER`. Bump it for each build (currently
  **636**). The release is tagged `v0.1.<build>`.
- When the build succeeds it dispatches `.github/workflows/altsource.yml`, which refreshes the SideStore source
  (`tools/update-altsource.py`, published via GitHub Pages). The user installs and updates through SideStore.
- Host "Check" steps in build-ipa.yml have
  `continue-on-error: ${{ github.repository == 'pgrace34/bcd-games' }}`. When merging upstream, add this to any new
  Check steps (a small script did it for 63 steps during the 0.1.634 merge).
- The hourly altsource cron was removed (2026-11-07) because GitHub auto-disabled the fork's Actions. If Actions stop
  running again, the owner re-enables them under the repository's Actions tab.
- The user has no Mac, so nothing iOS-side (Swift, ObjC) can be compiled outside GitHub Actions. Check carefully before
  pushing.

### Merging upstream

`git fetch upstream && git merge upstream/main`, then:
- rebuild `docs/local-build/BUILD-NUMBER` from upstream's copy with the last line set to the next fork build number;
- add `continue-on-error` to any new Check steps;
- update the release-notes text in build-ipa.yml ("Based on BCD 0.1.xxx …").

Last merged: BCD 0.1.634 (upstream e378a60), which adds per-game OpenGL (`gl-backend = zink | gles`).

## Current work: Chrono Trigger's movies (Steam app 613830)

**State:** the game runs with OpenGL (auto-tune sets `gl-backend = zink`). With
`env.MADEIRA_WG_VIDEO_FORMAT = rgb32` the opening anime movie plays with sound, but **the picture stays black** and the
audio is choppy (about a 10 ms readback per frame). The game reaches the title screen after the movie. The user does
**not** want the movies disabled (`env.MADEIRA_WG_VIDEO = 0` was rejected).

**Movie path:** Media Foundation → Wine EVR → D3D9 (DXMT's d3d9 in a 32-bit process) → the child window
`VideoPlayerSubWindow` under the GLFW game window. The EVR mixer calls dxva2 VideoProcessBlt (ColorFill black, then
StretchRect from a plain offscreen surface into the render target). The presenter then StretchRects into an additional
swapchain on the child window and Presents.

**Two hypotheses. Build 636 logs both:**
1. **A StretchRect is rejected.** DXMT's planner (`dxmt/src/d3d9/d3d9_stretchrect.hpp`, `plan_stretch_rect`) rejects a
   size-mismatched stretch into a non-RENDERTARGET destination, which DXVK allows, and returns INVALIDCALL silently.
   Look for `[d3d9-stretch] ... REJECTED` lines (patch: `tools/patch-dxmt-d9-stretch-log.py`).
   *Fix if confirmed:* let DXMT stretch into a plain DEFAULT-pool destination as DXVK does (first check that such
   textures can be render targets in DXMT).
2. **The child swapchain's layer sits under the GL parent's layer.** Winios.m (`app/Madeira/Winios/Winios.m`) dumps the
   compositor's sublayers 1.5 s and 8 s after a Metal layer is created: `[layer-order] dump N ...` lines with hwnd,
   root, hidden, frame, z and drawable counts. *Fix if confirmed:* order a child window's Metal layer above its GL
   parent (see `winios_order_family`, `g_root_hwnd`).

**Next step:** the user installs build 636, launches Chrono Trigger with `rgb32`, lets the movie run about 15 s and
sends the logs. Read the `[d3d9-stretch]` and `[layer-order]` lines, make the fix, and bump to build 637. Once the
movie shows, consider making `rgb32` the fork's default for this game.

## Other open items

- Split screen in the upright position and cover-screen layout still need testing by the user.
- Star Force Enigma's crash is an upstream issue, not a fork one.
