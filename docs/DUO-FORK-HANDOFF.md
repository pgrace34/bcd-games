# BCD Duo fork: handoff notes

Last updated 2026-11-07 (build 639). This file is for whoever picks up the fork next, whether that's a person or a new
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
  **639**). The release is tagged `v0.1.<build>`.
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
StretchRect from the 1920x1080 plain frame surface into the mixer's 1154x810 plain output surface). The presenter then
copies that into an additional swapchain's back buffer on the child window and Presents.

**Build 636's logs (2026-11-07, run with `env.MADEIRA_WG_VIDEO_FORMAT = argb32`, not rgb32):**
- **Hypothesis 1 confirmed.** Every frame's `[d3d9-stretch]` pair is: the frame StretchRect REJECTED planner (src 1920x1080
  A8R8G8B8 plain DEFAULT surface, rect full -> dst 1154x810 X8R8G8B8 plain DEFAULT surface (usage 0), rect (0,81 1154x649)),
  then an ok copy of that 1154x810 surface into the 1154x810 render target. 4096 of 8192 calls rejected: every frame.
  Cause: `plan_stretch_rect`'s gate `extent_mismatch && !(dst.usage & D3DUSAGE_RENDERTARGET)`.
- **Hypothesis 2 ruled out.** `[layer-order]` dumps 3 to 6 put the movie's child window (hwnd 0x20068, root 0x10064,
  `VideoPlayerSubWindow`) at the top of the stack (index 19, above its GL parent 0x10064 at 12), unhidden, and its drawables
  grow (12 -> 162). The swapchain presents fine; it just presents black.
- **Choppy sound found.** `[rb-tex] fmt 80 1920x1080 L0` x4096 and `[d9-drain] readback-drains=4096 avg-us=9553`: each
  frame's LockRect of the EVR's 1920x1080 frame surface drained the GPU and read the texture back (~9.5 ms), though only the
  GPU reads that surface. DXMT's LockRect reads back every non-DYNAMIC DEFAULT surface on every non-DISCARD lock.

**Build 637 (both fixes are DXMT patch scripts run by build-ipa.yml before the i386 farm key):**
- `tools/patch-dxmt-d9-stretch-plain.py`: the planner lets a size-changing StretchRect target a standalone DEFAULT-pool
  plain surface (as DXVK and wined3d do). CreateOffscreenPlainSurface already gives that texture Metal RenderTarget usage.
  A host harness (stub headers + the patched header) ran the logged case: REJECTED before, `stretch` after; a plain texture
  level, a render target into a plain surface and a BC destination stay rejected.
- `tools/patch-dxmt-d9-plain-lock.py`: `MTLD3D9Surface::m_madeira_mirror_current` (set after a readback, cleared by
  `madeiraNoteGpuWrite()`, which the four `dst->flagContainerAutoGenDirty()` writer exits in d3d9_device.cpp now call:
  UpdateSurface, StretchRect, ColorFill x2). A plain surface's LockRect skips the readback while it holds.
  `[d9-plain-lock]` counts skips. `env.MADEIRA_D9_PLAIN_LOCK_READBACK = 1` restores a readback on every lock.
  If DXMT ever gains another GPU writer into surfaces, it must call `madeiraNoteGpuWrite()` (the script refuses to apply
  if the writer count is no longer 4).
- Release notes gained a bullet for it.

**Next step:** the user installs build 637 and plays the opening movie. Expect `[d3d9-stretch] ... ok kind=stretch` for the
1920x1080 -> 1154x810 calls, no more REJECTED, `[d9-plain-lock] N readback(s) skipped`, and `[d9-drain]` nearly silent
during the movie. If the picture shows, try both `rgb32` and `argb32` and consider making the one that works the fork's
default for this game. If the picture is still black with `kind=stretch`, look at the Stretch render pass into a
BGRA8 plain texture with a dst origin offset (0,81) (EmitStretchBlitOp_d9) and the X8R8G8B8 alpha handling.

