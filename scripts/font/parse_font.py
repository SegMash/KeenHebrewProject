#!/usr/bin/env python3
"""
SCI Font Parser - Extracts characters from SCI font files as PNG images.
Based on the SCI font format specification.
"""

import argparse
import struct
import os
from pathlib import Path
from PIL import Image


def create_image(width, height, bitmap_data):
    """Create a PIL Image from SCI font bitmap data."""
    # Create a new black and white image
    img = Image.new('1', (width, height), 0)
    
    bytes_per_row = (width + 7) // 8
    
    # Set pixels
    for y in range(height):
        for x in range(width):
            byte_index = y * bytes_per_row + (x // 8)
            bit_index = 7 - (x % 8)  # MSB first
            
            if byte_index < len(bitmap_data):
                pixel = (bitmap_data[byte_index] >> bit_index) & 1
                img.putpixel((x, y), pixel)
    
    return img


def parse_font(font_path, output_dir):
    """Parse SCI font file and extract all characters as BMP files."""
    
    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    with open(font_path, 'rb') as f:
        # Read header
        reserved = struct.unpack('<I', f.read(4))[0]
        num_chars = struct.unpack('<H', f.read(2))[0]
        line_height = struct.unpack('<H', f.read(2))[0]
        
        print(f"Font Information:")
        print(f"  Reserved data: {reserved}")
        print(f"  Number of characters: {num_chars}")
        print(f"  Line height: {line_height} pixels")
        print()
        
        # Read character pointers
        char_pointers = []
        for i in range(num_chars):
            pointer = struct.unpack('<H', f.read(2))[0]
            char_pointers.append(pointer)
        
        # Extract each character
        for char_num in range(num_chars):
            # Seek to character data (add 2 bytes offset to pointer)
            actual_address = char_pointers[char_num] + 2
            f.seek(actual_address)
            
            # Read character dimensions (HEIGHT first, then WIDTH)
            width = struct.unpack('B', f.read(1))[0]
            height = struct.unpack('B', f.read(1))[0]
            if width == 0 or height == 0:
                print(f"Character {char_num}: Empty (0x0)")
                continue
            
            # Calculate bitmap size
            bytes_per_row = (width + 7) // 8
            bitmap_size = height * bytes_per_row
            
            # Read bitmap data
            bitmap_data = f.read(bitmap_size)
            
            if len(bitmap_data) < bitmap_size:
                print(f"Character pointer: {char_pointers[char_num]} char: {char_num}: Incomplete data (expected {bitmap_size}, got {len(bitmap_data)})")
                continue
            
            # Create image
            img = create_image(width, height, bitmap_data)
            
            # Save to file
            output_file = output_path / f"{char_num}.png"
            img.save(output_file)
            
            print(f"Character {char_num}: {width}x{height} pixels -> {output_file.name}")
        
        print(f"\nSuccessfully exported {num_chars} characters to {output_dir}")


def main():
    parser = argparse.ArgumentParser(
        description='Parse SCI font files and export characters as PNG images.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Example:
  python parse_font.py kq4_resources/font.000 output_fonts/
        '''
    )
    
    parser.add_argument('font_file', help='Path to the SCI font file')
    parser.add_argument('output_dir', help='Directory to save the PNG files')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.font_file):
        print(f"Error: Font file '{args.font_file}' not found.")
        return 1
    
    try:
        parse_font(args.font_file, args.output_dir)
        return 0
    except Exception as e:
        print(f"Error parsing font: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit(main())
