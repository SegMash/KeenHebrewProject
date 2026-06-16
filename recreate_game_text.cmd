REM Translate text resources
python scripts/text/build_ck_txt_from_csv.py --input-csv storytxt_extract_heb.csv --output keen1/STORYTXT.CK1 --override
python scripts/text/build_ck_txt_from_csv.py --input-csv previews_extract_heb.csv --output keen1/PREVIEWS.CK1 --override
REM Copy Hebrew Letters from SCI font
python.exe .\scripts\font\copy_sci_font_to_image.py --sci-font .\font.000 --target-image .\1FON0000.BMP --from-index 224 --to-index 249 --target-start-index 97 --bg-color 15 --fg-color 0 --output .\GRAPHICS\1FON0000.BMP --override
python.exe .\scripts\font\copy_sci_font_to_image.py --sci-font .\font.000 --target-image .\GRAPHICS\1FON0000.BMP --from-index 250 --to-index 250 --target-start-index 124 --bg-color 15 --fg-color 0 --output .\GRAPHICS\1FON0000.BMP --override
python.exe .\scripts\font\copy_sci_font_to_image.py --sci-font .\font.000 --target-image .\GRAPHICS\1FON0000.BMP --from-index 224 --to-index 249 --target-start-index 225 --bg-color 7 --fg-color 4 --output .\GRAPHICS\1FON0000.BMP --override
python.exe .\scripts\font\copy_sci_font_to_image.py --sci-font .\font.000 --target-image .\GRAPHICS\1FON0000.BMP --from-index 250 --to-index 250 --target-start-index 252 --bg-color 7 --fg-color 4 --output .\GRAPHICS\1FON0000.BMP --override
REM IMport New Font
KEENGRAPH.exe -episode=1 -import -bmpdir="GRAPHICS" -tra -filedir="keen1"
REM Inject strings to KEEN exe
python scripts/text/inject_exe_strings_from_csv.py --input-csv exe_strings_heb.csv --input-exe keen1/KEEN1.EXE --override