"""Sync Hebrew translations onto fresh offsets after re-extracting the exe.

Use case: you re-ran `extract_exe_strings_csv.py` against a new build of the
exe and got a new strings CSV with shifted offsets. You don't want to lose
the Hebrew translations you already typed into the previous heb CSV.

This script is driven by the NEW exe CSV: every row from the new exe ends up
in the output with its `offset`, `eng_size`, `special_char`, and `text_en`
taken as-is from the new exe CSV. For each row, the `text_he` (and
`he_size`) is pulled from the old heb CSV when the `text_en` matches.

Duplicate `text_en` values are matched in occurrence order (N-th occurrence
in the new file consumes the N-th occurrence in the old file), so repeated
strings like "DEMO" or "DISK ERROR!..." stay aligned.

Rows from the new exe whose `text_en` is NOT present in the old heb CSV
are SKIPPED entirely (they are not written to the output). Rows whose
English exists in the old heb CSV but with an empty `text_he` get an
auto-generated placeholder: the English text uppercased (with `%s`
placeholders preserved). Old translations whose `text_en` no longer appears
in the new exe are dropped from the output and reported on stderr.
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

    # Build a FIFO queue of OLD heb rows per text_en, so that when the new
    # exe CSV contains the same English string multiple times (e.g. "DEMO"
    # twice), the N-th new occurrence consumes the N-th old translation.
    old_queue_by_text: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in old_rows:
        old_queue_by_text[row["text_en"]].append(row)

    merged: list[dict[str, str]] = []
    skipped_missing_en: list[str] = []
    auto_uppercased: list[str] = []
    translated_count = 0
    eng_size_mismatches: list[str] = []
    special_char_mismatches: list[str] = []

    # Drive the output by the NEW exe CSV: rows whose text_en exists in the
    # old heb CSV are written through (with hebrew pulled from old or
    # auto-uppercased when blank). Rows whose text_en is NOT present in the
    # old heb CSV at all are skipped entirely.
    for new_row in new_rows:
        text_en = new_row["text_en"]
        queue = old_queue_by_text.get(text_en)

        if not queue:
            preview = text_en[:80] + ("..." if len(text_en) > 80 else "")
            skipped_missing_en.append(
                f"[new offset {new_row['offset']}] text_en={preview!r}"
            )
            continue

        old_row = queue.pop(0)
        merged_row = {
            "offset": new_row["offset"],
            "eng_size": new_row["eng_size"],
            "he_size": old_row.get("he_size", "") or "",
            "special_char": new_row["special_char"],
            "text_en": text_en,
            "text_he": "",
        }

        old_text_he = old_row.get("text_he", "") or ""
        if old_text_he:
            merged_row["text_he"] = old_text_he
            translated_count += 1
        else:
            # The English exists in the old heb CSV but with no hebrew
            # translation. Per user request, drop in the English text
            # uppercased as a placeholder so it's visually distinct.
            # `%s` markers must stay lowercase because inject_exe_strings
            # matches them literally as control-byte placeholders.
            merged_row["text_he"] = "%s".join(
                chunk.upper() for chunk in text_en.split("%s")
            )
            auto_uppercased.append(new_row["offset"])

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

        merged.append(merged_row)

    # Translations from the old heb file whose text_en is not present in the
    # new exe CSV. They are dropped from the output but listed so the user
    # can decide whether the English text actually changed.
    orphans: list[str] = []
    for text_en, leftover in old_queue_by_text.items():
        for row in leftover:
            preview = text_en[:80] + ("..." if len(text_en) > 80 else "")
            orphans.append(
                f"[old offset {row.get('offset', '?')}] text_en={preview!r} "
                f"text_he={(row.get('text_he') or '')!r}"
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(merged)

    print(
        f"Wrote {len(merged)} rows to {args.output} "
        f"({translated_count} hebrew translations carried over, "
        f"{len(auto_uppercased)} auto-filled with UPPERCASE english, "
        f"{len(skipped_missing_en)} skipped because text_en was not in the heb CSV)."
    )

    if skipped_missing_en:
        _print_list(
            f"  {len(skipped_missing_en)} row(s) from the new exe CSV were SKIPPED because "
            "text_en is not present in the heb CSV:",
            skipped_missing_en,
        )

    if auto_uppercased:
        _print_list(
            f"  {len(auto_uppercased)} row(s) existed in the old heb CSV without a translation; "
            "text_he was auto-filled with the UPPERCASE english:",
            auto_uppercased,
        )

    if orphans:
        _print_list(
            f"  {len(orphans)} old translation(s) had no matching English string in the new "
            "exe and were dropped:",
            orphans,
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
