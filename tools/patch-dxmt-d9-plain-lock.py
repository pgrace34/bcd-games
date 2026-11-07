#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# BCD Duo fork (pgrace34/bcd-games), added 2026-11-07. Based on BCD by thebcd69; see LICENSE-BCD.md.
"""Skip the GPU readback when a Direct3D 9 plain offscreen surface is locked and the GPU has not written it since.

Chrono Trigger's anime movies play through Wine's EVR: every decoded 1920x1080 frame is written into a DEFAULT-pool
offscreen-plain surface through LockRect / UnlockRect, and the GPU only ever reads that surface (the StretchRect source).
DXMT's LockRect refreshes the host mirror of every non-DYNAMIC DEFAULT surface from the Metal texture before handing out
the pointer, which drains the GPU and waits for it. Build 636 logged one such readback per movie frame
([rb-tex] fmt 80 1920x1080 x4096, [d9-drain] avg-us=9500): about 10 ms of every frame, which made the sound choppy.

The mirror only needs that refresh when the GPU wrote the texture after the mirror was last in step with it. For a
standalone plain surface (no RENDERTARGET / DEPTHSTENCIL usage, not a texture level) the GPU can write it only through
ColorFill, StretchRect (GetRenderTargetData's DEFAULT-pool copy included) and UpdateSurface: SetRenderTarget rejects it,
GetFrontBufferData takes CPU pools only and ComposeRects is a stub. Each of those writers ends in
dst->flagContainerAutoGenDirty() in d3d9_device.cpp (4 sites), so each now also calls dst->madeiraNoteGpuWrite(). The
surface remembers that its mirror is current after a successful readback; UnlockRect's upload keeps the two in step;
a GPU write clears it. While it is current, LockRect skips the readback. This is the shape of DXVK's per-subresource
NeedsReadback tracking. Render targets, depth surfaces and texture levels keep the readback on every lock, as before.

env MADEIRA_D9_PLAIN_LOCK_READBACK=1 restores a readback on every lock. [d9-plain-lock] counts the skips (first 2 lines,
then every 4096th).

PE side: every d3d9 built from this source, the i386 d3d9.dll included (the i386 farm key hashes tools/patch-dxmt-*.py).
Idempotent (marker "d9-plain-lock-v1" in each file); exits 1 naming the anchor when one moved, no file changed. Run from
the repository root.
"""
import pathlib
import re
import sys

MARKER = "d9-plain-lock-v1"
HPP = pathlib.Path("dxmt/src/d3d9/d3d9_surface.hpp")
CPP = pathlib.Path("dxmt/src/d3d9/d3d9_surface.cpp")
DEV = pathlib.Path("dxmt/src/d3d9/d3d9_device.cpp")

HPP_METHOD_OLD = """  void flagContainerAutoGenDirty();
"""
HPP_METHOD_NEW = """  void flagContainerAutoGenDirty();
  // madeira-bcd d9-plain-lock-v1 (BCD Duo fork, tools/patch-dxmt-d9-plain-lock.py): the device's GPU writers (ColorFill,
  // StretchRect, UpdateSurface) call this on their destination, so LockRect knows the host mirror is stale again.
  void
  madeiraNoteGpuWrite() {
    m_madeira_mirror_current = false;
  }
"""

HPP_MEMBER_OLD = """  bool m_isImplicitLosable = false;
"""
HPP_MEMBER_NEW = """  bool m_isImplicitLosable = false;
  // madeira-bcd d9-plain-lock-v1: a standalone plain DEFAULT surface's host mirror matches its Metal texture (set after a
  // readback, kept by UnlockRect's upload, cleared by madeiraNoteGpuWrite). LockRect skips the readback while it holds.
  bool m_madeira_mirror_current = false;
"""

