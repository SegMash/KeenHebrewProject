from __future__ import annotations

import argparse
import csv
import shutil
import sys
import unicodedata
from pathlib import Path

from eng_heb_map import HEBREW_TO_CODE

# CONTROL BYTES IN CK text files (e.g. STORYTXT.CK1, PREVIEWS.CK1)
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

PRESERVED_PUNCTUATION = {"-",".","?","(",")"}

UNICODE_TO_LATIN1_REPLACEMENTS = {
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2012": "-",  # figure dash
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2015": "-",  # horizontal bar
    "\u2018": "'",  # left single quote
    "\u2019": "'",  # right single quote
    "\u201C": '"',  # left double quote
    "\u201D": '"',  # right double quote
    "\u2026": "...",  # ellipsis
    "\u00A0": " ",  # non-breaking space
}


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


def normalize_text_for_layout(text: str) -> str:
    """Apply Unicode replacements and drop punctuation BEFORE wrapping/padding.

    This must happen before `rtl_transform` so that line wrapping and column
    padding are computed against the exact character set that ends up in the
    output file. Otherwise lines that originally contained punctuation come
    out shorter than the padded width once `encode_ck_text` strips it.
    """
    replaced = "".join(UNICODE_TO_LATIN1_REPLACEMENTS.get(ch, ch) for ch in text)

    # Per project preference, punctuation can be dropped from AI-generated text,
    # except for characters we explicitly want to keep (e.g. hyphen).
    return "".join(
        ch for ch in replaced
        if ch in PRESERVED_PUNCTUATION
        or not unicodedata.category(ch).startswith("P")
    )


def encode_ck_text(text: str) -> bytes:
    """Map Hebrew code points to their CK byte codes and emit latin-1 bytes.

    Expects `text` to already be normalized via `normalize_text_for_layout`.
    """
    mapped_chars = [HEBREW_TO_CODE.get(char, char) for char in text]
    return "".join(mapped_chars).encode("latin-1", errors="ignore")


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


def rebalance_last_line(wrapped_lines: list[str], max_line_length: int = MAX_LINE_LENGTH) -> list[str]:
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

        if len(candidate) <= max_line_length and len(remaining_line) <= LAST_LINE_MAX_LENGTH:
            rebuilt_previous = candidate
            split_index += 1
            break

        if len(candidate) <= max_line_length:
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


def rtl_transform(text: str, leading_spaces: int = 0, max_line_length: int = MAX_LINE_LENGTH) -> str:
    wrapped_lines = rebalance_last_line(
        wrap_text_no_word_break(text, max_len=max_line_length),
        max_line_length=max_line_length,
    )
    reversed_lines: list[str] = []
    for index, line in enumerate(wrapped_lines):
        reversed_line = line[::-1]
        reversed_line = reversed_line.replace("(", "\0").replace(")", "(").replace("\0", ")")
        if index == 0 and leading_spaces > 0:
            reversed_line += " " * leading_spaces

        # The last wrapped line gets the shorter width. This also covers the
        # single-line case (index 0 is also the last line), so it pads to
        # LAST_LINE_MAX_LENGTH regardless of the per-row max_line_length.
        is_last_line = index == len(wrapped_lines) - 1
        padded_width = LAST_LINE_MAX_LENGTH if is_last_line else max_line_length
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

    # Optional per-row override for the wrap/pad width. Falls back to the
    # global MAX_LINE_LENGTH when the column is missing or blank. The last
    # line cap (LAST_LINE_MAX_LENGTH) is intentionally NOT overridden.
    max_line_length_raw = (row.get("max_line_length") or "").strip()
    if max_line_length_raw:
        try:
            max_line_length = int(max_line_length_raw)
        except ValueError as exc:
            raise ValueError(
                f"Invalid max_line_length '{max_line_length_raw}' in CSV row {line_index}."
            ) from exc
        if max_line_length <= 0:
            raise ValueError(
                f"max_line_length must be positive in CSV row {line_index}."
            )
    else:
        max_line_length = MAX_LINE_LENGTH

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

    # Normalize first (drops punctuation, applies Unicode replacements) so the
    # column padding inside `rtl_transform` matches the final byte count.
    #normalized_text = normalize_text_for_layout(source_text).rstrip(" ")
    normalized_text = source_text.rstrip(" ")
    transformed_text = rtl_transform(normalized_text, leading_spaces, max_line_length=max_line_length)
    line.extend(encode_ck_text(transformed_text))

    control_hex = row.get("special_end_sign_hex", "")
    controls = parse_control_hex(control_hex, int(line_index))
    line.extend(controls)

    return bytes(line), trailing_breaks


def build_ck_txt_bytes(csv_rows: list[dict[str, str]]) -> bytes:
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
        description="Build CK text file from translated CSV metadata and text."
    )
    parser.add_argument(
        "--input",
        dest="input_csv",
        type=Path,
        help="Alias for --input-csv.",
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=Path("ck_txt_extract_heb.csv"),
        help="Path to translated CSV (default: ck_txt_extract_heb.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("keen1/STORYTXT.CK1"),
        help="Output CK text file path (default: keen1/STORYTXT.CK1)",
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
        data = build_ck_txt_bytes(rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(data)
    except (ValueError, FileExistsError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    print(f"Wrote {len(rows)} records to {args.output} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
