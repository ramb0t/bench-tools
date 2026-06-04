# bench-tools

Scripts for automating lab bench instruments over USB. Plain Python CLIs — clone,
install the two dependencies, and run with `./`. No packaging, no build step.

Each instrument gets its own subdirectory. Shared helpers can be factored out per
tool as they grow.

## Tools

| Path | Instrument | What it does |
|------|------------|--------------|
| [`dp100/dp100_pulse.py`](dp100/dp100_pulse.py) | Alientek DP100 PSU | Pulse the output on/off to power-cycle a device under test |
| [`phomemo-m110/print-label`](phomemo-m110/print-label) | Phomemo M110(S) label printer | Print a PNG/SVG label (e.g. filament spool labels) over USB |

## Setup

```bash
pip install -r requirements.txt   # hid (cython-hidapi) + crcmod
```

### USB permissions (Linux)

Most tools talk to USB-HID instruments via `hidraw`. On a desktop session your
logged-in seat usually gets an ACL automatically. For headless/cron use, or if
you hit a permission error, install the relevant udev rule and replug:

```bash
sudo cp dp100/99-atk-dp100.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## DP100 pulse generator

```bash
cd dp100

# 12 V, 2 A limit, 2 s on / 1 s off, run until Ctrl-C (output forced OFF on exit)
./dp100_pulse.py --volts 12 --amps 2 --on 2 --off 1

# 5 V, 10 pulses of 1 s on / 0.5 s off, then stop
./dp100_pulse.py --volts 5 --amps 1 --on 1 --off 0.5 --cycles 10

./dp100_pulse.py --help
```

Safety / behaviour notes:

- **Output is forced OFF on every exit path** — normal completion, exception,
  Ctrl-C, and SIGTERM all disable the rail before the process exits.
- **Buck-only ceiling:** the DP100 cannot output more than its input. The tool
  reads the device's max settable voltage and refuses (rather than silently
  clamping) if you ask for more.
- **Trip detection:** after each ON phase it reads `work_st`; a real protection
  trip (OVP/OCP/OPP/OTP/UVP/REP) auto-disables the output and the tool stops so
  the run doesn't log phantom pulses. Normal (NM) and CC current-limiting are
  not treated as trips.
- **Timing floor:** host-toggled USB HID gives ~50–100 ms per edge with jitter.
  This tool is for edges ≥ ~0.5 s (power-cycle / reboot / brownout testing).
  Crisp sub-100 ms switching needs an external MOSFET on the output or the
  device's native sequence feature — not this tool.

### Credits

The DP100 USB-HID protocol (opcodes, frame layout, CRC parameters, field
offsets) was reverse-engineered by the community. This tool's understanding of
it comes primarily from **[palzhj/pydp100](https://github.com/palzhj/pydp100)** —
thanks to the author for documenting it.

`dp100_pulse.py` is an independent implementation, not a copy: pydp100 carries no
license, so its code was not reused. Only the protocol facts (which describe
Alientek's hardware, not pydp100's code) informed this re-implementation. The
output regulation modes and `work_st` protection-status values (NM/OVP/OCP/OPP/
OTP/UVP/REP) come from the [Alientek DP100 user
manual](https://manuals.plus/alientek/dp100-high-performance-digital-power-manual).

## Phomemo M110 label printer

There is no official Phomemo Linux driver. `phomemo_m110_print.py` converts an
image to the M110's raster format and writes it to the printer; `print-label` is
a thin wrapper that runs the converter and sends the bytes to the device. Built
for printing filament spool labels from
[3dfilamentprofiles.com](https://3dfilamentprofiles.com) (export as PNG or SVG).

```bash
cd phomemo-m110

# 40x30 mm label, tuned defaults (align right, speed 2, density 2)
./print-label ~/Downloads/some-label.png      # PNG or SVG

# other sizes from the site:
./print-label label.png --width 50 --height 30   # Expanded
./print-label label.png --width 30 --height 40   # Vertical
./print-label label.png --width 40 --height 12   # Slim

./phomemo_m110_print.py --help
```

Connection / setup:

- USB device `0483:5740` (Jieli chip) appears as `/dev/usb/lp0` via the `usblp`
  kernel module; CUPS' usb backend sees it as `usb:///M110S`.
- Writing the device needs group `lp`: `sudo usermod -aG lp $USER`, then log out
  and back in. Until then `print-label` falls back to `sudo` (askpass).
- SVG input is rasterized with `rsvg-convert` straight onto the 320-dot grid
  (`sudo dnf install librsvg2-tools`). SVG and PNG output are virtually identical
  in practice — the 203 dpi thermal head is the limiter, not the source.

Tuning notes (this unit, cheap label stock):

- **Media is right-referenced** (label's right edge = head's rightmost), so the
  default is `--align right`. `--xoff <dots>` nudges (8 dots = 1 mm).
- The head is 48 bytes / 384 dots / 48 mm wide; a 40 mm label uses the rightmost
  320 dots.
- Hard threshold (no dithering) keeps the QR code scannable — important.
- Counter-intuitively this unit prints **cleanest at low heat**: large solid
  fills bleed at high energy. Stepping density down 8 → 1 kept improving; the
  defaults `--speed 2 --density 2` settled as the sweet spot. Density 0 is
  "off/default", not lighter, and speed 1 is the slowest.
- A blotch always in the same spot is usually print-head residue — wipe the head
  with isopropyl alcohol (printer off, head cool).

### Credits

M110 raster protocol (init/density/speed bytes, the `GS v 0` raster command, and
the trailing feed) from
[vivier/phomemo-tools](https://github.com/vivier/phomemo-tools), a community CUPS
driver — thanks to the author for documenting it. `phomemo_m110_print.py` is an
independent standalone implementation using those protocol facts.
