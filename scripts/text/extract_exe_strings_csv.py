from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


SPECIAL_BYTES = {
    0x0A,
    0x0D,
    0x0F,
    0x13,
    0x1B,
    0x8D,
    0x8F,
    0x90,
    0x97,
    0x98,
    0x99,
    0x9A,
}


def parse_offset(value: str, arg_name: str) -> int:
    try:
        parsed = int(value, 0)
    except ValueError as exc:
        raise ValueError(f"Invalid {arg_name} value: {value}") from exc

    if parsed < 0:
        raise ValueError(f"{arg_name} must be >= 0")
    return parsed


def extract_null_delimited_strings(data: bytes, offset_from: int, offset_to: int) -> list[dict[str, str | int]]:
    records: list[dict[str, str | int]] = []

    index = offset_from
    while index <= offset_to:
        while index <= offset_to and data[index] == 0x00:
            index += 1

        if index > offset_to:
            break

        start = index
        while index <= offset_to and data[index] != 0x00:
            index += 1

        raw = data[start:index]
        special_hits: list[int] = []
        clean_parts = bytearray()
        for value in raw:
            if value in SPECIAL_BYTES:
                special_hits.append(value)
                clean_parts.extend(b"%s")
                continue
            clean_parts.append(value)

        text_en = bytes(clean_parts).decode("latin-1")
        # Skip placeholder-only or whitespace-only rows.
        placeholder_stripped = text_en.replace("%s", "").replace(" ", "")
        if placeholder_stripped == "":
            continue

        special_char = " ".join(f"0x{value:02X}" for value in special_hits)
        records.append(
            {
                "offset": f"0x{start:X}",
                "eng_size": len(raw),
                "he_size": "",
                "special_char": special_char,
                "text_en": text_en,
                "text_he": "",
            }
        )

    return records


def write_csv(records: list[dict[str, str | int]], output_path: Path, append: bool = False) -> None:
    fieldnames = ["offset", "eng_size", "he_size", "special_char", "text_en", "text_he"]
    file_exists = output_path.exists()
    mode = "a" if append else "w"
    with output_path.open(mode, newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        if not append or not file_exists or output_path.stat().st_size == 0:
            writer.writeheader()
        writer.writerows(records)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Scan EXE bytes between --offset-from and --offset-to (inclusive) and "
            "extract strings delimited by one or more 0x00 bytes."
        )
    )
    parser.add_argument(
        "--exe",
        type=Path,
        required=True,
        help="Path to the executable file to scan.",
    )
    parser.add_argument(
        "--offset-from",
        required=True,
        help="Start offset (decimal or hex, e.g. 12345 or 0x3039).",
    )
    parser.add_argument(
        "--offset-to",
        required=True,
        help="End offset, inclusive (decimal or hex).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("keen1_exe_strings_extract.csv"),
        help="Output CSV path (default: keen1_exe_strings_extract.csv).",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append rows to existing CSV instead of overwriting.",
    )
    args = parser.parse_args()

    try:
        offset_from = parse_offset(args.offset_from, "--offset-from")
        offset_to = parse_offset(args.offset_to, "--offset-to")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if offset_to < offset_from:
        print("--offset-to must be >= --offset-from", file=sys.stderr)
        sys.exit(1)

    if not args.exe.exists():
        print(f"EXE file does not exist: {args.exe}", file=sys.stderr)
        sys.exit(1)

    data = args.exe.read_bytes()
    max_offset = len(data) - 1
    if offset_from > max_offset or offset_to > max_offset:
        print(
            f"Offsets out of bounds for file size {len(data)} bytes (max valid offset: {max_offset})",
            file=sys.stderr,
        )
        sys.exit(1)

    records = extract_null_delimited_strings(data, offset_from, offset_to)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_csv(records, args.output, append=args.append)

    print(f"Extracted {len(records)} strings to {args.output}")


if __name__ == "__main__":
    main()