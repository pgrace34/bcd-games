#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# BCD Duo fork (pgrace34/bcd-games), added 2026-11-07. Based on BCD by thebcd69; see LICENSE-BCD.md.
"""[d3d9-stretch]: what each Direct3D 9 StretchRect was asked to do and what it did (log only).

Chrono Trigger (Steam, 32-bit, OpenGL) plays its anime movies through Wine's EVR: the mixer copies each decoded frame
into a plain offscreen surface, dxva2's VideoProcessBlt ColorFills the mixer's output black and StretchRects the frame
into it, and the presenter StretchRects that into its swapchain's back buffer. With env.MADEIRA_WG_VIDEO_FORMAT = rgb32
the movie plays (sound, 900 presents in 24 s) but the screen stays black. A planner rejection in either StretchRect
returns D3DERR_INVALIDCALL without a word (dxva2 only WARNs, and warn+dxva2 is off), and the frame stays the ColorFill's
black. These lines say which call it is, if any:

  [d3d9-stretch] #3 ok kind=stretch src 1920x1080 fmt=22 pool=0 usage=0x0 rect=(0,0 1920x1080) -> dst 1154x810 fmt=22 ...
  [d3d9-stretch] #4 REJECTED planner src ... -> dst ... (the frame stays as the destination was)

The first 24 calls and the first 48 rejections are written (a rejection also counts in a summary every 4096 calls);
nothing is changed: every call takes the path it took before. PE side: every d3d9 built from this source, the i386
d3d9.dll included (the i386 farm key hashes tools/patch-dxmt-*.py). Idempotent (marker "d9-stretch-log-v1"); exits 1
naming the anchor when one moved, the files unchanged. Run from the repository root.
"""
import pathlib
import sys

F = pathlib.Path("dxmt/src/d3d9/d3d9_device.cpp")
MARKER = "d9-stretch-log-v1"

HELPER_OLD = """HRESULT STDMETHODCALLTYPE
MTLD3D9Device::StretchRect(
"""
HELPER_NEW = """/* madeira-bcd d9-stretch-log-v1 (BCD Duo fork, Chrono Trigger's black movie; tools/patch-dxmt-d9-stretch-log.py):
 * [d3d9-stretch] says what a StretchRect was asked to do and whether the planner took it. Log only. */
static void madeira_d9_stretch_log(const char *verdict, const char *kind, const D3DSURFACE_DESC &sd, const D3DSURFACE_DESC &dd,
                                   const RECT *sr, const RECT *dr, D3DTEXTUREFILTERTYPE filter, bool from_readback,
                                   bool rejected) {
  static std::atomic<uint32_t> calls{0}, rejections{0};
  const uint32_t n = calls.fetch_add(1, std::memory_order_relaxed) + 1;
  uint32_t r = rejected ? rejections.fetch_add(1, std::memory_order_relaxed) + 1 : rejections.load(std::memory_order_relaxed);
  if ((n % 4096) == 0) {
    char sum[160];
    std::snprintf(sum, sizeof(sum), "[d3d9-stretch] %u calls so far, %u rejected", n, r);
    Logger::info(sum);
  }
  if (!(n <= 24 || (rejected && r <= 48)))
    return;
  char srs[48], drs[48];
  if (sr)
    std::snprintf(srs, sizeof(srs), "(%ld,%ld %ldx%ld)", (long)sr->left, (long)sr->top, (long)(sr->right - sr->left),
                  (long)(sr->bottom - sr->top));
  else
    std::snprintf(srs, sizeof(srs), "full");
  if (dr)
    std::snprintf(drs, sizeof(drs), "(%ld,%ld %ldx%ld)", (long)dr->left, (long)dr->top, (long)(dr->right - dr->left),
                  (long)(dr->bottom - dr->top));
  else
    std::snprintf(drs, sizeof(drs), "full");
  char buf[512];
  std::snprintf(buf, sizeof(buf),
                "[d3d9-stretch] #%u %s %s src %ux%u fmt=%u pool=%u usage=%#lx type=%u rect=%s -> dst %ux%u fmt=%u pool=%u "
                "usage=%#lx type=%u rect=%s filter=%u%s%s",
                n, verdict, kind, sd.Width, sd.Height, (unsigned)sd.Format, (unsigned)sd.Pool, (unsigned long)sd.Usage,
                (unsigned)sd.Type, srs, dd.Width, dd.Height, (unsigned)dd.Format, (unsigned)dd.Pool,
                (unsigned long)dd.Usage, (unsigned)dd.Type, drs, (unsigned)filter, from_readback ? " readback" : "",
                rejected ? " (the frame stays as the destination was)" : "");
  Logger::info(buf);
}

HRESULT STDMETHODCALLTYPE
MTLD3D9Device::StretchRect(
"""

