#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# BCD Duo fork (pgrace34/bcd-games), added 2026-11-13. Based on BCD by thebcd69; see LICENSE-BCD.md.
"""Fast forward: NtQueryPerformanceCounter returns the game's clock (build/ntdll-unix/bcd_ff_ios.c).

QueryPerformanceCounter, timeGetTime, GetTickCount and GetTickCount64 all reach the unix side here (see
bcd_ff_ios.c). The counter goes through bcd_ff_time(), which is the real counter until the app first changes the
speed (BCDFastForward.swift), so a run that never uses fast forward reads exactly what it read before.

Usage: patch-wine-ff-qpc.py wine/dlls/ntdll/unix/sync.c. Idempotent (marker "madeira-bcd: ff-qpc"); exits 1 naming
the anchor that moved, the file unchanged.
"""
import pathlib
import sys

F = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/sync.c")
MARKER = "madeira-bcd: ff-qpc"
OLD = """NTSTATUS WINAPI NtQueryPerformanceCounter( LARGE_INTEGER *counter, LARGE_INTEGER *frequency )
{
    __sync_fetch_and_add( &ios_qpc_syscalls, 1 );
    counter->QuadPart = monotonic_counter();
"""
NEW = """/* madeira-bcd: ff-qpc (BCD Duo fork, tools/patch-wine-ff-qpc.py): the game's clock, faster while fast forward is held */
extern ULONGLONG bcd_ff_time( ULONGLONG real );
NTSTATUS WINAPI NtQueryPerformanceCounter( LARGE_INTEGER *counter, LARGE_INTEGER *frequency )
{
    __sync_fetch_and_add( &ios_qpc_syscalls, 1 );
    counter->QuadPart = bcd_ff_time( monotonic_counter() );
"""


def main() -> int:
    if not F.is_file():
        print(f"patch-wine-ff-qpc: {F} not found")
        return 1
    text = F.read_text()
    if MARKER in text:
        print("ff-qpc: already applied")
        return 0
    if text.count(OLD) != 1:
        print(f"patch-wine-ff-qpc: anchor moved: NtQueryPerformanceCounter (found {text.count(OLD)} times)")
        return 1
    F.write_text(text.replace(OLD, NEW))
    print("ff-qpc: applied (NtQueryPerformanceCounter reads bcd_ff_time)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
