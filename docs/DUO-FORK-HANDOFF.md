# BCD Duo fork: handoff notes

Last updated 2026-11-10 (build 643). This file is for whoever picks up the fork next, whether that's a person or a new
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
  **643**). The release is tagged `v0.1.<build>`.
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

**Build 639's [mf-diag] log (2026-11-07 19:28, opening movie ~30 s):** user confirmed the mobile-data fix works.
- The video source sent a 1920x1080 sample about every 5 ms from the start while the EVR took one every 33 ms; the
  sample copier's input queue in the session reached 256 (`transform ... refused a sample: queued, depth 256`).
  3424 session requests to source streams in the run. Presenter allocator empty 32x; presenter waits of 1.2 s.
- 45 `presenter BACKWARDS` lines: pts 233 ms shown after 5572 ms (the start backlog), then repeatedly 30-600 ms back,
  each right after a small burst of queued copier input.
- Cause 1 (order): `transform_node_push_sample` offers every arriving sample to the transform (ProcessInput) even while
  older samples wait in that input's queue; the copier takes one sample at a time, so whenever it was free the newest
  frame jumped the queue and the older ones were fed afterwards.
- Cause 2 (over-requesting): `transform_node_deliver_samples` requests upstream whenever the transform needs input and
  its queue is empty, without tracking requests already in flight (the source answers asynchronously); every
  allocator-release callback (`session_deliver_pending_samples`) and arrival repeated it.

