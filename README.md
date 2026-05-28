# bench-tools

Scripts for automating lab bench instruments over USB. Plain Python CLIs — clone,
install the two dependencies, and run with `./`. No packaging, no build step.

Each instrument gets its own subdirectory. Shared helpers can be factored out per
tool as they grow.

## Tools

| Path | Instrument | What it does |
|------|------------|--------------|
| [`dp100/dp100_pulse.py`](dp100/dp100_pulse.py) | Alientek DP100 PSU | Pulse the output on/off to power-cycle a device under test |

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
