#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Where the VC++ runtime DLLs come from: Documents/vcruntime, only .dll files, and tools/fetch-vcruntime.sh.

  1. madeira_vcruntime_dlls() (WineProcessBridge.m), compiled on the host with Foundation and run over temporary
     folders:
       - no Documents/vcruntime (nil, empty, or a folder that does not exist): exactly the build's .dll files, no
         other file (a README, a .gitkeep or a marker is not linked into system32 and not counted any more);
       - with it: its files shadow the build's of the same name (any letter case), names only it has are added, a
         name outside Microsoft's fourteen (kernel32.dll, ntdll.dll, ...) is ignored, non-.dll files are ignored,
         and the count of names taken from it is right;
       - the build's folder does not exist: only Documents' files, or none;
  2. the call sites: Documents/vcruntime is named only there; the two exemptions (vcruntime140.dll, msvcp140.dll),
     MADEIRA_MFC and MADEIRA_WINE_VCRT keep working; the bundle's folder is still named only in madeira_vcruntime_dir;
  3. tools/fetch-vcruntime.sh: bash syntax; https only; an installer that is not one makes it fail without writing
     anything; with the build's own download (/tmp/vcr.exe, or VCRUNTIME_INSTALLER) it gives the fourteen DLLs byte for
     byte as the build's extraction (this checkout's app/Madeira/x86_64-vcruntime, else ~/madeira-bcd's); skipped, with a
     note, when there is no download or no cabextract;
  4. tests/host/check-vcruntime.py on synthetic folders: no folder / no DLLs pass without --strict and fail with it, a
     missing file, a non-PE file, a truncated certificate table and a wrong optional-header magic fail, a complete signed
     set passes.
