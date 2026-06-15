from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

from eng_heb_map import HEBREW_TO_CODE

# CONTROL BYTES IN STORYTXT.CK1
CR = 0x0D
LF = 0x0A
RED_PREFIX = 0x7E
BULLET = 0x09
BIG_BULLET_UL = 0x97
BIG_BULLET_UR = 0x98
BIG_BULLET_LL = 0x99
BULLET_LR = 0x9A
MAX_LINE_LENGTH = 38
LAST_LINE_MAX_LENGTH = 37


def parse_control_hex(value: str, line_index: int) -> list[int]:
    value = value.strip()
    if not value:
        return []

    controls: list[int] = []
    for token in value.split():
        token = token.strip()
        if not token:
            continue
        if token.lower().startswith("0x"):
            token = token[2:]
        try:
            parsed = int(token, 16)
        except ValueError as exc:
            raise ValueError(
                f"Invalid control byte '{token}' in CSV row {line_index}."
            ) from exc

        if parsed < 0 or parsed > 0xFF:
            raise ValueError(
                f"Control byte out of range ({parsed}) in CSV row {line_index}."
            )
        controls.append(parsed)

    return controls


def encode_story_text(text: str) -> bytes:
    mapped_chars: list[str] = []
    for char in text:
        mapped_chars.append(HEBREW_TO_CODE.get(char, char))
    return "".join(mapped_chars).encode("latin-1")


def wrap_text_no_word_break(text: str, max_len: int = MAX_LINE_LENGTH) -> list[str]:
    words = text.split()
    if not words:
        return [""]

    wrapped: list[str] = []
    current = words[0]

    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_len:
            current = candidate
        else:
            wrapped.append(current)
            current = word

    wrapped.append(current)
    return wrapped


def rebalance_last_line(wrapped_lines: list[str]) -> list[str]:
    if len(wrapped_lines) < 2 or len(wrapped_lines[-1]) <= LAST_LINE_MAX_LENGTH:
        return wrapped_lines

    combined_words = (wrapped_lines[-2] + " " + wrapped_lines[-1]).split()
    if not combined_words:
        return wrapped_lines

    rebuilt_previous = combined_words[0]
    split_index = 1

    while split_index < len(combined_words):
        candidate = f"{rebuilt_previous} {combined_words[split_index]}"
        remaining = " ".join(combined_words[split_index + 1 :])
        remaining_line = combined_words[split_index] if not remaining else f"{combined_words[split_index]} {remaining}"

        if len(candidate) <= MAX_LINE_LENGTH and len(remaining_line) <= LAST_LINE_MAX_LENGTH:
            rebuilt_previous = candidate
            split_index += 1
            break

        if len(candidate) <= MAX_LINE_LENGTH:
            rebuilt_previous = candidate
            split_index += 1
            continue

        break

    rebuilt_last_words = combined_words[split_index:]
    if not rebuilt_last_words:
        return wrapped_lines

    rebuilt_last = " ".join(rebuilt_last_words)
    if len(rebuilt_last) > LAST_LINE_MAX_LENGTH:
        return wrapped_lines

    wrapped_lines[-2] = rebuilt_previous
    wrapped_lines[-1] = rebuilt_last
    return wrapped_lines


def rtl_transform(text: str, leading_spaces: int = 0) -> str:
    wrapped_lines = rebalance_last_line(wrap_text_no_word_break(text))
    reversed_lines: list[str] = []
    for index, line in enumerate(wrapped_lines):
        reversed_line = line[::-1]
        reversed_line = reversed_line.replace("(", "\0").replace(")", "(").replace("\0", ")")
        if index == 0 and leading_spaces > 0:
            reversed_line += " " * leading_spaces

        if index == 0:
            padded_width = MAX_LINE_LENGTH
        else:
            padded_width = LAST_LINE_MAX_LENGTH if index == len(wrapped_lines) - 1 else MAX_LINE_LENGTH
        padded_line = reversed_line.rjust(padded_width, " ")
        reversed_lines.append(padded_line)
    return "\r\n".join(reversed_lines)


def row_to_bytes(row: dict[str, str], row_number: int) -> tuple[bytes, int]:
    line_index = row.get("line_index", str(row_number))

    try:
        leading_spaces = int(row.get("leading_spaces", "0"))
        is_red = int(row.get("is_red", "0"))
        has_bullet = int(row.get("has_bullet", "0"))
        has_big_bullet_top = int(row.get("has_big_bullet_top", "0"))
        has_big_bullet_bottom = int(row.get("has_big_bullet_bottom", "0"))
        trailing_breaks = int(row.get("trailing_breaks", "0"))
    except ValueError as exc:
        raise ValueError(f"Invalid numeric metadata in CSV row {line_index}.") from exc

    if leading_spaces < 0 or trailing_breaks < 0:
        raise ValueError(f"Negative metadata values are not allowed in CSV row {line_index}.")

    source_text = row.get("text_he", "")
    if source_text == "":
        source_text = row.get("text_en", "")

    line = bytearray()
    if is_red:
        line.append(RED_PREFIX)
    if has_bullet:
        line.append(BULLET)
    if has_big_bullet_top:
        line.extend([BIG_BULLET_UL, BIG_BULLET_UR])
    if has_big_bullet_bottom:
        line.extend([BIG_BULLET_LL, BULLET_LR])

    transformed_text = rtl_transform(source_text.rstrip(" "), leading_spaces)
    line.extend(encode_story_text(transformed_text))

    control_hex = row.get("special_end_sign_hex", "")
    controls = parse_control_hex(control_hex, int(line_index))
    line.extend(controls)

    return bytes(line), trailing_breaks


def build_storytxt_bytes(csv_rows: list[dict[str, str]]) -> bytes:
    output = bytearray()

    for row_number, row in enumerate(csv_rows, start=1):
        line_bytes, trailing_breaks = row_to_bytes(row, row_number)
        output.extend(line_bytes)
        output.extend(bytes([CR, LF]) * trailing_breaks)

    return bytes(output)


def ensure_output_policy(output_path: Path, override: bool, backup_path: Path | None) -> None:
    if not output_path.exists():
        return

    if backup_path is not None:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, backup_path)
        return

    if override:
        return

    raise FileExistsError(
        f"Output file already exists: {output_path}. Use --override or --backup <file>."
    )


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        required_fields = {
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
        }
        missing = required_fields.difference(reader.fieldnames or [])
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"CSV is missing required columns: {missing_text}")

        return list(reader)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build STORYTXT.CK1 from translated CSV metadata and text."
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=Path("storytxt_extract_heb.csv"),
        help="Path to translated CSV (default: storytxt_extract_heb.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("keen1/STORYTXT.CK1"),
        help="Output STORYTXT file path (default: keen1/STORYTXT.CK1)",
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="Allow overwriting an existing output file.",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        help="Backup the existing output file to this path before overwriting.",
    )
    args = parser.parse_args()

    if not args.input_csv.exists():
        print(f"Input CSV does not exist: {args.input_csv}", file=sys.stderr)
        sys.exit(1)

    try:
        ensure_output_policy(args.output, args.override, args.backup)
        rows = read_csv_rows(args.input_csv)
        data = build_storytxt_bytes(rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(data)
    except (ValueError, FileExistsError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    print(f"Wrote {len(rows)} records to {args.output} ({len(data)} bytes)")


if __name__ == "__main__":
    main()