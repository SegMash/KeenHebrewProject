from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

from eng_heb_map import HEBREW_TO_CODE


def parse_special_chars(special_char_str: str) -> list[int]:
    """Parse space-separated hex byte values like '0x0A 0x0D' into a list of ints."""
    if not special_char_str or special_char_str.strip() == "":
        return []

    result: list[int] = []
    for token in special_char_str.split():
        token = token.strip()
        if not token:
            continue
        if token.lower().startswith("0x"):
            token = token[2:]
        try:
            result.append(int(token, 16))
        except ValueError:
            raise ValueError(f"Invalid hex byte: {token}")

    return result


def map_hebrew_text(text: str) -> str:
    """Map Hebrew text using HEBREW_TO_CODE mapping."""
    mapped = []
    for char in text:
        if char in HEBREW_TO_CODE:
            mapped.append(HEBREW_TO_CODE[char])
        else:
            mapped.append(char)
    return "".join(mapped)


def reverse_rtl_text(text: str) -> str:
    """Reverse text for LTR rendering engines while keeping %s at original indices."""
    placeholder_positions: list[int] = []
    stripped_chars: list[str] = []

    i = 0
    while i < len(text):
        if i + 1 < len(text) and text[i : i + 2] == "%s":
            placeholder_positions.append(i)
            i += 2
            continue
        stripped_chars.append(text[i])
        i += 1

    reversed_text = "".join(reversed(stripped_chars))
    reversed_text = reversed_text.replace("(", "\0").replace(")", "(").replace("\0", ")")

    restored = reversed_text
    inserted = 0
    for pos in placeholder_positions:
        insert_at = pos + inserted
        if insert_at < 0:
            insert_at = 0
        if insert_at > len(restored):
            insert_at = len(restored)
        restored = restored[:insert_at] + "%s" + restored[insert_at:]
        inserted += 2

    return restored


def reconstruct_string(text_he: str, special_char_str: str) -> bytes:
    """
    Reconstruct the full string by:
    1. Mapping Hebrew to game codes
    2. Replacing %s placeholders with actual special bytes
    """
    if not text_he:
        return b""

    # Reverse RTL text for EXE rendering (single line, no wrapping).
    rtl_text = reverse_rtl_text(text_he)

    # Map Hebrew text
    mapped_text = map_hebrew_text(rtl_text)

    # Parse special bytes
    special_bytes = parse_special_chars(special_char_str)

    # Replace %s placeholders with actual bytes
    result = bytearray()
    placeholder_index = 0
    i = 0
    while i < len(mapped_text):
        if i + 1 < len(mapped_text) and mapped_text[i : i + 2] == "%s":
            # Replace %s with corresponding special byte
            if placeholder_index < len(special_bytes):
                result.append(special_bytes[placeholder_index])
                placeholder_index += 1
            i += 2
        else:
            result.append(ord(mapped_text[i]))
            i += 1

    return bytes(result)


def parse_offset(value: str) -> int:
    token = value.strip().lower()
    if token.startswith("0x"):
        return int(token, 16)
    return int(token, 10)


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


def extract_trailing_placeholders(text: str) -> tuple[str, str]:
    """Extract trailing %s patterns. Return (core_text, trailing_part)."""
    trailing = ""
    while text.endswith("%s"):
        trailing = "%s" + trailing
        text = text[:-2]
    return text, trailing


def count_placeholders(text: str) -> int:
    """Count %s placeholders in text."""
    count = 0
    i = 0
    while i < len(text):
        if i + 1 < len(text) and text[i : i + 2] == "%s":
            count += 1
            i += 2
        else:
            i += 1
    return count


def split_special_chars(special_char_str: str, core_placeholder_count: int) -> tuple[str, str]:
    """Split special_char_str into core and trailing parts based on placeholder count."""
    if not special_char_str or special_char_str.strip() == "":
        return "", ""

    tokens = special_char_str.split()
    core_tokens = tokens[:core_placeholder_count]
    trailing_tokens = tokens[core_placeholder_count:]

    core_str = " ".join(core_tokens) if core_tokens else ""
    trailing_str = " ".join(trailing_tokens) if trailing_tokens else ""
    return core_str, trailing_str


def build_padded_string_with_trailing(
    core_bytes: bytes, trailing_bytes: bytes, eng_size: int, he_size_raw: str
) -> bytes:
    """Build final padded string.

    Layout: ``[left spaces][core][trailing][NULLs (0x00)]``.

    Left padding stays as ASCII spaces so on-screen positioning is preserved.
    The trailing special bytes (typically a 0x0A newline) sit immediately
    after the core text, and any remaining slot space is filled with 0x00
    so C-style string readers terminate cleanly past the special bytes
    rather than rendering trailing blanks.
    """
    total_length = len(core_bytes) + len(trailing_bytes)
    if total_length > eng_size:
        raise ValueError(
            f"core + trailing is longer than eng_size ({total_length} > {eng_size})"
        )

    if he_size_raw.strip() == "":
        left_spaces = eng_size - total_length
        trailing_nulls = 0
    else:
        target_left_width = int(he_size_raw)
        left_spaces = max(target_left_width - len(core_bytes), 0)
        available_padding = eng_size - len(core_bytes) - len(trailing_bytes)
        if available_padding < 0:
            raise ValueError(
                f"core + trailing is longer than eng_size ({len(core_bytes) + len(trailing_bytes)} > {eng_size})"
            )

        if left_spaces > available_padding:
            left_spaces = available_padding
        trailing_nulls = available_padding - left_spaces

    return (
        (b" " * left_spaces)
        + core_bytes
        + trailing_bytes
        + (b"\x00" * trailing_nulls)
    )