"""
import os
import re
import shutil
import subprocess
import struct
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("  " + detail) if detail and not cond else ""))
    if not cond:
        ok = False


bridge = (APP / "WineProcessBridge.m").read_text()
T = Path(tempfile.mkdtemp(prefix="vcrt-sources-"))
try:
    # ---------------------------------------------------------------- 1. the list builder
    fn = re.search(r"static NSArray<NSString \*> \*madeira_vcruntime_dlls\(.*?\n}\n", bridge, re.S)
    check("madeira_vcruntime_dlls is defined", fn is not None)
    cc = shutil.which("clang") or shutil.which("cc")
    if fn and cc:
        harness = T / "h.m"
        harness.write_text(
            "#import <Foundation/Foundation.h>\n" + fn.group(0) + r'''
int main(int argc, char **argv) {
    @autoreleasepool {
        NSString *dir = argv[1][0] ? @(argv[1]) : @"/nonexistent-build-folder";
        NSString *user = argc > 2 && argv[2][0] ? @(argv[2]) : nil;
        NSMutableDictionary<NSString *, NSString *> *home = [NSMutableDictionary dictionary];
        NSUInteger fromDocs = 0;
        NSArray<NSString *> *names = madeira_vcruntime_dlls([NSFileManager defaultManager], dir, user, home, &fromDocs);
        printf("%lu\n", (unsigned long)fromDocs);
        for (NSString *n in [names sortedArrayUsingSelector:@selector(compare:)])
            printf("%s=%s\n", n.UTF8String, home[n.lowercaseString].UTF8String);
    }
    return 0;
}
''')
        b = subprocess.run([cc, "-x", "objective-c", "-fobjc-arc", "-Wall", "-Werror", "-framework", "Foundation",
                            str(harness), "-o", str(T / "h")], capture_output=True, text=True)
        check("it builds on the host", b.returncode == 0, b.stderr[-600:])
        if b.returncode == 0:
            def run(build_files, docs_files, with_build=True, user_mode="docs"):
                d = Path(tempfile.mkdtemp(dir=T))
                build, docs = d / "x86_64-vcruntime", d / "Documents/vcruntime"
                if with_build:
                    build.mkdir()
                    for f in build_files:
                        (build / f).write_bytes(b"build")
                if docs_files is not None:
                    docs.mkdir(parents=True)
                    for f in docs_files:
                        (docs / f).write_bytes(b"docs")
                user = {"docs": str(docs), "nil": "", "missing": str(d / "Documents/nope")}[user_mode]
                r = subprocess.run([str(T / "h"), str(build) if with_build else "", user], capture_output=True, text=True)
                lines = r.stdout.splitlines()
                got = {l.split("=", 1)[0]: l.split("=", 1)[1] for l in lines[1:]}
                return int(lines[0]), got, build, docs

            build_set = ["concrt140.dll", "msvcp140.dll", "vcruntime140.dll", "mfc140.dll", "README.txt", ".gitkeep", "vcruntime140.dll.marker"]
            n, got, build, _ = run(build_set, None, user_mode="nil")
            check("no Documents folder: the build's .dll files, nothing else", n == 0 and got == {
                f: str(build / f) for f in ("concrt140.dll", "msvcp140.dll", "vcruntime140.dll", "mfc140.dll")}, str(got))
            n, got, build, _ = run(build_set, None, user_mode="missing")
            check("a Documents folder that does not exist: the same", n == 0 and sorted(got) == sorted(
                ["concrt140.dll", "msvcp140.dll", "vcruntime140.dll", "mfc140.dll"]), str(got))
            n, got, build, docs = run(build_set, [], user_mode="docs")
            check("an empty Documents/vcruntime: the same", n == 0 and sorted(got) == sorted(
                ["concrt140.dll", "msvcp140.dll", "vcruntime140.dll", "mfc140.dll"]), str(got))
            n, got, build, docs = run(build_set, ["MSVCP140.DLL", "vcamp140.dll", "kernel32.dll", "ntdll.dll", "d3dcompiler_47.dll",
                                                  "notes.txt", "mfc140u.dll"])
            check("Documents' files shadow the build's of the same name, in any letter case",
                  got.get("MSVCP140.DLL") == str(docs / "MSVCP140.DLL") and "msvcp140.dll" not in got, str(got))
            check("  names only Documents has are added (vcamp140.dll, mfc140u.dll)",
                  got.get("vcamp140.dll") == str(docs / "vcamp140.dll") and got.get("mfc140u.dll") == str(docs / "mfc140u.dll"))
            check("  names outside Microsoft's fourteen are ignored (kernel32, ntdll, d3dcompiler_47, notes.txt)",
                  not any(k.lower() in ("kernel32.dll", "ntdll.dll", "d3dcompiler_47.dll", "notes.txt") for k in got), str(got))
            check("  the build's other files stay (concrt140.dll, vcruntime140.dll, mfc140.dll)",
                  all(got.get(f) == str(build / f) for f in ("concrt140.dll", "vcruntime140.dll", "mfc140.dll")))
            check("  and three names came from Documents", n == 3, str(n))
            n, got, _, docs = run([], ["vcruntime140_1.dll"], with_build=False)
            check("no build folder: only Documents' known files", n == 1 and got == {"vcruntime140_1.dll": str(docs / "vcruntime140_1.dll")}, str(got))
            n, got, _, _ = run([], None, with_build=False, user_mode="nil")
            check("no build folder and no Documents: nothing", n == 0 and got == {}, str(got))
    elif not cc:
        print("note no C compiler: part 1 skipped")

    # ---------------------------------------------------------------- 2. the call sites
    check("Documents/vcruntime is named once, at the call", bridge.count('@"vcruntime"') == 1
          and 'stringByAppendingPathComponent:@"vcruntime"], vcrtHome, &vcrtFromDocs)' in bridge)
    check("the bundle's x86_64-vcruntime is still named only in madeira_vcruntime_dir", bridge.count('@"x86_64-vcruntime"') == 1)
    check("one log line says when Documents' DLLs shadow the build's",
          "DLL(s) from Documents/vcruntime shadow the build's" in bridge)
    ov = bridge[bridge.index("int vcrtLinked = 0, vcrtSkipped = 0;"):bridge.index("NSString *src = vcrtHome[[dll lowercaseString]];")]
    check("the two exemptions and MADEIRA_MFC come before the link",
          '@"vcruntime140.dll"' in ov and '@"msvcp140.dll"' in ov and "BOOL mfcOff = mfcEnv && mfcEnv[0] == '0';" in ov)

    # ---------------------------------------------------------------- 3. tools/fetch-vcruntime.sh
    script = R / "tools/fetch-vcruntime.sh"
    text = script.read_text()
    check("fetch-vcruntime.sh is executable and passes bash -n",
          os.access(script, os.X_OK) and subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0)
    check("  it downloads over https only (no http, no 7-Zip)", "--proto '=https'" in text and "--proto-redir '=https'" in text
          and "http://" not in text and "7zz" not in text.replace("7-Zip", "") and "7z " not in text)
    check("  it names the fourteen files of tools/fetch-vcruntime.md",
          len(re.findall(r"^    (?:concrt|msvcp|vc|mfc)\w*\.dll$", text, re.M)) == 14)
    bogus = T / "bogus.exe"
    bogus.write_bytes(b"this is not an installer\n" * 100)
    dest = T / "dest-bogus"
    env = dict(os.environ, VCRUNTIME_INSTALLER=str(bogus), VCRUNTIME_DEST=str(dest))
    have_cab = shutil.which("cabextract") is not None
    if have_cab:
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
        check("an installer that is not one: it fails and writes nothing", r.returncode != 0 and not dest.exists(), r.stdout[-300:] + r.stderr[-300:])
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=dict(env, VCRUNTIME_INSTALLER=str(T / "absent.exe")))
        check("a missing VCRUNTIME_INSTALLER file: it fails and writes nothing", r.returncode != 0 and not dest.exists())
    else:
        print("note no cabextract: the script's runs are skipped")
    installer = os.environ.get("VCRUNTIME_INSTALLER") or "/tmp/vcr.exe"
    refs = [Path(p) for p in [os.environ.get("VCRUNTIME_REFERENCE")] if p] + [APP / "x86_64-vcruntime", Path.home() / "madeira-bcd/app/Madeira/x86_64-vcruntime"]
    ref = next((p for p in refs if (p / "vcruntime140.dll").is_file() and len(list(p.glob("*.dll"))) >= 14), None)
    if have_cab and os.path.getsize(installer) > 1_000_000 if os.path.exists(installer) else False:
        dest = T / "dest-real"
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                           env=dict(os.environ, VCRUNTIME_INSTALLER=installer, VCRUNTIME_DEST=str(dest)))
        got = sorted(p.name for p in dest.glob("*.dll")) if dest.exists() else []
        check("the build's own download: the script gives the fourteen DLLs and its own check passes", r.returncode == 0 and len(got) == 14,
              r.stdout[-300:] + r.stderr[-300:])
        if ref and len(got) == 14:
            diff = [n for n in got if (dest / n).read_bytes() != (ref / n).read_bytes()]
            check("  byte for byte as the build's extraction (%s)" % ref, not diff, str(diff))
        else:
            print("note no reference extraction to compare with")
    else:
        print("note no installer download (%s) or no cabextract: the real run is skipped" % installer)

    # ---------------------------------------------------------------- 4. check-vcruntime.py
    names = ["concrt140.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
             "msvcp140_codecvt_ids.dll", "vcamp140.dll", "vccorlib140.dll", "vcomp140.dll", "vcruntime140.dll",
             "vcruntime140_1.dll", "vcruntime140_threads.dll", "mfc140.dll", "mfc140u.dll"]

    def fake_dll(magic=0x20B, signed=True, pe=True):
        d = bytearray(1024)
        d[0:2] = b"MZ" if pe else b"XX"
        struct.pack_into("<I", d, 0x3C, 0x80)
        d[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", d, 0x80 + 24, magic)
        if signed:
            struct.pack_into("<II", d, 0x80 + 24 + 112 + 4 * 8, 600, 300)
        else:
            struct.pack_into("<II", d, 0x80 + 24 + 112 + 4 * 8, 1024, 0)
        return bytes(d)

    def run_check(files, strict):
        d = Path(tempfile.mkdtemp(dir=T))
        for n, blob in files.items():
            (d / n).write_bytes(blob)
        r = subprocess.run([sys.executable, str(R / "tests/host/check-vcruntime.py")] + (["--strict"] if strict else []),
                           capture_output=True, text=True, env=dict(os.environ, VCRUNTIME_DIR=str(d)))
        return r.returncode

    good = {n: fake_dll() for n in names}
    check("check-vcruntime.py: a complete signed set passes", run_check(good, True) == 0)
    check("  an empty folder passes without --strict and fails with it", run_check({}, False) == 0 and run_check({}, True) == 1)
    r = subprocess.run([sys.executable, str(R / "tests/host/check-vcruntime.py"), "--strict"], capture_output=True, text=True,
                       env=dict(os.environ, VCRUNTIME_DIR=str(T / "no-such-folder")))
    check("  a folder that does not exist: skipped without --strict, an error with it",
          subprocess.run([sys.executable, str(R / "tests/host/check-vcruntime.py")], capture_output=True,
                         env=dict(os.environ, VCRUNTIME_DIR=str(T / "no-such-folder"))).returncode == 0 and r.returncode == 1)
    check("  one file missing fails", run_check({n: b for n, b in good.items() if n != "mfc140u.dll"}, False) == 1)
    check("  a file that is not a PE fails", run_check(dict(good, **{"vcomp140.dll": fake_dll(pe=False)}), False) == 1)
    check("  a truncated certificate table fails", run_check(dict(good, **{"vcamp140.dll": fake_dll(signed=False)}), False) == 1)
    check("  a PE32 (not PE32+) file fails", run_check(dict(good, **{"vccorlib140.dll": fake_dll(magic=0x10B)}), False) == 1)
    # BCD (2026-10-29): the public export has no tools/fetch-vcruntime.md; a checkout without it skips its check.
    if (R / "tools/fetch-vcruntime.md").is_file():
        doc = (R / "tools/fetch-vcruntime.md").read_text()
        check("tools/fetch-vcruntime.md names the script, the check and the Documents folder",
              "./tools/fetch-vcruntime.sh" in doc and "check-vcruntime.py --strict" in doc and "Documents" in doc and "cabextract" in doc)
    else:
        print("SKIP tools/fetch-vcruntime.md: absent (the public export has no tools/fetch-vcruntime.md)")
finally:
    shutil.rmtree(T, ignore_errors=True)

print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
