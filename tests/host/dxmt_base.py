"""The dxmt revision the fork's winemetal / dxmt / airconv patch checks start from.

madeira-bcd: since the 2026-10-15 upstream merge the dxmt pin (128b297) carries
the fork's patches that went upstream (willfaust/dxmt #9 and #21-#33), so a
check that applies a patch to the pin as committed would find it already there.
Those checks start from OVERLAY_BASE, the pin the patches were written against,
instead: rev() names it (fetched into the dxmt checkout when a shallow clone
lacks it) when the pin carries the wave, else HEAD as before. CI builds from the
new pin plus the same patches, which leaves the same files (the pin's own
additions are those patches' output; checked when the merge was made).
"""
from pathlib import Path
import subprocess

OVERLAY_BASE = 'db546ee466ad50ec6463edb5fa183de11db43761'
PIN_MARKER = 'madeira_rp_lean_on'   # willfaust/dxmt#26, in the wave's pin and later
# BCD (2026-10-29): the public export points the dxmt submodule at thebcd69/bcd-dxmt, whose history does not carry OVERLAY_BASE
# (the fork's tenth build: "upload-pack: not our ref"); the project's own repositories do. A checkout whose origin lacks the
# commit fetches it from them, in this order.
FALLBACK_REMOTES = ['https://github.com/willfaust/dxmt.git', 'https://github.com/3Shain/dxmt.git']


def rev(dxmt):
    dxmt = Path(dxmt)
    wm = dxmt / 'src/winemetal/unix/winemetal_unix.c'
    if not wm.is_file() or PIN_MARKER not in wm.read_text(errors='ignore'):
        return 'HEAD'
    have = subprocess.run(['git', '-C', str(dxmt), 'cat-file', '-e', OVERLAY_BASE + '^{commit}'], capture_output=True)
    if have.returncode:
        for remote in ['origin'] + FALLBACK_REMOTES:
            r = subprocess.run(['git', '-C', str(dxmt), 'fetch', '-q', '--depth=1', remote, OVERLAY_BASE], capture_output=True, text=True)
            if r.returncode == 0:
                break
        else:
            raise SystemExit(f"dxmt_base: {OVERLAY_BASE} is in none of origin, {', '.join(FALLBACK_REMOTES)}: {r.stderr.strip()[-300:]}")
    return OVERLAY_BASE
