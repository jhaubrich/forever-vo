# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Pulls Warcraft III's English unit voice lines out of a local Reforged install.

build_wc3_references.py wants a folder of extracted `units/<race>/<unit>/*`
audio. Reforged keeps it in a CASC store, which needs CascLib to read. CascLib
is not in nixpkgs, so build it once and keep the shared library where this
script looks for it (tools/data/libcasc.so, gitignored):

    git clone --depth 1 https://github.com/ladislav-zezula/CascLib
    nix shell nixpkgs#cmake nixpkgs#gnumake nixpkgs#gcc nixpkgs#zlib -c sh -c '
        cmake -S CascLib -B CascLib/build -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
              -DCMAKE_BUILD_TYPE=Release -DCASC_BUILD_SHARED_LIB=ON -DCASC_BUILD_STATIC_LIB=OFF
        cmake --build CascLib/build -j8'
    cp CascLib/build/libcasc.so.1.0.0 tools/data/libcasc.so

    ./tools/run.sh tools/extract_wc3_units.py                 # everything English
    ./tools/run.sh tools/extract_wc3_units.py --only dryad --only ogre

The store carries every locale three times: the classic base
(`war3.w3mod:_locales\\enus.w3mod:units\\...`), the HD layer as ogg
(`_hd.w3mod`) and a lossless flac layer (`_de.w3mod`). The flac layer is taken
by default; --layer picks another. Output lands in tools/voices/raw-wc3/units/
as <race>/<unit>/<file>, the folder shape build_wc3_references.py matches on.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR, VOICES_DIR

CASCLIB = Path(os.environ.get("FOREVER_VO_CASCLIB") or DATA_DIR / "libcasc.so")
STORES = [
    Path.home() / "Faugus/battlenet/drive_c/Program Files (x86)/Warcraft III",
    Path.home() / ".wine/drive_c/Program Files (x86)/Warcraft III",
    Path("C:/Program Files (x86)/Warcraft III"),
]
OUT_DIR = VOICES_DIR / "raw-wc3" / "units"
LAYERS = {"de": "war3.w3mod:_de.w3mod:_locales\\enus.w3mod:units\\",
          "hd": "war3.w3mod:_hd.w3mod:_locales\\enus.w3mod:units\\",
          "classic": "war3.w3mod:_locales\\enus.w3mod:units\\"}
AUDIO = (".flac", ".ogg", ".wav", ".mp3")
MAX_PATH = 1024
CASC_LOCALE_ALL = 0xFFFFFFFF


class FindData(ctypes.Structure):
    _fields_ = [("szFileName", ctypes.c_char * MAX_PATH), ("CKey", ctypes.c_ubyte * 16),
                ("EKey", ctypes.c_ubyte * 16), ("TagBitMask", ctypes.c_uint64),
                ("FileSize", ctypes.c_uint64), ("szPlainName", ctypes.c_char_p),
                ("dwFileDataId", ctypes.c_uint32), ("dwLocaleFlags", ctypes.c_uint32),
                ("dwContentFlags", ctypes.c_uint32), ("dwSpanCount", ctypes.c_uint32),
                ("bits", ctypes.c_uint32), ("NameType", ctypes.c_int)]


def load_casclib(path: Path) -> ctypes.CDLL:
    if not path.exists():
        raise SystemExit(f"no CascLib at {path}; build it as described at the top of this script")
    lib = ctypes.CDLL(str(path))
    lib.CascOpenStorage.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    lib.CascOpenStorage.restype = ctypes.c_bool
    lib.CascCloseStorage.argtypes = [ctypes.c_void_p]
    lib.CascFindFirstFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_char_p]
    lib.CascFindFirstFile.restype = ctypes.c_void_p
    lib.CascFindNextFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.CascFindNextFile.restype = ctypes.c_bool
    lib.CascFindClose.argtypes = [ctypes.c_void_p]
    lib.CascOpenFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32,
                                 ctypes.POINTER(ctypes.c_void_p)]
    lib.CascOpenFile.restype = ctypes.c_bool
    lib.CascReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    lib.CascReadFile.restype = ctypes.c_bool
    lib.CascCloseFile.argtypes = [ctypes.c_void_p]
    lib.GetCascError.restype = ctypes.c_uint32
    return lib


def find_store(explicit: Path | None) -> Path:
    for candidate in ([explicit] if explicit else STORES):
        if candidate and (candidate / ".build.info").exists():
            return candidate
    raise SystemExit("no Warcraft III install found; pass --store <folder containing .build.info>")


def list_units(lib: ctypes.CDLL, storage: ctypes.c_void_p, prefix: str) -> list[tuple[str, int]]:
    """(CASC name, size) of every audio file under the chosen layer's units folder."""
    fd = FindData()
    handle = lib.CascFindFirstFile(storage, b"*", ctypes.byref(fd), None)
    found: list[tuple[str, int]] = []
    ok = bool(handle)
    while ok:
        name = fd.szFileName.decode(errors="replace")
        if name.lower().startswith(prefix.lower()) and name.lower().endswith(AUDIO):
            found.append((name, int(fd.FileSize)))
        ok = lib.CascFindNextFile(handle, ctypes.byref(fd))
    lib.CascFindClose(handle)
    return found


def read_file(lib: ctypes.CDLL, storage: ctypes.c_void_p, name: str, size: int) -> bytes | None:
    handle = ctypes.c_void_p()
    if not lib.CascOpenFile(storage, name.encode(), CASC_LOCALE_ALL, 0, ctypes.byref(handle)):
        return None
    try:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_uint32(0)
        total = 0
        while total < size:
            if not lib.CascReadFile(handle, ctypes.byref(buf, total), size - total, ctypes.byref(got)) or got.value == 0:
                break
            total += got.value
        return buf.raw[:total]
    finally:
        lib.CascCloseFile(handle)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, help="Warcraft III install folder (the one with .build.info)")
    ap.add_argument("--casclib", type=Path, default=CASCLIB, help=f"shared library (default {CASCLIB})")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--layer", choices=sorted(LAYERS), default="de")
    ap.add_argument("--only", action="append", help="unit folders containing this (substring match)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    lib = load_casclib(args.casclib)
    store = find_store(args.store)
    storage = ctypes.c_void_p()
    if not lib.CascOpenStorage(str(store).encode(), CASC_LOCALE_ALL, ctypes.byref(storage)):
        raise SystemExit(f"CascOpenStorage failed on {store} (error {lib.GetCascError()})")
    try:
        prefix = LAYERS[args.layer]
        print(f"listing {store} ...")
        files = list_units(lib, storage, prefix)
        print(f"{len(files)} English unit audio files in the {args.layer} layer")
        written = skipped = failed = 0
        for name, size in sorted(files):
            relative = Path(*name[len(prefix):].split("\\"))
            if args.only and not any(o.lower() in relative.parent.as_posix().lower() for o in args.only):
                continue
            dest = args.out / relative
            if dest.exists() and dest.stat().st_size == size:
                skipped += 1
                continue
            if args.dry_run:
                print(f"  {relative}  {size}")
                written += 1
                continue
            data = read_file(lib, storage, name, size)
            if data is None:
                failed += 1
                print(f"  failed: {relative} (error {lib.GetCascError()})")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            written += 1
        verb = "would write" if args.dry_run else "wrote"
        print(f"{verb} {written}, already there {skipped}, failed {failed} -> {args.out}")
        return 1 if failed else 0
    finally:
        lib.CascCloseStorage(storage)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