**Build 640:** `tools/patch-wine-mf-session-order.py` (dlls/mf/session.c): per input `madeira_pending` (requests in
flight, decremented by any arrival from upstream including NULL/EOS, cleared on flush) and `madeira_replay` (the sample
being delivered was popped from the input's own queue). Arrivals go behind queued samples; a replayed one is offered
first and goes back to the head if refused; upstream is asked only with no request in flight. Runs before
patch-wine-mf-video-diag.py, which now also recognises the patched push_sample (its queue line reads
`has N sample(s) waiting`). Not compiled locally (Wine's generated headers are not available here); built by CI.
Next: on 640, expect no `presenter BACKWARDS`, queue depths of 1-2, source video samples at ~30/s, and memory flat.
If a movie stalls, `madeira_pending` stuck above 0 is the first suspect (a request lost without a flush).

**Build 640's log (2026-11-07 21:36, opening movie ~32 s): fixed.** Presenter showed frame #960 at pts 32032 ms with
the clock at 32006 ms (every frame ~25 ms ahead, threshold 8 ms, queue 1); no waits, nothing late, no BACKWARDS, no
samples queued in front of the copier; source video samples at the movie's rate (960 in 31.9 s); D3D9 census 99 frames
in 3.27 s; footprint 1.5-1.8 GB with no [mem-burst] (was 4.6-5.0 GB). One sample refused by the mixer at the start
(pts 233). The log's first lines are a carried-over tail of the previous session (t=114495...), not this run.
Open: trim [mf-diag] to its anomaly lines (BACKWARDS, >1 s late, waits, queue depth, refused/ignored/unrequested,
presenter allocator empty) and drop the routine per-sample/request/show lines; the user was asked, not yet decided.
Also offered: argb32 as Chrono Trigger's default video format; point BCDUpdateCheck at the fork's source
(it reads thebcd69.github.io's, which is at 634).

## Touch editor: a drag no longer opens the panel (build 641)

Reported 2026-11-08: editing controls upright on the Duo's large screen, a dragged button went under the control's panel.
`BCDTouchControl.editDrag` (BCDTouchControls.swift) selected the control on touch-down, which opens the panel docked
top or bottom (BCDTouchPanelPlacement, re-docked live as the control moves). Now a touch that moves 8 pt or more is a
drag: any open panel is put away (`select(nil)`) while the control moves, and on release the panel comes back only if it
was this control's (docked clear of its new place; its tab resets to the default for the action). A touch that never
moves that far is a press and selects it on release. A locked control still moves nothing and is selected on release.
The pinch still resizes the selected control, so a control is pressed before it is pinched. check-bcd-touch.py passes.

## Cleanups (build 642) and ideas not yet done (2026-11-10)

Build 642: [mf-diag] trimmed to its anomaly lines (`ROUTINE` in tools/patch-wine-mf-video-diag.py lists the edits left
out; empty it to get the per-sample lines back); `BCDUpdateCheck.sourceURL` reads the fork's source
(pgrace34.github.io/bcd-games/source.json; check-update-check.py updated); the foldable settings note no longer says
"not tried on a real device". User confirmed build 641's drag change was requested and builds 637-641 work.

Ideas discussed with the user, none started:
- Video default: the decoder's default output is NV12 and DXMT's D3D9 has no NV12, so Chrono Trigger needs
  `env.MADEIRA_WG_VIDEO_FORMAT = argb32` by hand. A general fix would let a D3D9 EVR game negotiate a format it can show.
  First step: the user clears that setting on a current build, plays the opening movie and sends the log.
- Upstreaming: the session-order, StretchRect-plain, plain-lock, cloud-before-JIT and editor-drag changes to
  thebcd69/bcd-games; the session faults also exist in Wine 11.21's dlls/mf/session.c. The user opens the PRs.
- Touch layouts per screen shape (positions are fractions of the screen; one layout serves open, split and cover).
- Do not add fork-only rules to recommended/recommendations.json: the app takes BCD's online list only if it keeps
  every built-in rule, so a fork-only rule would make it reject BCD's updates.
- Mega Man Star Force Legacy Collection (64-bit, Enigma): two logs of build 631 (2026-11-06), same stop both times:
  c0000005 reading 0 at 0x14fb15761, in the first block after the entry point 0x14fb156c4. State there: RCX=0,
  RAX=0x14fb15d53 (end pointer), RBP=image base, i.e. the entry stub's decrypt loop had just finished; the previous
  host block exits to 0x14fb15750 / 0x14fb15761, both mid-instruction in the now-decrypted code (0x14fb1575f is
  `mov rax, 0xb`). The emulator translated the bytes after the loop before they were decrypted, the same fault as
  recommendations.json's resident-evil-5 rule. To try (no build needed): `env.FEX_MULTIBLOCK = 0` and
  `env.MADEIRA_X86_FAULT_DUMP = 1` in the game's own settings; the user was asked to test and send a log.
  Result (build 641, 2026-11-10 08:34): FEX_MULTIBLOCK = 0 gets past the stub. The game runs 13 s, creates its D3D12
  device, a 1920x1080 swapchain and two compute pipelines (CS_ZeroFill, CS_FastClear), calls
  ID3D12PipelineState::GetCachedBlob (unimplemented, E_NOTIMPL) and in the same instant starts its own CrashReport.exe
  and exits 0 (black window). Same pattern as the resident-evil-requiem rules; next to try in the game's settings:
  `d3d12-pso-cached-blob = 1` (Direct3D 12 > Cached blob of a pipeline). Side notes from that log: the JIT pool was
  shrunk to 256 MB ("no hole fits a 896MB pool") and CrashReport.exe then failed to load SHELL32 (pool exhausted).
  Result with d3d12-pso-cached-blob = 1 (build 641, 08:39): GetCachedBlob answers, the game goes on (1434 compute
  shaders converted, 0 unimplemented D3D12 calls) and ~0.1 s after the blob answer thread 00d0 faults in the game's own
  code: c0000005 reading 0x18 at exe rva 0x6a9511c. That is the tail of a hash-map lookup (function at rva 0x6a95040:
  map in rcx, key pointer in rdx, node = {next, hash, key, value}); a miss falls into `mov rax,[rax+18h]` with rax = 0,
  so the game assumes the key is present. Caller (ret rva 0x6a3f842): `if (obj[0x130]) v = lookup(obj[0x100]->[8],
  obj[0x90]->[0x18])`. The unhandled-exception filter then starts CrashReport.exe; frames=0. No volatile metadata is
  loaded for the exe (full TSO), so ordering is not the suspect. Cause unknown; no existing switch covers it. The user
  was told this is the uncertain point: finding it needs diagnostic builds (deeper guest stack, what the game does with
  the blob) with no promise of a fix. Parked unless the user asks to continue.

## Build 643 (2026-11-10): per-shape touch layouts and one Star Force diagnostic round

Build 642 was cancelled by this push; 643 contains it.

**Touch controls per screen shape** (asked for by the user). `BCDDuoLayouts` (BCDDisplayLive.swift): shapes `wide`,
`cover-wide`, `upright`, `split`, `cover` from the root view's size (inner display >= 600 pt on its short side) and
BCDDuoSplit. In a library session `TouchControlsModel.shapeChanged(to:)` puts the controls on screen away under the old
shape and brings back the new shape's own (a shape with none yet keeps the ones on screen). Called from
BCDDisplayWatch.rootSizeChanged (before ControlPresetsModel.displayChanged) and at session start; kept per game in
`LibraryEntry.controlShapes` / `controlShapeLayouts` (written by saveCurrentProfile); cleared at session end. Gate:
BCDFoldable.active and the switch `bcd.duo.layouts.per-shape` (Settings > Graphics > Foldable iPhone, default on).
Log: `[duo-layouts] shape A -> B: ...`. Not compiled locally. Not handled: turning the split-screen switch mid-session
does not re-sync until the next size change.

**Star Force diagnostics (the one round agreed with the user; stop after it unless it points at a fix):**
- signal_arm64_ios.c: the guest stack walk reads 32 KB instead of 2 KB (the faulting function's caller has a 2.7 KB
  frame, so only one return address was found) and reports up to 24 returns; `[guest-ctx] reg <name>=...` dumps the 96
  bytes each live guest register points at, plus the image rva of its first qword (a vtable).
- madeira_d3d12.c: `[pso-blob]` lines: every GetCachedBlob call (pipeline, result, size) and any cached blob handed
  back to CreateGraphics/ComputePipelineState.
- Two experiments (game settings, Direct3D 12): `d3d12-pso-cached-blob-size = N` (blob of N bytes instead of 16) and
  `d3d12-shader-cache-none = 1` (the shader-cache feature answers NONE, so an engine that asks first skips GetCachedBlob).
- Runs to ask for: (1) current settings; (2) + `d3d12-pso-cached-blob-size = 65536`; (3) cached-blob off and
  `d3d12-shader-cache-none = 1`. Read: `[pso-blob]`, `[guest-ctx] ret #n`, `[guest-ctx] reg`.

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
