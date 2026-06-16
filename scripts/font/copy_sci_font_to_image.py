#!/usr/bin/env python3
"""
Copy SCI font characters into a target EGA image (128x128, 4-bit color).
Each character cell is 8x8 pixels in a 16x16 grid.
"""

import argparse
import struct
from pathlib import Path
from PIL import Image


def parse_sci_font_char(font_file, char_index):
    """Parse a single character from SCI font file and return (width, height, bitmap_data)."""
    with open(font_file, "rb") as f:
        # Read header
        reserved = struct.unpack("<I", f.read(4))[0]
        num_chars = struct.unpack("<H", f.read(2))[0]
        line_height = struct.unpack("<H", f.read(2))[0]

        if char_index >= num_chars:
            return None

        # Read character pointer for this index
        f.seek(8 + char_index * 2)
        pointer = struct.unpack("<H", f.read(2))[0]

        # Seek to character data
        f.seek(pointer + 2)

        # Read character dimensions
        width = struct.unpack("B", f.read(1))[0]
        height = struct.unpack("B", f.read(1))[0]

        if width == 0 or height == 0:
            return None

        # Calculate bitmap size and read data
        bytes_per_row = (width + 7) // 8
        bitmap_size = height * bytes_per_row
        bitmap_data = f.read(bitmap_size)

        if len(bitmap_data) < bitmap_size:
            return None

        return width, height, bitmap_data


def get_grid_position(index):
    """Convert linear index (0-255) to (line, column) in 16x16 grid."""
    line = index // 16
    col = index % 16
    return line, col


