#!/usr/bin/env python3
"""DXMT's tessellation helper shader compiles with Xcode 27's Metal compiler.

BCD (2026-10-30). airconv's air_tessellation.metal took the next workload index with the compiler's internal
__metal_atomic_fetch_add_explicit(pointer, 1, order, scope). Xcode 27.1's Metal compiler (metalfe-32023.921) gives that
builtin five parameters and refuses the call ("too few arguments to function call, expected 5, have 4"), so the shader
headers step stopped there (the fork's build on GitHub's xcode-27 image). Upstream DXMT (3Shain/dxmt, main) already takes
the index through the public API, atomic_fetch_add_explicit on the pointer reinterpreted as a threadgroup atomic_int, which
every Metal compiler accepts; this applies that one line to the pin. Idempotent; fails by name if the anchor moved. Run
from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/airconv/shaders/air_tessellation.metal")
OLD = ("  return __metal_atomic_fetch_add_explicit(out_count, 1, int(memory_order_relaxed), "
       "__METAL_MEMORY_SCOPE_THREADGROUP__);\n")
NEW = ("  return atomic_fetch_add_explicit(reinterpret_cast<threadgroup atomic_int *>(out_count), 1, memory_order_relaxed);"
       "   // BCD: as upstream DXMT; Xcode 27's Metal compiler refuses the internal builtin\n")


def main():
    s = PATH.read_text()
    if NEW in s:
        print(f"{PATH}: already patched")
        return
    if s.count(OLD) != 1:
        sys.exit(f"patch-dxmt-tess-atomic: anchor not found once in {PATH}: {OLD[:60]!r}")
    PATH.write_text(s.replace(OLD, NEW, 1))
    print(f"{PATH}: patched")


main()
