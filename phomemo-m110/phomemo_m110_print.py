#!/usr/bin/env python3
"""Convert an image to Phomemo M110 raster bytes and (optionally) print it.

M110 protocol values from vivier/phomemo-tools (reverse-engineered, tested):
  init    : 1b 4e 0d 05  (speed=fast)  1b 4e 04 0f (density=max)  1f 11 0a (label w/ gaps)
  raster  : 1d 76 30 00  <wbytes LE16> <hlines LE16>  <packed bits, MSB=left, 1=black>
  trailer : 1f f0 05 00  1f f0 03 00   (commit + feed to gap)

203 dpi == 8 dots/mm. M110 head line width = 43 bytes = 344 dots.
A 40x30 mm label = 320x240 dots; content is left-aligned in the 43-byte line.
"""
import sys, argparse, subprocess, tempfile, os
from PIL import Image

def load_image(path, content_w):
    """Load a raster image, or rasterize an SVG straight onto the dot grid
    (render at the target print width so text/QR snap to dots — no downscale softening)."""
    if path.lower().endswith(".svg"):
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        subprocess.run(["rsvg-convert", "-w", str(content_w), "-o", tmp.name, path], check=True)
        im = Image.open(tmp.name).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))  # flatten transparency to white
        im = Image.alpha_composite(bg, im).convert("L")
        os.unlink(tmp.name)
        return im
    return Image.open(path).convert("L")

DPMM = 8                  # 203 dpi
MAX_LINES_PER_BLOCK = 240 # 0xf0, matches vivier chunking

def build(img_path, label_w_mm, label_h_mm, threshold, invert, rotate, align,
          head_bytes, density, speed, xoff):
    content_w = label_w_mm * DPMM          # e.g. 40mm -> 320 dots
    target_h  = label_h_mm * DPMM          # e.g. 30mm -> 240 dots

    im = load_image(img_path, content_w)
    if rotate:
        im = im.rotate(rotate, expand=True)
    # scale to fit within content_w x target_h, preserve aspect
    src_w, src_h = im.size
    scale = min(content_w / src_w, target_h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    im = im.resize((new_w, new_h), Image.LANCZOS)

    # hard threshold -> 1-bit (point() so QR stays crisp; no dithering)
    bw = im.point(lambda p: 0 if (p < threshold) ^ invert else 255, mode="1")

    # paste onto a white canvas exactly head_bytes*8 wide x new_h tall
    canvas_w = head_bytes * 8
    canvas = Image.new("1", (canvas_w, new_h), 1)  # 1 = white
    if align == "left":
        x = 0
    elif align == "right":
        x = canvas_w - new_w
    else:  # center within the full head width
        x = (canvas_w - new_w) // 2
    x = max(0, min(canvas_w - new_w, x + xoff))
    canvas.paste(bw, (x, 0))

    # pack bits: 1 = black. PIL "1" mode: 0=black,255/1=white -> invert to printer convention
    px = canvas.load()
    h = new_h
    raster = bytearray()
    for y in range(h):
        for bx in range(head_bytes):
            byte = 0
            for bit in range(8):
                xx = bx * 8 + bit
                # px gives 0 (black) or 255 (white) in mode "1"
                if px[xx, y] == 0:
                    byte |= (1 << (7 - bit))
            raster.append(byte)

    out = bytearray()
    out += bytes([0x1b,0x4e,0x0d, speed & 0xff,
                  0x1b,0x4e,0x04, density & 0xff,
                  0x1f,0x11,0x0a])
    line = 0
    while line < h:
        block = min(MAX_LINES_PER_BLOCK, h - line)
        out += bytes([0x1d,0x76,0x30,0x00,
                      head_bytes & 0xff, (head_bytes >> 8) & 0xff,
                      block & 0xff, (block >> 8) & 0xff])
        out += raster[line*head_bytes : (line+block)*head_bytes]
        line += block
    out += bytes([0x1f,0xf0,0x05,0x00, 0x1f,0xf0,0x03,0x00])
    return bytes(out), canvas

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("-o", "--out", help="write raster bytes to this file (else stdout)")
    ap.add_argument("--preview", help="save 1-bit preview PNG here")
    ap.add_argument("--width",  type=int, default=40, help="label width mm")
    ap.add_argument("--height", type=int, default=30, help="label height mm")
    ap.add_argument("--threshold", type=int, default=160, help="0-255, higher = more black")
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--rotate", type=int, default=0)
    ap.add_argument("--align", choices=["left","center","right"], default="right")
    ap.add_argument("--head-bytes", type=int, default=48, help="printer head line width in bytes (48=384dots/48mm)")
    ap.add_argument("--density", type=int, default=2, help="thermal density 1-15 (higher=darker; this unit prints cleanest low)")
    ap.add_argument("--speed", type=int, default=2, help="print speed 1-5 (lower=slower=sharper)")
    ap.add_argument("--xoff", type=int, default=0, help="horizontal nudge in dots (+right/-left)")
    a = ap.parse_args()

    data, canvas = build(a.image, a.width, a.height, a.threshold, a.invert, a.rotate,
                         a.align, a.head_bytes, a.density, a.speed, a.xoff)
    if a.preview:
        canvas.save(a.preview)
        sys.stderr.write(f"preview -> {a.preview} ({canvas.size[0]}x{canvas.size[1]})\n")
    sys.stderr.write(f"{len(data)} bytes of raster ready\n")
    if a.out:
        open(a.out, "wb").write(data)
    else:
        sys.stdout.buffer.write(data)

if __name__ == "__main__":
    main()
