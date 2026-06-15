from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

# CONTROL BYTES IN CK text files (e.g. STORYTXT.CK1, PREVIEWS.CK1)
CR = 0x0D
LF = 0x0A
RED_PREFIX = 0x7E
BULLET = 0x09
BIG_BULLET_UL = 0x97
BIG_BULLET_UR = 0x98
BIG_BULLET_LL = 0x99
BIG_BULLET_LR = 0x9A
SPECIAL_END_SIGNS = {0x1A, 0x1F}


def parse_ck_txt_lines(data: bytes) -> list[dict[str, str | int]]:
    """Parse a CK text file (for example STORYTXT.CK1 or PREVIEWS.CK1) into CSV-friendly records.

    A record represents one text block.
    Blocks are split only on 2+ consecutive CRLF pairs.
    Metadata is extracted from control bytes so reinjection can preserve layout.
    """
    records: list[dict[str, str | int]] = []

    separator_pattern = re.compile(rb"(?:\r\n){2,}")
    blocks: list[tuple[bytes, int]] = []
    start = 0

    for match in separator_pattern.finditer(data):
        block = data[start:match.start()]
        trailing_breaks = len(match.group(0)) // 2
        blocks.append((block, trailing_breaks))
        start = match.end()

    # Final block has no trailing separator in the source stream.
    blocks.append((data[start:], 0))

    line_index = 1
    for chunk, trailing_breaks in blocks:
        # Split only on selected special end signs and preserve marker per segment.
        sub_chunks: list[tuple[bytes, int | None]] = []
        current = bytearray()
        for value in bytes(chunk):
            if value in SPECIAL_END_SIGNS:
                sub_chunks.append((bytes(current), value))
                current.clear()
            else:
                current.append(value)
        if current or not sub_chunks:
            sub_chunks.append((bytes(current), None))

        for sub_index, (sub_chunk, split_control) in enumerate(sub_chunks):
            line = bytes(sub_chunk)
            sub_trailing_breaks = trailing_breaks if sub_index == len(sub_chunks) - 1 else 0

            # 1) Red line marker (0x7E), usually before the text area.
            is_red = False
            if RED_PREFIX in line:
                is_red = True
                line = line.replace(bytes([RED_PREFIX]), b"")

            # 2) Bullet marker (0x09) on the source side.
            has_bullet = False
            if BULLET in line:
                has_bullet = True
                line = line.replace(bytes([BULLET]), b"")

            # 3) Big-bullet pieces that need RTL-aware placement later.
            has_big_bullet_top = (BIG_BULLET_UL in line) or (BIG_BULLET_UR in line)
            has_big_bullet_bottom = (BIG_BULLET_LL in line) or (BIG_BULLET_LR in line)

            if has_big_bullet_top:
                line = line.replace(bytes([BIG_BULLET_UL]), b"")
                line = line.replace(bytes([BIG_BULLET_UR]), b"")
            if has_big_bullet_bottom:
                line = line.replace(bytes([BIG_BULLET_LL]), b"")
                line = line.replace(bytes([BIG_BULLET_LR]), b"")

            # 4) Save trailing control byte(s) metadata.
            trailing_controls: list[int] = [split_control] if split_control is not None else []
            trailing_control_bytes: list[int] = []
            while line and line[-1] <= 0x1F and line[-1] not in (CR, LF):
                trailing_control_bytes.append(line[-1])
                line = line[:-1]
            trailing_control_bytes.reverse()
            trailing_controls.extend(trailing_control_bytes)
            trailing_control_hex = " ".join(f"0x{value:02X}" for value in trailing_controls)

            # 5) Decode remaining bytes as latin-1 so 1 byte maps to 1 char losslessly.
            text_raw = line.decode("latin-1")
            text_raw = text_raw.replace("\r\n", " ")

            # Count indentation before visible text after control bytes are removed.
            leading_spaces = 0
            while leading_spaces < len(text_raw) and text_raw[leading_spaces] == " ":
                leading_spaces += 1

            # Keep only content text in CSV so translators don't edit indentation.
            text = text_raw[leading_spaces:].rstrip(" ")

            records.append(
                {
                    "line_index": line_index,
                    "leading_spaces": leading_spaces,
                    "is_red": 1 if is_red else 0,
                    "has_bullet": 1 if has_bullet else 0,
                    "has_big_bullet_top": 1 if has_big_bullet_top else 0,
                    "has_big_bullet_bottom": 1 if has_big_bullet_bottom else 0,
                    "special_end_sign_hex": trailing_control_hex,
                    "trailing_breaks": sub_trailing_breaks,
                    "text_en": text,
                    "text_he": "",
                }
            )
            line_index += 1

    return records


def write_csv(records: list[dict[str, str | int]], output_path: Path) -> None:
    fieldnames = [
        "line_index",
        "leading_spaces",
        "is_red",
        "has_bullet",
        "has_big_bullet_top",
        "has_big_bullet_bottom",
        "special_end_sign_hex",
        "trailing_breaks",
        "text_en",
        "text_he",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract CK text file lines and metadata into CSV."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("keen1/STORYTXT.CK1"),
        help="Path to CK text file (default: keen1/STORYTXT.CK1)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ck_txt_extract.csv"),
        help="Path to output CSV (default: ck_txt_extract.csv)",
    )
    args = parser.parse_args()

    data = args.input.read_bytes()
    records = parse_ck_txt_lines(data)
    write_csv(records, args.output)

    print(f"Extracted {len(records)} lines to {args.output}")


if __name__ == "__main__":
    main()