**Build 637's log (2026-11-07 16:06, argb32):** the movie shows. Every frame's StretchRect is `ok kind=stretch`;
`[d9-plain-lock]` skipped the readbacks after the first two (one per EVR frame surface). The user reports stutter and the
picture "occasionally goes back a few frames". The log shows:
- 16:06:37.0-39.5: thread-pool worker 00e4 at a full core allocates ~390 frames of 1920x1080 BGRA (0x7f0000 guest
  commits, `[mem-burst]` +350 MB per 250 ms, `[laa] SPILL` past the low 2 GB; phys 2.4 -> 5.0 GB, 2 GB compressed).
  The census shows only ~12 LockRects (copier copies into EVR samples) in that time, so ~380 frames were queued
  upstream of the copier, not drawn.
- 16:06:39.5-48.9: process nearly idle (~9 s). 16:06:48.9 on: worker 00d8 at ~60% of a core while phys drains
  5.0 -> 1.8 GB (the backlog consumed); from 16:06:55.8 00e4 decodes steadily (~25% core) alongside.
- `[d3d9-census]` summary 2: 100 presents in 13.2 s (~7.6/s for a 29.97 fps movie), one LockRect per present.
- DXMT is not the cause: Unlock uploads ride the ordered op stream (stageTextureUpload -> QueueBlitOp), so a frame's
  upload cannot overtake an earlier StretchRect; no `[d9-upload-commit] commits=` line, i.e. the upload ring stayed
  under 64 MB between presents.
- Pipeline (from Wine source at the pinned commit): winegstreamer media source (memory samples, one per RequestSample)
  -> media session -> sample copier MFT (MFCreateSampleCopierMFT, into the EVR stream sink allocator's D3D samples)
  -> EVR stream sink -> mixer (dxva2 VideoProcessBlt; drops a sample it refuses, NOTACCEPTING, silently) -> presenter
  (waits while pts - clock > 3*threshold, presents late frames immediately, never drops; no repaint implemented).
  Reading it did not show which link issues ~390 source requests, or how frames present out of order.

**Build 638:** `tools/patch-wine-mf-video-diag.py` (logging only, ERR lines `[mf-diag]`, rate-limited, each with
t=GetTickCount): source samples per stream with pts; session requests to source streams; sink requests; samples queued
in front of a transform that refused input (depth at powers of two); samples reaching a sink with no request; EVR mixer
refusals and samples ignored while not running; presenter shows (pts, clock, ahead/late, queue depth), BACKWARDS frames,
waits >= 100 ms, presenter allocator empty. Next: read the `[mf-diag]` lines from a 30 s run of the opening movie and
fix the link that misbehaves.

## Mobile data and Steam Cloud (build 639)

Reported 2026-11-07: on mobile data BCD Duo "disconnects me from mobile data and then refuses to launch as it can't check
Steam Cloud". Cause: a Dock start (`startDock` in ContentView.swift, the path Steam games take) enabled JIT before the
Steam Cloud check. With no Wi-Fi, enabling JIT runs the Madeira JIT shortcut, which turns Mobile Data off (LocalDevVPN's
loopback does not work over cellular) and turns it back on only inside the launch, after the debugger detaches
(`JITNetworkShortcut.restoreBlocking`). The cloud check then ran with no network, failed, held the start, and the launch
that would have restored Mobile Data never ran (`restoreLeftover` puts it back only at the next app start). Fix: the
Dock start checks Steam Cloud first (as the library's direct start already did), and the start JIT resumes passes
`cloudChecked: true` so it is not asked again ("Play anyway" stays honoured). tests/host/check-bcd-launch.py,
check-jit-method.py and check-dock-start-screen.py updated for the order and signature.

## Other open items

- Split screen in the upright position and cover-screen layout still need testing by the user.
- Star Force Enigma's crash is an upstream issue, not a fork one.
