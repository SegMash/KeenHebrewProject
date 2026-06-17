"""Sync Hebrew translations onto fresh offsets after re-extracting the exe.

Use case: you re-ran `extract_exe_strings_csv.py` against a new build of the
exe and got a new strings CSV with shifted offsets. You don't want to lose
the Hebrew translations you already typed into the previous heb CSV.

This script iterates the OLD heb CSV row-for-row (so the output has exactly
the same set of rows you already curated -- no new rows from the new exe are
added). For each old row it looks up the matching row in the new exe CSV by
`text_en` and, when matched, refreshes the `offset` / `eng_size` /
`special_char` fields while preserving `text_he` / `he_size`.

Duplicate `text_en` values are matched in occurrence order (N-th occurrence
in the old file consumes the N-th occurrence in the new file), so repeated
strings like "DEMO" or "DISK ERROR!..." stay aligned.

Old rows that have no match in the new exe are kept as-is (with their old
offset) and reported on stderr so you can decide what to do.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path


FIELDNAMES = ["offset", "eng_size", "he_size", "special_char", "text_en", "text_he"]


def read_csv(path: Path) -> list[dict[str, str]]:
    # `skipinitialspace=True` lets the parser tolerate a stray space between
    # a comma delimiter and the opening quote of the next field (e.g.
    # `..."english text", "hebrew, text"`). Without it, Python's csv module
    # treats the field as unquoted, splits on the first inner comma, and the
    # rest of the value silently leaks into a phantom extra column.
    with path.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file, skipinitialspace=True)
        missing = set(FIELDNAMES).difference(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{path} is missing required columns: {', '.join(sorted(missing))}"
            )
        return list(reader)


def _print_list(header: str, items: list[str], limit: int = 25) -> None:
    print(header, file=sys.stderr)
    for line in items[:limit]:
        print(f"    {line}", file=sys.stderr)
    if len(items) > limit:
        print(f"    ... and {len(items) - limit} more", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Carry Hebrew translations from an existing heb CSV onto the fresh "
            "offsets in a newly-extracted exe strings CSV."
        )
    )
    parser.add_argument(
        "--new-exe-csv",
        type=Path,
        required=True,
        help=(
            "CSV produced by extract_exe_strings_csv.py against the NEW exe "
            "(source of truth for offsets, eng_size, special_char)."
        ),
    )
    parser.add_argument(
        "--old-heb-csv",
        type=Path,
        required=True,
        help="Existing heb CSV containing translations to preserve.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output CSV path.",
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    args = parser.parse_args()

    for path, label in [(args.new_exe_csv, "--new-exe-csv"), (args.old_heb_csv, "--old-heb-csv")]:
        if not path.exists():
            print(f"{label} file does not exist: {path}", file=sys.stderr)
            sys.exit(1)

    if args.output.exists() and not args.override:
        print(
            f"Output file already exists: {args.output}. Use --override to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        new_rows = read_csv(args.new_exe_csv)
        old_rows = read_csv(args.old_heb_csv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    # Build a FIFO queue of NEW exe rows per text_en, so when the old heb file
    # contains the same English string multiple times (e.g. "DEMO" twice),
    # the N-th old occurrence consumes the N-th new occurrence.
    new_queue_by_text: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in new_rows:
        new_queue_by_text[row["text_en"]].append(row)

    merged: list[dict[str, str]] = []
    unmatched_old: list[str] = []
    refreshed_count = 0
    eng_size_mismatches: list[str] = []
    special_char_mismatches: list[str] = []

    # Iterate the OLD heb rows so the output preserves the same set of rows
    # (no new entries from the new exe are added).
    for old_row in old_rows:
        text_en = old_row["text_en"]
        queue = new_queue_by_text.get(text_en)

        if queue:
            new_row = queue.pop(0)
            merged_row = {
                "offset": new_row["offset"],
                "eng_size": new_row["eng_size"],
                "he_size": old_row.get("he_size", "") or "",
                "special_char": new_row["special_char"],
                "text_en": text_en,
                "text_he": old_row.get("text_he", "") or "",
            }
            refreshed_count += 1

            old_eng_size = (old_row.get("eng_size") or "").strip()
            new_eng_size = (new_row.get("eng_size") or "").strip()
            if old_eng_size and new_eng_size and old_eng_size != new_eng_size:
                eng_size_mismatches.append(
                    f"[new offset {new_row['offset']}] old eng_size={old_eng_size} "
                    f"new eng_size={new_eng_size}"
                )

            old_special = (old_row.get("special_char") or "").strip()
            new_special = (new_row.get("special_char") or "").strip()
            if old_special != new_special:
                special_char_mismatches.append(
                    f"[new offset {new_row['offset']}] old special_char={old_special!r} "
                    f"new special_char={new_special!r}"
                )
        else:
            # No match in the new exe: keep the old row as-is so the user can
            # investigate. The old offset is left untouched.
            merged_row = {
                "offset": old_row.get("offset", "") or "",
                "eng_size": old_row.get("eng_size", "") or "",
                "he_size": old_row.get("he_size", "") or "",
                "special_char": old_row.get("special_char", "") or "",
                "text_en": text_en,
                "text_he": old_row.get("text_he", "") or "",
            }
            preview = text_en[:80] + ("..." if len(text_en) > 80 else "")
            unmatched_old.append(
                f"[old offset {old_row.get('offset', '?')}] text_en={preview!r}"
            )

        merged.append(merged_row)

    # Informational: list strings in the new exe that have no entry in the
    # heb file. These are NOT added to the output (per user requirement).
    unclaimed_new: list[str] = []
    for text_en, leftover in new_queue_by_text.items():
        for row in leftover:
            preview = text_en[:80] + ("..." if len(text_en) > 80 else "")
            unclaimed_new.append(
                f"[new offset {row.get('offset', '?')}] text_en={preview!r}"
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(merged)

    print(
        f"Wrote {len(merged)} rows to {args.output} "
        f"({refreshed_count} offsets refreshed from the new exe, "
        f"{len(unmatched_old)} kept with old offset)."
    )

    if unmatched_old:
        _print_list(
            f"  {len(unmatched_old)} heb row(s) had no match in the new exe and were kept "
            "with their OLD offset -- investigate whether the English string changed:",
            unmatched_old,
        )

    if unclaimed_new:
        _print_list(
            f"  {len(unclaimed_new)} string(s) in the new exe are not present in the heb file "
            "and were NOT added:",
            unclaimed_new,
        )

    if eng_size_mismatches:
        _print_list(
            f"  {len(eng_size_mismatches)} matched row(s) have different eng_size in old vs new "
            "(new value was used; double-check the English text):",
            eng_size_mismatches,
        )

    if special_char_mismatches:
        _print_list(
            f"  {len(special_char_mismatches)} matched row(s) have different special_char "
            "in old vs new (new value was used):",
            special_char_mismatches,
        )


if __name__ == "__main__":
    main()
