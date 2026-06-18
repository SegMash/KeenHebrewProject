"""Find 16-bit DOS pointers to a target string in a decompressed MZ executable.

Background
----------
In a 16-bit DOS executable, near pointers to data are usually 2-byte
DS-relative offsets: the runtime computes the linear address as
``DS:offset``, where ``DS`` is the DGROUP segment.

For a typical small/compact/medium-model C or Pascal program, ``DS == SS``
at startup, and the initial ``SS`` value lives in the MZ header at offset
``0x0E``. Multiplying that by 16 gives the load-image offset of DS:0.

So if a string sits at load-image offset ``S`` and DGROUP starts at load-
image offset ``D``, the DS-relative pointer that addresses it has value
``S - D``. Searching the load image for that 16-bit little-endian value
reveals every place that *could* be a pointer to the string.

Usage
-----
    python scripts/text/find_string_pointers.py keen1/KEEN1.EXENEW \
        --target-load 0x15EE6

You can pass ``--dgroup 0xC950`` to override the DGROUP base if you don't
trust the MZ-derived value. ``--caves`` additionally reports long runs of
zero bytes (good places to stash enlarged strings).
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def parse_mz_header(raw: bytes) -> dict[str, int]:
    if raw[:2] != b"MZ":
        raise ValueError("Not an MZ executable (missing 'MZ' signature)")

    e_cblp = struct.unpack_from("<H", raw, 0x02)[0]
    e_cp = struct.unpack_from("<H", raw, 0x04)[0]
    e_crlc = struct.unpack_from("<H", raw, 0x06)[0]
    e_cparhdr = struct.unpack_from("<H", raw, 0x08)[0]
    e_ss = struct.unpack_from("<H", raw, 0x0E)[0]
    e_sp = struct.unpack_from("<H", raw, 0x10)[0]
    e_ip = struct.unpack_from("<H", raw, 0x14)[0]
    e_cs = struct.unpack_from("<H", raw, 0x16)[0]
    e_lfarlc = struct.unpack_from("<H", raw, 0x18)[0]

    header_size = e_cparhdr * 16
    image_size = e_cp * 512 - (512 - e_cblp if e_cblp else 0) - header_size
    return {
        "header_size": header_size,
        "image_size": image_size,
        "relocations": e_crlc,
        "reloc_table_offset": e_lfarlc,
        "initial_cs": e_cs,
        "initial_ip": e_ip,
        "initial_ss": e_ss,
        "initial_sp": e_sp,
    }


def find_word_matches(load_image: bytes, value: int) -> list[int]:
    needle = struct.pack("<H", value & 0xFFFF)
    matches: list[int] = []
    pos = 0
    while True:
        idx = load_image.find(needle, pos)
        if idx == -1:
            break
        matches.append(idx)
        pos = idx + 1  # allow overlapping matches
    return matches


def context_bytes(load_image: bytes, offset: int, before: int = 8, after: int = 10) -> str:
    start = max(0, offset - before)
    end = min(len(load_image), offset + after)
    parts: list[str] = []
    for i, b in enumerate(load_image[start:end]):
        sep = ">" if start + i == offset else " "
        parts.append(f"{sep}{b:02X}")
    return "".join(parts).strip()


def find_zero_caves(load_image: bytes, min_size: int = 256) -> list[tuple[int, int]]:
    caves: list[tuple[int, int]] = []
    in_run = False
    run_start = 0
    for i, b in enumerate(load_image):
        if b == 0x00:
            if not in_run:
                in_run = True
                run_start = i
        else:
            if in_run and (i - run_start) >= min_size:
                caves.append((run_start, i - run_start))
            in_run = False
    if in_run and (len(load_image) - run_start) >= min_size:
        caves.append((run_start, len(load_image) - run_start))
    return caves


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", type=Path, help="Decompressed MZ executable (e.g. KEEN1.EXENEW)")
    ap.add_argument(
        "--target-load",
        type=lambda s: int(s, 0),
        required=True,
        help="Load-image offset of the string you want pointers to (e.g. 0x15EE6)",
    )
    ap.add_argument(
        "--dgroup",
        type=lambda s: int(s, 0),
        default=None,
        help="Override DGROUP load-image offset. Default uses initial SS * 16 from MZ header.",
    )
    ap.add_argument(
        "--caves",
        action="store_true",
        help="Also list long runs of 0x00 bytes (potential free space for enlarged strings).",
    )
    ap.add_argument(
        "--cave-min",
        type=lambda s: int(s, 0),
        default=512,
        help="Minimum size (bytes) of a zero-run to be reported as a cave. Default: 512.",
    )
    args = ap.parse_args()

    raw = args.exe.read_bytes()
    mz = parse_mz_header(raw)
    load = raw[mz["header_size"]:]

    print(f"File         : {args.exe}  ({len(raw):,} bytes)")
    print(f"MZ header    : 0x{mz['header_size']:X} bytes")
    print(f"Load image   : 0x{len(load):X} bytes")
    print(f"Initial CS:IP= {mz['initial_cs']:04X}:{mz['initial_ip']:04X}  (entry point)")
    print(f"Initial SS:SP= {mz['initial_ss']:04X}:{mz['initial_sp']:04X}  (stack)")
    print(f"Reloc table  : {mz['relocations']} entries @ file offset 0x{mz['reloc_table_offset']:X}")

    dgroup_load = args.dgroup if args.dgroup is not None else mz["initial_ss"] * 16
    src = "user-supplied" if args.dgroup is not None else "MZ initial SS * 16"
    print(f"\nDGROUP base  : 0x{dgroup_load:X} ({src})")

    ds_rel = args.target_load - dgroup_load
    print(f"Target string load offset : 0x{args.target_load:X}")
    print(f"Target DS-relative offset : 0x{ds_rel:X}  (= 0x{args.target_load:X} - 0x{dgroup_load:X})")

    if not (0 <= ds_rel <= 0xFFFF):
        print(
            "\nWARNING: DS-relative offset doesn't fit in 16 bits. Either the DGROUP\n"
            "base is wrong or this string is reached via a FAR pointer (4-byte segment:offset).\n"
        )
        return

    hits = find_word_matches(load, ds_rel)
    print(f"\nCandidate 16-bit pointer locations to 0x{args.target_load:X} (value 0x{ds_rel:04X}):")
    print(f"  total matches: {len(hits)}")
    print()
    print("  load offset   surrounding bytes (>= match)")
    print("  -----------   --------------------------------")
    for h in hits:
        print(f"  0x{h:05X}      {context_bytes(load, h)}")

    if args.caves:
        caves = find_zero_caves(load, args.cave_min)
        print(f"\nZero-byte caves (>= {args.cave_min} bytes):")
        print("  load offset   length")
        print("  -----------   ------")
        for start, length in caves:
            print(f"  0x{start:05X}      {length:,} bytes")


if __name__ == "__main__":
    main()