def inject_strings(csv_path: Path, input_exe: Path, output_exe: Path) -> None:
    """Inject translated Hebrew strings into EXE using fixed-size in-place replacement."""
    exe_data = bytearray(input_exe.read_bytes())

    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print("CSV is empty", file=sys.stderr)
            return

        oversized_count = 0
        injected_count = 0
        skipped_count = 0

        for row_number, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            text_he_raw = row.get("text_he", "")
            text_he = text_he_raw.strip()
            # Skip only when the cell is truly empty. A spaces-only translation
            # (e.g. "   ") is intentional (used to blank out a string in-game)
            # and must be preserved verbatim.
            if text_he_raw == "":
                skipped_count += 1
                continue
            if text_he == "":
                text_he = text_he_raw

            offset_raw = row.get("offset", "").strip()
            eng_size_raw = row.get("eng_size", "").strip()
            he_size_raw = row.get("he_size", "").strip()
            special_char = row.get("special_char", "")

            try:
                if offset_raw == "":
                    raise ValueError("missing offset")
                if eng_size_raw == "":
                    raise ValueError("missing eng_size")

                offset = parse_offset(offset_raw)
                eng_size = int(eng_size_raw)

                core_text, trailing_text = extract_trailing_placeholders(text_he)
                core_placeholder_count = count_placeholders(core_text)
                core_special_char, trailing_special_char = split_special_chars(
                    special_char, core_placeholder_count
                )

                core_reconstructed = reconstruct_string(core_text, core_special_char)
                trailing_reconstructed = reconstruct_string(trailing_text, trailing_special_char)
                he_size = len(core_reconstructed) + len(trailing_reconstructed)

                if offset < 0 or offset + eng_size > len(exe_data):
                    raise ValueError(
                        f"offset range out of bounds (offset={offset_raw}, eng_size={eng_size})"
                    )

                if he_size > eng_size:
                    oversized_count += 1
                    text_en = row.get("text_en", "")[:50]  # First 50 chars for display
                    print(
                        f"ROW {row_number}: {offset_raw} - ENG={eng_size} HE={he_size} "
                        f"(+{he_size - eng_size}) | EN: {text_en} | HE: {text_he}"
                    )
                    continue

                padded = build_padded_string_with_trailing(
                    core_reconstructed, trailing_reconstructed, eng_size, he_size_raw
                )
                if len(padded) != eng_size:
                    raise ValueError(
                        f"internal padding error: produced {len(padded)} bytes for eng_size {eng_size}"
                    )
                exe_data[offset : offset + eng_size] = padded
                injected_count += 1
            except Exception as exc:
                print(f"ROW {row_number}: ERROR - {exc}", file=sys.stderr)
                sys.exit(1)

        if oversized_count > 0:
            print(
                f"\nInjection aborted: {oversized_count} translations exceed original size.",
                file=sys.stderr,
            )
            sys.exit(1)

    output_exe.parent.mkdir(parents=True, exist_ok=True)
    output_exe.write_bytes(bytes(exe_data))
    print(
        f"Injected {injected_count} translations into {output_exe}. "
        f"Skipped {skipped_count} rows with empty text_he."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inject Hebrew translations into KEEN1.EXE from CSV. "
            "Rows with shorter translations are left-padded with spaces; any "
            "remaining slot space after the trailing special bytes is filled "
            "with 0x00 (NULL terminator)."
        )
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=Path("exe_strings_heb.csv"),
        help="Path to Hebrew-translated CSV (default: exe_strings_heb.csv).",
    )
    parser.add_argument(
        "--input-exe",
        type=Path,
        default=Path("keen1/KEEN1.EXE"),
        help="Source EXE path to patch (default: keen1/KEEN1.EXE).",
    )
    parser.add_argument(
        "--output-exe",
        type=Path,
        default=Path("keen1/KEEN1.EXE"),
        help="Destination EXE path (default: keen1/KEEN1.EXE).",
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
        print(f"CSV file does not exist: {args.input_csv}", file=sys.stderr)
        sys.exit(1)
    if not args.input_exe.exists():
        print(f"Input EXE does not exist: {args.input_exe}", file=sys.stderr)
        sys.exit(1)

    try:
        ensure_output_policy(args.output_exe, args.override, args.backup)
        inject_strings(args.input_csv, args.input_exe, args.output_exe)
    except (ValueError, FileExistsError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()