LOCKED_OLD = """  // are GPU copies that must not straddle an outstanding CPU map).
  if (src->locked() || dst->locked())
    return D3DERR_INVALIDCALL;
  const D3DSURFACE_DESC &sd = src->desc();
  const D3DSURFACE_DESC &dd = dst->desc();
"""
LOCKED_NEW = """  // are GPU copies that must not straddle an outstanding CPU map).
  if (src->locked() || dst->locked()) {
    madeira_d9_stretch_log("REJECTED", src->locked() ? "src-locked" : "dst-locked", src->desc(), dst->desc(), pSourceRect,
                           pDestRect, Filter, from_readback, true);   // madeira-bcd d9-stretch-log-v1
    return D3DERR_INVALIDCALL;
  }
  const D3DSURFACE_DESC &sd = src->desc();
  const D3DSURFACE_DESC &dd = dst->desc();
"""

TEX_OLD = """  Rc<dxmt::Texture> src_tex = src->dxmtTexture();
  Rc<dxmt::Texture> dst_tex = dst->dxmtTexture();
  if (!src_tex || !dst_tex)
    return D3DERR_INVALIDCALL;

  // Reduce both participants to plain values and let the shared planner
"""
TEX_NEW = """  Rc<dxmt::Texture> src_tex = src->dxmtTexture();
  Rc<dxmt::Texture> dst_tex = dst->dxmtTexture();
  if (!src_tex || !dst_tex) {
    madeira_d9_stretch_log("REJECTED", !src_tex ? "src-no-texture" : "dst-no-texture", sd, dd, pSourceRect, pDestRect, Filter,
                           from_readback, true);   // madeira-bcd d9-stretch-log-v1
    return D3DERR_INVALIDCALL;
  }

  // Reduce both participants to plain values and let the shared planner
"""

PLAN_OLD = """      dxmt::plan_stretch_rect(plan_src, plan_dst, pSourceRect, pDestRect, Filter, m_inScene, from_readback);
  if (FAILED(plan.hr)) {
"""
PLAN_NEW = """      dxmt::plan_stretch_rect(plan_src, plan_dst, pSourceRect, pDestRect, Filter, m_inScene, from_readback);
  {   // madeira-bcd d9-stretch-log-v1
    const char *kind = FAILED(plan.hr)                                 ? "planner"
                       : plan.kind == dxmt::StretchRectKind::Copy       ? "kind=copy"
                       : plan.kind == dxmt::StretchRectKind::Stretch    ? "kind=stretch"
                                                                        : "kind=other";
    madeira_d9_stretch_log(FAILED(plan.hr) ? "REJECTED" : "ok", kind, sd, dd, pSourceRect, pDestRect, Filter, from_readback,
                           FAILED(plan.hr));
  }
  if (FAILED(plan.hr)) {
"""


def main() -> int:
    if not F.is_file():
        print(f"patch-dxmt-d9-stretch-log: {F} not found (run from the repository root with the dxmt submodule)")
        return 1
    text = F.read_text()
    if MARKER in text:
        print("patch-dxmt-d9-stretch-log: already patched")
        return 0
    for name, old in (("StretchRect definition", HELPER_OLD), ("locked() gate", LOCKED_OLD),
                      ("dxmtTexture guard", TEX_OLD), ("plan_stretch_rect call", PLAN_OLD)):
        if text.count(old) != 1:
            print(f"patch-dxmt-d9-stretch-log: anchor moved: {name} (found {text.count(old)} times)")
            return 1
    text = text.replace(HELPER_OLD, HELPER_NEW).replace(LOCKED_OLD, LOCKED_NEW).replace(TEX_OLD, TEX_NEW)
    text = text.replace(PLAN_OLD, PLAN_NEW)
    if "<atomic>" not in text:
        text = "#include <atomic>\n" + text
    if "<cstdio>" not in text:
        text = "#include <cstdio>\n" + text
    F.write_text(text)
    print("patch-dxmt-d9-stretch-log: patched (log only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
