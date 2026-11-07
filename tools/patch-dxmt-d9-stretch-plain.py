#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# BCD Duo fork (pgrace34/bcd-games), added 2026-11-07. Based on BCD by thebcd69; see LICENSE-BCD.md.
"""Let a Direct3D 9 StretchRect scale into a DEFAULT-pool offscreen-plain surface, as DXVK does.

Chrono Trigger (Steam, 32-bit, OpenGL) plays its anime movies through Wine's EVR. dxva2's VideoProcessBlt ColorFills the
mixer's output surface black and then StretchRects each 1920x1080 frame into it, scaled to the window (1154x810 on the
Duo). That output surface is a plain offscreen surface (CreateOffscreenPlainSurface, DEFAULT pool, Usage 0), and DXMT's
planner (dxmt/src/d3d9/d3d9_stretchrect.hpp, plan_stretch_rect) rejects any size-changing StretchRect into a destination
without D3DUSAGE_RENDERTARGET. Build 636's [d3d9-stretch] lines showed it: every frame's stretch REJECTED planner, so the
frame stayed the ColorFill's black while the sound played.

Native D3D9 does reject that stretch, but wined3d and DXVK both allow it (DXVK: "works fine in practice"), and Wine's own
EVR relies on it. DXMT already creates a DEFAULT-pool offscreen-plain texture with Metal RenderTarget usage so the
render-pass Stretch can target it (a same-size format-converting stretch into such a surface already takes that path),
so only the planner's gate changes: a standalone plain destination now takes the scale too. Block-compressed plain
surfaces carry no RenderTarget usage and stay rejected by the planner's later compressed check.

PE side: every d3d9 built from this source, the i386 d3d9.dll included (the i386 farm key hashes tools/patch-dxmt-*.py).
Idempotent (marker "d9-stretch-plain-v1"); exits 1 naming the anchor when it moved, the file unchanged. Run from the
repository root.
"""
import pathlib
import sys

F = pathlib.Path("dxmt/src/d3d9/d3d9_stretchrect.hpp")
MARKER = "d9-stretch-plain-v1"

GATE_OLD = """  if (extent_mismatch && !(dst.usage & D3DUSAGE_RENDERTARGET))
    return fail();
"""
GATE_NEW = """  // madeira-bcd d9-stretch-plain-v1 (BCD Duo fork, tools/patch-dxmt-d9-stretch-plain.py): a standalone DEFAULT-pool
  // offscreen-plain destination takes the scale as well, as DXVK and wined3d allow. CreateOffscreenPlainSurface gives
  // its Metal texture RenderTarget usage, so the sampler-scale render pass can target it. Wine's EVR (dxva2
  // VideoProcessBlt) scales every movie frame into such a surface; rejecting it left Chrono Trigger's movies black.
  if (extent_mismatch && !(dst.usage & D3DUSAGE_RENDERTARGET) && !dst_plain)
    return fail();
"""

# dst_plain must already be in scope above the gate (it is declared for the plain-destination source rule).
SCOPE_ANCHOR = "  const bool dst_plain = !dst.is_texture_subresource && !(dst.usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL));\n"


def main() -> int:
    if not F.is_file():
        print(f"patch-dxmt-d9-stretch-plain: {F} not found (run from the repository root with the dxmt submodule)")
        return 1
    text = F.read_text()
    if MARKER in text:
        print("patch-dxmt-d9-stretch-plain: already patched")
        return 0
    for name, old in (("non-render-target stretch gate", GATE_OLD), ("dst_plain declaration", SCOPE_ANCHOR)):
        if text.count(old) != 1:
            print(f"patch-dxmt-d9-stretch-plain: anchor moved: {name} (found {text.count(old)} times)")
            return 1
    if text.index(SCOPE_ANCHOR) > text.index(GATE_OLD):
        print("patch-dxmt-d9-stretch-plain: anchor moved: dst_plain is declared after the gate")
        return 1
    F.write_text(text.replace(GATE_OLD, GATE_NEW))
    print("patch-dxmt-d9-stretch-plain: patched (plain DEFAULT-pool destinations take a scaled StretchRect)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