CPP_LOCK_OLD = """  if (m_buffer == nullptr && m_texture != nullptr && m_desc.Pool == D3DPOOL_DEFAULT &&
      !(m_desc.Usage & D3DUSAGE_DYNAMIC) && !(Flags & D3DLOCK_DISCARD) && !IsNullFormat(m_desc.Format))
    m_device->readbackSurfaceMirror(this);
"""
CPP_LOCK_NEW = """  if (m_buffer == nullptr && m_texture != nullptr && m_desc.Pool == D3DPOOL_DEFAULT &&
      !(m_desc.Usage & D3DUSAGE_DYNAMIC) && !(Flags & D3DLOCK_DISCARD) && !IsNullFormat(m_desc.Format)) {
    // madeira-bcd d9-plain-lock-v1 (BCD Duo fork, tools/patch-dxmt-d9-plain-lock.py): a standalone plain surface (no
    // RT / DS usage, not a texture level) is written by the GPU only through ColorFill, StretchRect and UpdateSurface,
    // which clear m_madeira_mirror_current. While the mirror is current the readback (a GPU drain and wait) is skipped.
    // Wine's EVR locks its frame surface once per movie frame to write the next frame; the GPU only reads it.
    // env MADEIRA_D9_PLAIN_LOCK_READBACK=1 reads back on every lock, as before.
    const bool plain = !isTextureSubresource() && !(m_desc.Usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL));
    static const bool skip_enabled = env::getEnvVar("MADEIRA_D9_PLAIN_LOCK_READBACK") != "1";
    if (plain && skip_enabled && m_madeira_mirror_current) {
      static std::atomic<uint32_t> skipped{0};
      const uint32_t n = skipped.fetch_add(1, std::memory_order_relaxed) + 1;
      if (n <= 2 || !(n % 4096))
        Logger::info(str::format("[d9-plain-lock] ", n, " readback(s) skipped so far; this one: plain surface ", m_desc.Width,
                                 "x", m_desc.Height, " fmt=", static_cast<uint32_t>(m_desc.Format),
                                 ", not written by the GPU since its mirror was last in step"));
    } else {
      const bool synced = m_device->readbackSurfaceMirror(this);
      m_madeira_mirror_current = plain && synced;
    }
  }
"""

DEV_WRITER_RE = re.compile(r"^([ \t]*)dst->flagContainerAutoGenDirty\(\);\n", re.M)
DEV_WRITER_COUNT = 4  # UpdateSurface, StretchRect (stretchRectImpl), ColorFill (2 exits)


def main() -> int:
    for f in (HPP, CPP, DEV):
        if not f.is_file():
            print(f"patch-dxmt-d9-plain-lock: {f} not found (run from the repository root with the dxmt submodule)")
            return 1
    hpp, cpp, dev = HPP.read_text(), CPP.read_text(), DEV.read_text()
    done = [MARKER in t for t in (hpp, cpp, dev)]
    if all(done):
        print("patch-dxmt-d9-plain-lock: already patched")
        return 0
    if any(done):
        print("patch-dxmt-d9-plain-lock: partly patched (marker in some files only); restore the dxmt submodule first")
        return 1
    for name, text, old in (("surface flagContainerAutoGenDirty declaration", hpp, HPP_METHOD_OLD),
                            ("surface m_isImplicitLosable member", hpp, HPP_MEMBER_OLD),
                            ("LockRect mirror readback", cpp, CPP_LOCK_OLD)):
        if text.count(old) != 1:
            print(f"patch-dxmt-d9-plain-lock: anchor moved: {name} (found {text.count(old)} times)")
            return 1
    writers = len(DEV_WRITER_RE.findall(dev))
    if writers != DEV_WRITER_COUNT:
        print(f"patch-dxmt-d9-plain-lock: anchor moved: device GPU writers' dst->flagContainerAutoGenDirty() "
              f"(found {writers}, expected {DEV_WRITER_COUNT}); a new writer must call madeiraNoteGpuWrite too")
        return 1
    if "util_env.hpp" not in cpp:
        print("patch-dxmt-d9-plain-lock: anchor moved: d3d9_surface.cpp no longer includes util_env.hpp")
        return 1

    hpp = hpp.replace(HPP_METHOD_OLD, HPP_METHOD_NEW).replace(HPP_MEMBER_OLD, HPP_MEMBER_NEW)
    cpp = cpp.replace(CPP_LOCK_OLD, CPP_LOCK_NEW)
    if "#include <atomic>" not in cpp:
        cpp = "#include <atomic>\n" + cpp
    dev = DEV_WRITER_RE.sub(
        lambda m: f"{m.group(1)}dst->madeiraNoteGpuWrite();   // madeira-bcd d9-plain-lock-v1\n"
                  f"{m.group(1)}dst->flagContainerAutoGenDirty();\n", dev)
    HPP.write_text(hpp)
    CPP.write_text(cpp)
    DEV.write_text(dev)
    print(f"patch-dxmt-d9-plain-lock: patched ({DEV_WRITER_COUNT} GPU writers mark their destination; "
          "a plain surface's lock skips the readback while its mirror is current)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
