# Keen Text Tools

This folder currently has four scripts:

- `extract_ck_txt_csv.py`: extracts CK text files (for example `STORYTXT.CK1` and `PREVIEWS.CK1`) into CSV for translation.
- `build_ck_txt_from_csv.py`: rebuilds CK text files (for example `STORYTXT.CK1` and `PREVIEWS.CK1`) from translated CSV.
- `extract_exe_strings_csv.py`: scans a byte range in an EXE and extracts null-delimited strings to CSV.
- `inject_exe_strings_from_csv.py`: injects translated EXE strings from CSV back into `KEEN1.EXE`.

## 1) Extract from CK text files to CSV

Default paths:

- input: `keen1/STORYTXT.CK1`
- output: `ck_txt_extract.csv`

Run:

```bash
python scripts/text/extract_ck_txt_csv.py
```

Custom input/output:

```bash
python scripts/text/extract_ck_txt_csv.py --input keen1/STORYTXT.CK1 --output ck_txt_extract.csv
```

## 2) Build CK text files from translated CSV

Default paths:

- input CSV: `ck_txt_extract_heb.csv`
- output file: `keen1/STORYTXT.CK1`

### Safety behavior

If output file already exists:

- without flags: script exits with error (no overwrite)
- `--override`: overwrite directly
- `--backup <file>`: backup existing output to `<file>`, then overwrite

### Common examples

Build and overwrite directly:

```bash
python scripts/text/build_ck_txt_from_csv.py --override
```

Build with backup before overwrite:

```bash
python scripts/text/build_ck_txt_from_csv.py --backup backup/STORYTXT.CK1.bak
```

Build with explicit paths:

```bash
python scripts/text/build_ck_txt_from_csv.py --input-csv ck_txt_extract_heb.csv --output keen1/STORYTXT.CK1 --backup backup/STORYTXT_before_rebuild.CK1
```

## Typical workflow

1. Extract source text to CSV.
2. Fill 	ext_he in the CSV.
3. Rebuild a CK text file (for example STORYTXT.CK1 or PREVIEWS.CK1) from the translated CSV using --backup or --override.

## 3) Extract strings from EXE by offset range

This script scans bytes in an EXE between `--offset-from` and `--offset-to` (inclusive).
String delimiter is one or more `0x00` bytes.

CSV columns are:

- `offset`
- `eng_size`
- `he_size`
- `special_char`
- `text_en`
- `text_he`

`he_size` and `text_he` are intentionally left empty for later translation work.
If `0x0A`, `0x0D`, `0x0F`, `0x13`, `0x1B`, `0x8D`, `0x8F`, `0x90`, `0x97`, `0x98`, `0x99`, or `0x9A` appears inside a string, each occurrence is replaced in `text_en` with `%s`.
The `special_char` column stores the matching special-byte sequence in occurrence order (for example: `0x0F 0x13`).
Rows where `text_en` would contain only spaces and/or `%s` are omitted from the CSV.

Run with hex offsets:

```bash
python scripts/text/extract_exe_strings_csv.py --exe keen1/KEEN1.EXE_ --offset-from 0x1000 --offset-to 0x3000 --output keen1_exe_strings_extract.csv
```

Run with decimal offsets:

```bash
python scripts/text/extract_exe_strings_csv.py --exe keen1/KEEN1.EXE_ --offset-from 4096 --offset-to 12288 --output keen1_exe_strings_extract.csv
```

Append to an existing CSV instead of overwriting:

```bash
python scripts/text/extract_exe_strings_csv.py --exe keen1/KEEN1.EXE --offset-from 0x1000 --offset-to 0x1200 --output keen1_exe_strings_extract.csv --append
```

## 4) Inject translated strings into KEEN1.EXE

This script reads `exe_strings_heb.csv`, reverses each translated string for RTL display (single-line reverse, no wrapping), maps Hebrew characters through `HEBREW_TO_CODE`, restores `%s` placeholders from `special_char`, and writes translated bytes at each `offset`.

Size rules:

- if translated size is larger than `eng_size`, injection is aborted with a report
- if `he_size` is empty and translated size is smaller than `eng_size`, it is left-padded with spaces (`0x20`) to match exactly
- if `he_size` has a number, it is used to split padding between left and right:
	`left_spaces = max(he_size - message_length, 0)`
	`right_spaces = eng_size - message_length - left_spaces`
- if the string ends with one or more `%s`, right-spaces are inserted BEFORE the trailing `%s` (not after)

### Common examples

Inject in-place with backup:

```bash
python scripts/text/inject_exe_strings_from_csv.py --input-csv exe_strings_heb.csv --input-exe keen1/KEEN1.EXE --output-exe keen1/KEEN1.EXE --backup backup/KEEN1_before_hebrew.EXE
```

Inject to a new output file:

```bash
python scripts/text/inject_exe_strings_from_csv.py --input-csv exe_strings_heb.csv --input-exe keen1/KEEN1.EXE --output-exe keen1/KEEN1_HEB.EXE
```



### FONTS

## Export


```bash
KEENGRAPH.exe -episode=1 -export -bmpdir="GRAPHICS" -tra -filedir="keen1"
```

## Import

```bash
KEENGRAPH.exe -episode=1 -import -bmpdir="GRAPHICS" -tra -filedir="keen1"
```