def save_4bit_bmp(img, output_path, palette_rgb):
    """Write a palette-mode PIL image as a true 4-bit (16-color) BMP.

    PIL's BMP encoder always writes 8 bpp for mode "P", so we build the file
    by hand: BITMAPFILEHEADER + BITMAPINFOHEADER + 16-entry BGRA palette +
    bottom-up pixel rows packed as two 4-bit indices per byte (high nibble
    first), with each row padded to a 4-byte boundary.
    """
    width, height = img.size
    pixels = img.load()

    bytes_per_row = (width + 1) // 2  # 2 pixels per byte
    row_padding = (4 - bytes_per_row % 4) % 4
    padded_row_size = bytes_per_row + row_padding

    pixel_data_size = padded_row_size * height
    palette_size = 16 * 4  # 16 colors × BGRA
    data_offset = 14 + 40 + palette_size
    file_size = data_offset + pixel_data_size

    with open(output_path, "wb") as f:
        # BITMAPFILEHEADER
        f.write(b"BM")
        f.write(struct.pack("<I", file_size))
        f.write(struct.pack("<HH", 0, 0))  # reserved
        f.write(struct.pack("<I", data_offset))

        # BITMAPINFOHEADER
        f.write(struct.pack("<I", 40))      # header size
        f.write(struct.pack("<i", width))
        f.write(struct.pack("<i", height))
        f.write(struct.pack("<H", 1))       # planes
        f.write(struct.pack("<H", 4))       # bits per pixel
        f.write(struct.pack("<I", 0))       # BI_RGB, no compression
        f.write(struct.pack("<I", pixel_data_size))
        f.write(struct.pack("<i", 0))       # x pixels/meter
        f.write(struct.pack("<i", 0))       # y pixels/meter
        f.write(struct.pack("<I", 16))      # colors used
        f.write(struct.pack("<I", 0))       # important colors

        # Palette: 16 entries, BGRA (alpha/reserved = 0)
        for i in range(16):
            r = palette_rgb[i * 3]
            g = palette_rgb[i * 3 + 1]
            b = palette_rgb[i * 3 + 2]
            f.write(struct.pack("BBBB", b, g, r, 0))

        # Pixel data, bottom-up
        for y in range(height - 1, -1, -1):
            row = bytearray(padded_row_size)
            for x in range(0, width, 2):
                p1 = pixels[x, y] & 0x0F
                p2 = (pixels[x + 1, y] & 0x0F) if x + 1 < width else 0
                row[x // 2] = (p1 << 4) | p2
            f.write(bytes(row))


def place_char_in_image(img, char_width, char_height, bitmap_data, grid_line, grid_col, bg_color, fg_color):
    """Place a character bitmap into the image at grid position with specified colors.

    Empty (all-background) columns on the left and right of the source glyph are
    trimmed first, then the remaining columns are horizontally centered inside
    the 8x8 destination cell. Rows are not centered (top-aligned).
    Glyphs whose trimmed width exceeds 8 are clipped on the right.
    """
    pixel_x = grid_col * 8
    pixel_y = grid_line * 8

    # Fill the entire 8x8 cell with background color
    for y in range(8):
        for x in range(8):
            img.putpixel((pixel_x + x, pixel_y + y), bg_color)

    bytes_per_row = (char_width + 7) // 8

    def src_pixel(cx, cy):
        """Return the bit (0/1) at (cx, cy) in the source glyph, or 0 if out of range."""
        if cx < 0 or cx >= char_width or cy < 0 or cy >= char_height:
            return 0
        byte_index = cy * bytes_per_row + (cx // 8)
        if byte_index >= len(bitmap_data):
            return 0
        bit_index = 7 - (cx % 8)  # MSB first
        return (bitmap_data[byte_index] >> bit_index) & 1

    # Find the leftmost and rightmost columns that contain any foreground pixel.
    left_col = None
    right_col = None
    for cx in range(char_width):
        if any(src_pixel(cx, cy) for cy in range(char_height)):
            if left_col is None:
                left_col = cx
            right_col = cx

    # Entirely blank glyph: leave the cell as background and bail out.
    if left_col is None:
        return

    trimmed_width = right_col - left_col + 1

    # Horizontally center the trimmed glyph; clip on the right if wider than 8.
    if trimmed_width <= 8:
        x_offset = (8 - trimmed_width) // 2
        draw_width = trimmed_width
    else:
        x_offset = 0
        draw_width = 8

    for y in range(min(char_height, 8)):
        for x in range(draw_width):
            if src_pixel(left_col + x, y):
                img.putpixel((pixel_x + x_offset + x, pixel_y + y), fg_color)


def main():
    parser = argparse.ArgumentParser(
        description="Copy SCI font characters into a target 128x128 EGA image (4-bit)."
    )
    parser.add_argument(
        "--sci-font",
        type=Path,
        required=True,
        help="Path to SCI font file",
    )
    parser.add_argument(
        "--target-image",
        type=Path,
        required=True,
        help="Path to target EGA image (128x128, 4-bit color)",
    )
    parser.add_argument(
        "--from-index",
        type=int,
        required=True,
        help="Starting character index in SCI font",
    )
    parser.add_argument(
        "--to-index",
        type=int,
        required=True,
        help="Ending character index in SCI font (inclusive)",
    )
    parser.add_argument(
        "--target-start-index",
        type=int,
        required=True,
        help="Starting position in target image (0-255, linear index in 16x16 grid)",
    )
    parser.add_argument(
        "--bg-color",
        type=int,
        required=True,
        help="Background color index (0-15 for EGA)",
    )
    parser.add_argument(
        "--fg-color",
        type=int,
        required=True,
        help="Foreground/letter color index (0-15 for EGA)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output_font.bmp"),
        help="Output image path (default: output_font.bmp)",
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="Overwrite output file if it exists",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.from_index > args.to_index:
        print("Error: --from-index must be <= --to-index", file=__import__("sys").stderr)
        return 1

    if not (0 <= args.bg_color <= 15):
        print("Error: --bg-color must be 0-15", file=__import__("sys").stderr)
        return 1

    if not (0 <= args.fg_color <= 15):
        print("Error: --fg-color must be 0-15", file=__import__("sys").stderr)
        return 1

    if not (0 <= args.target_start_index <= 255):
        print("Error: --target-start-index must be 0-255", file=__import__("sys").stderr)
        return 1

    # Load or create target image
    if args.target_image.exists():
        src_img = Image.open(args.target_image)
        if src_img.size != (128, 128):
            print(
                f"Error: target image must be 128x128, got {img.size}",
                file=__import__("sys").stderr,
            )
            return 1
        img = src_img.copy().convert("P")
        ega_palette = [
            0,0,0, 0,0,170, 0,170,0, 0,170,170, 170,0,0, 170,0,170, 170,85,0, 170,170,170,
            85,85,85, 85,85,255, 85,255,85, 85,255,255, 255,85,85, 255,85,255, 255,255,85, 255,255,255
        ]
        full_palette = ega_palette + [0] * (768 - len(ega_palette))
        img.putpalette(full_palette)
        if img.mode != "P":
            print(
                f"Error: target image must be in palette mode, got {img.mode}",
                file=__import__("sys").stderr,
            )
            return 1

    else:
        # Create new 128x128 4-bit palette image
        img = Image.new("P", (128, 128), args.bg_color)
        # Create a 16-color palette (EGA colors)
        palette = []
        for i in range(16):
            palette.extend([i * 16, i * 16, i * 16])  # Grayscale for simplicity
        img.putpalette(palette)

    # Copy characters
    copied_count = 0
    skipped_count = 0
    target_index = args.target_start_index

    for sci_index in range(args.from_index, args.to_index + 1):
        if target_index > 255:
            print(
                f"Warning: target index {target_index} exceeds grid bounds, stopping",
                file=__import__("sys").stderr,
            )
            break

        char_data = parse_sci_font_char(args.sci_font, sci_index)
        if char_data is None:
            print(f"Skipping SCI font char {sci_index}: invalid or empty")
            skipped_count += 1
        else:
            width, height, bitmap_data = char_data
            grid_line, grid_col = get_grid_position(target_index)
            place_char_in_image(
                img, width, height, bitmap_data, grid_line, grid_col, args.bg_color, args.fg_color
            )
            copied_count += 1
            print(f"Copied SCI {sci_index} to target {target_index} (line {grid_line}, col {grid_col})")

        target_index += 1

    # Save output
    if args.output.exists() and not args.override:
        print(f"Error: output file exists: {args.output}", file=__import__("sys").stderr)
        print("Use --override to overwrite", file=__import__("sys").stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Pull the 16-color palette back out of the image and write a true 4-bit BMP.
    palette_rgb = img.getpalette() or []
    palette_rgb = (palette_rgb + [0] * 48)[:48]  # ensure 16 RGB entries
    save_4bit_bmp(img, args.output, palette_rgb)
    print(f"Wrote {copied_count} characters to {args.output} (4-bit BMP)")

    return 0


if __name__ == "__main__":
    exit(main())
