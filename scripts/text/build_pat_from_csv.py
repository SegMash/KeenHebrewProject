"""Build a CKPatch .pat file from the Hebrew-translated exe strings CSV.

Reuses the same byte-reconstruction logic as `inject_exe_strings_from_csv.py`
(RTL reversal, Hebrew->code mapping via HEBREW_TO_CODE, `%s` placeholder
substitution, padding to original english width) so the produced patch
yields the exact same bytes the inject script would write -- only via
CKPatch's in-memory patching instead of editing KEEN1.EXE on disk.

The CSV stores raw FILE offsets (i.e. offsets in the decompressed exe as a
hex editor would show them). CKPatch addresses, however, are LOAD-IMAGE
offsets (relative to the start of the loaded program, AFTER the MZ exe
header is stripped). For Keen 1 v1.31 the MZ header is 0x200 bytes long,
so this script subtracts 0x200 from every CSV offset by default. Override
with --mz-header-size if you target a different exe.

The bytes for each row are emitted as a mixed sequence of printable
strings (`"..."`) and hex bytes (`$XX`) so non-printable bytes (control
codes, double-quote, etc.) can never break the CKPatch parser.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from inject_exe_strings_from_csv import (
    build_padded_string_with_trailing,
    count_placeholders,
    extract_trailing_placeholders,
    parse_offset,
    reconstruct_string,
    split_special_chars,
)


DEFAULT_MZ_HEADER_SIZE = 0x200


def bytes_to_ckpatch_tokens(data: bytes) -> str:
    """Convert a raw byte sequence into CKPatch token syntax.

    Printable ASCII (0x20..0x7E), except for the double-quote, is grouped
    into "..." string literals. Anything else is emitted as a $XX hex byte.
    Adjacent tokens are joined with a single space, e.g.:
        b'New \x0aTest' -> '"New " $0A "Test"'
    """
    tokens: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            tokens.append('"' + "".join(current) + '"')
            current.clear()

    for byte in data:
        # Printable ASCII excluding `"` (which would close the string literal)
        # and `$` (CKPatch's token-prefix character, just to be safe inside
        # quoted strings even though it's normally allowed).
        if 0x20 <= byte <= 0x7E and byte not in (0x22, 0x24):
            current.append(chr(byte))
        else:
            flush()
            tokens.append(f"${byte:02X}")

    flush()
    return " ".join(tokens) if tokens else '""'


def reconstruct_row_bytes(row: dict[str, str], row_number: int) -> tuple[int, bytes] | None:
    """Return (file_offset, padded_bytes) for a row, or None if the row should be skipped.

    Returns None for empty text_he. Raises ValueError on data errors.
    """
    text_he = (row.get("text_he") or "").strip()
    if not text_he:
        return None

    offset_raw = (row.get("offset") or "").strip()
    eng_size_raw = (row.get("eng_size") or "").strip()
    he_size_raw = (row.get("he_size") or "").strip()
    special_char = row.get("special_char") or ""

    if offset_raw == "":
        raise ValueError(f"row {row_number}: missing offset")
    if eng_size_raw == "":
        raise ValueError(f"row {row_number}: missing eng_size")

    offset = parse_offset(offset_raw)
    eng_size = int(eng_size_raw)

    # Mirror inject_exe_strings_from_csv: split off trailing %s placeholders
    # so they get appended after the right-side padding (matching the exe layout).
    core_text, trailing_text = extract_trailing_placeholders(text_he)
    core_placeholder_count = count_placeholders(core_text)
    core_special, trailing_special = split_special_chars(special_char, core_placeholder_count)

    core_bytes = reconstruct_string(core_text, core_special)
    trailing_bytes = reconstruct_string(trailing_text, trailing_special)
    he_size = len(core_bytes) + len(trailing_bytes)

    if he_size > eng_size:
        raise ValueError(
            f"row {row_number} ({offset_raw}): hebrew is {he_size} bytes but "
            f"original is only {eng_size} bytes (+{he_size - eng_size} over)"
        )

    padded = build_padded_string_with_trailing(
        core_bytes, trailing_bytes, eng_size, he_size_raw
    )
    if len(padded) != eng_size:
        raise ValueError(
            f"row {row_number} ({offset_raw}): internal padding error "
            f"({len(padded)} != {eng_size})"
        )

    return offset, bytes(padded)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a CKPatch .pat file from the Hebrew translated exe-strings CSV. "
            "CSV offsets (file offsets) are converted to CKPatch load-image offsets "
            "by subtracting the MZ header size."
        )
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=Path("exe_strings_heb.csv"),
        help="Path to Hebrew-translated CSV (default: exe_strings_heb.csv).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("keen1/hebpatch/patch.pat"),
        help="Output .pat file path (default: keen1/hebpatch/patch.pat).",
    )
    parser.add_argument(
        "--mz-header-size",
        type=lambda v: int(v, 0),
        default=DEFAULT_MZ_HEADER_SIZE,
        help=(
            "Size of the MZ exe header in bytes; subtracted from every CSV "
            "offset to obtain the CKPatch load-image offset. Default: 0x200 "
            "(Keen 1 v1.31)."
        ),
    )
    parser.add_argument(
        "--ext",
        default="ck1",
        help="Episode extension for the %%ext directive (default: ck1).",
    )
    parser.add_argument(
        "--version",
        default="1.31",
        help="Game version for the %%version directive (default: 1.31).",
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="Overwrite the output .pat file if it already exists.",
    )
    args = parser.parse_args()

    if not args.input_csv.exists():
        print(f"CSV file does not exist: {args.input_csv}", file=sys.stderr)
        sys.exit(1)

    if args.output.exists() and not args.override:
        print(
            f"Output already exists: {args.output}. Use --override to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)

    patch_lines: list[str] = [
        f"%ext {args.ext}",
        f"%version {args.version}",
        "# Auto-generated by build_pat_from_csv.py. Do not edit by hand.",
        f"# Source CSV   : {args.input_csv}",
        f"# MZ header    : 0x{args.mz_header_size:X} bytes (subtracted from CSV offsets)",
        "",
    ]

    written = 0
    skipped_blank = 0
    oversized: list[str] = []

    with args.input_csv.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file, skipinitialspace=True)
        for row_number, row in enumerate(reader, start=2):
            try:
                result = reconstruct_row_bytes(row, row_number)
            except ValueError as exc:
                # Oversized hebrew is logged but doesn't kill the rest of the
                # generation -- the user can shorten that one entry.
                msg = str(exc)
                if "bytes but original is only" in msg:
                    oversized.append(msg)
                    continue
                print(msg, file=sys.stderr)
                sys.exit(1)

            if result is None:
                skipped_blank += 1
                continue

            file_offset, padded = result
            load_offset = file_offset - args.mz_header_size
            if load_offset < 0:
                print(
                    f"row {row_number}: file offset 0x{file_offset:X} is inside the "
                    f"MZ header (< 0x{args.mz_header_size:X}); skipping.",
                    file=sys.stderr,
                )
                continue

            tokens = bytes_to_ckpatch_tokens(padded)
            patch_lines.append(f"%patch ${load_offset:X} {tokens}")
            written += 1

    patch_lines.append("")
    patch_lines.append("%end")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    # The body is plain ASCII at this point (we used ascii(...) for all
    # potentially non-ASCII content), so latin-1 is safe and matches what
    # the CKPatch parser expects.
    args.output.write_text("\n".join(patch_lines), encoding="ascii")

    print(
        f"Wrote {written} patch directives to {args.output} "
        f"(skipped {skipped_blank} rows with empty text_he, "
        f"{len(oversized)} oversized)."
    )

    if oversized:
        print("\nOversized translations (NOT included in the patch):", file=sys.stderr)
        for msg in oversized[:25]:
            print(f"  {msg}", file=sys.stderr)
        if len(oversized) > 25:
            print(f"  ... and {len(oversized) - 25} more", file=sys.stderr)


if __name__ == "__main__":
    main()
