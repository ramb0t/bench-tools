#!/usr/bin/env python3
"""Alientek DP100 pulse generator — bench test-rig tooling.

Cycles the DP100 output on/off at a fixed voltage/current to power-cycle a
device under test (DUT). Communicates over USB HID (VID 0x2e3c / PID 0xaf01).

This is standalone bench tooling — not part of the firmware build. Relocate it
out of scripts/ if you'd rather keep it separate.

Protocol (verified against github.com/palzhj/pydp100):
  64-byte HID reports, frame = [DIR][OP][0x00][LEN][DATA...][CRC16-lo][CRC16-hi]
  CRC-16/Modbus (poly 0x8005, refin/refout, init 0xFFFF). All values little-endian, mV / mA.

TIMING FLOOR: host-toggled USB HID gives ~50-100 ms per edge with jitter. This
tool is intended for edges >= ~0.5 s (power-cycle / reboot / brownout testing).
It cannot produce crisp sub-100 ms switching — for that you need an external
MOSFET on the output or the DP100's native sequence feature.

Requires: python 'hid' (cython-hidapi) and 'crcmod'.

Examples:
  # 12.0 V, 2 A limit, 2 s on / 1 s off, run forever (Ctrl-C to stop, output off)
  ./dp100_pulse.py --volts 12 --amps 2 --on 2 --off 1

  # 5.0 V, 10 pulses of 1 s on / 0.5 s off, then stop
  ./dp100_pulse.py --volts 5 --amps 1 --on 1 --off 0.5 --cycles 10
"""

import argparse
import signal
import sys
import time

import hid

try:
    import crcmod
except ImportError:
    sys.exit("error: missing dependency 'crcmod' (pip install crcmod)")

VID, PID = 0x2E3C, 0xAF01

DR_H2D = 0xFB          # host -> device
DR_D2H = 0xFA          # device -> host
OP_DEVICEINFO = 0x10
OP_BASICINFO = 0x30    # live measurements (Vin/Vout/Iout/temp/work_st)
OP_BASICSET = 0x35     # read/write output setpoint + enable

SET_MODIFY = 0x20      # apply the setpoint in this frame
SET_ACT = 0x80         # query the active setpoint

# BasicInfo byte 14 — output regulation mode
OUT_MODE = {0: "OFF", 1: "CV", 2: "CC"}
# BasicInfo byte 15 — working status. 0 == Normal; any nonzero is a protection
# trip and the device auto-disables the output (per the DP100 user manual).
WORK_ST = {0: "NM", 1: "OVP", 2: "OCP", 3: "OPP", 4: "OTP", 5: "UVP", 6: "REP"}

_crc16 = crcmod.mkCrcFun(0x18005, rev=True, initCrc=0xFFFF, xorOut=0x0000)


def _frame(op, data=b""):
    f = bytes([DR_H2D, op & 0xFF, 0x00, len(data) & 0xFF]) + data
    c = _crc16(f)
    return f + bytes([c & 0xFF, (c >> 8) & 0xFF])


def _setpoint(output, vset_mv, iset_ma, ovp_mv, ocp_ma):
    o = 1 if output else 0
    return bytes([
        SET_MODIFY, o,
        vset_mv & 0xFF, (vset_mv >> 8) & 0xFF,
        iset_ma & 0xFF, (iset_ma >> 8) & 0xFF,
        ovp_mv & 0xFF, (ovp_mv >> 8) & 0xFF,
        ocp_ma & 0xFF, (ocp_ma >> 8) & 0xFF,
    ])


class DP100:
    def __init__(self):
        self.dev = hid.device()
        self.dev.open(VID, PID)

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass

    def _txn(self, op, data=b"", want_op=None, timeout_ms=300):
        """Write a command and return the decoded reply payload for want_op (default: same op)."""
        want_op = op if want_op is None else want_op
        # flush stale input so we don't read a reply to a previous command
        self.dev.set_nonblocking(True)
        while self.dev.read(64):
            pass
        self.dev.set_nonblocking(False)
        self.dev.write(_frame(op, data))
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            r = self.dev.read(64, timeout_ms=timeout_ms)
            if not r:
                continue
            r = bytes(r)
            if r[0] != DR_D2H:
                continue
            rop, rlen = r[1], r[3]
            if _crc16(r[0:4 + rlen + 2]) != 0:  # 0 == CRC valid
                continue
            if rop == want_op:
                return r[4:4 + rlen]
        return None

    def basic_info(self):
        """Return dict of live measurements, or None on timeout."""
        d = self._txn(OP_BASICINFO)
        if d is None or len(d) < 16:
            return None
        u16 = lambda i: d[i] | (d[i + 1] << 8)
        return {
            "vin_mv": u16(0), "vout_mv": u16(2), "iout_ma": u16(4),
            "vo_max_mv": u16(6), "temp1": u16(8), "out_mode": d[14],
            "work_st": d[15],
        }

    def device_info(self):
        d = self._txn(OP_DEVICEINFO)
        if d is None:
            return None
        return d[0:15].split(b"\x00")[0].decode("utf-8", "replace")

    def set_output(self, output, vset_mv, iset_ma, ovp_mv, ocp_ma):
        """Apply setpoint + enable/disable. Returns True if the device ACKed."""
        return self._txn(OP_BASICSET,
                         _setpoint(output, vset_mv, iset_ma, ovp_mv, ocp_ma),
                         want_op=OP_BASICSET) is not None


def main():
    ap = argparse.ArgumentParser(
        description="Pulse the Alientek DP100 output on/off (power-cycle a DUT).",
        epilog="Timing floor ~50-100 ms/edge over USB HID; intended for edges >= ~0.5 s.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--volts", type=float, required=True,
                    help="output voltage during the ON phase (V) — REQUIRED, no default")
    ap.add_argument("--amps", type=float, required=True,
                    help="current limit during the ON phase (A) — REQUIRED")
    ap.add_argument("--on", type=float, default=2.0, help="ON duration (s)")
    ap.add_argument("--off", type=float, default=1.0, help="OFF duration (s)")
    ap.add_argument("--cycles", type=int, default=0,
                    help="number of on/off cycles (0 = run until Ctrl-C)")
    ap.add_argument("--ovp", type=float, default=None,
                    help="over-voltage protection (V), default = volts + 1.0")
    ap.add_argument("--ocp", type=float, default=None,
                    help="over-current protection (A), default = amps + 0.5")
    args = ap.parse_args()

    vset_mv = round(args.volts * 1000)
    iset_ma = round(args.amps * 1000)
    ovp_mv = round((args.ovp if args.ovp is not None else args.volts + 1.0) * 1000)
    ocp_ma = round((args.ocp if args.ocp is not None else args.amps + 0.5) * 1000)

    if args.on <= 0 or args.off < 0:
        sys.exit("error: --on must be > 0 and --off must be >= 0")

    try:
        psu = DP100()
    except Exception as e:
        sys.exit(f"error: cannot open DP100 ({e}). Is it plugged in? "
                 "Permissions: install scripts/99-atk-dp100.rules or run as root.")

    # Graceful, guaranteed output-off on SIGTERM as well as Ctrl-C / exceptions.
    def _sigterm(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _sigterm)

    pulses_done = 0
    try:
        info = psu.basic_info()
        if info is None:
            sys.exit("error: no response from DP100 (OP_BASICINFO timeout).")

        # Buck-only: output cannot exceed input. Refuse rather than silently clamp.
        vo_max_v = info["vo_max_mv"] / 1000.0
        print(f"DP100 '{psu.device_info()}'  Vin={info['vin_mv']/1000:.2f} V  "
              f"max settable Vout={vo_max_v:.2f} V")
        if vset_mv > info["vo_max_mv"]:
            sys.exit(f"error: requested {args.volts:.2f} V exceeds the device max "
                     f"settable {vo_max_v:.2f} V (DP100 is buck-only; raise the "
                     f"USB-C/DC input voltage).")

        print(f"pulsing: {args.volts:.2f} V / {args.amps:.2f} A  "
              f"OVP={ovp_mv/1000:.2f} V OCP={ocp_ma/1000:.2f} A  "
              f"{args.on:g}s on / {args.off:g}s off  "
              f"{'forever' if args.cycles == 0 else f'{args.cycles} cycles'}")
        print("Ctrl-C to stop (output is forced OFF on exit).\n")

        while args.cycles == 0 or pulses_done < args.cycles:
            # ----- ON -----
            psu.set_output(True, vset_mv, iset_ma, ovp_mv, ocp_ma)
            time.sleep(args.on)
            m = psu.basic_info()
            if m is None:
                print(f"cycle {pulses_done+1}: WARN no telemetry read")
            else:
                # A trip is a nonzero working status (OVP/OCP/OPP/OTP/UVP/REP);
                # the device auto-disables the output in those states. NM (0) and
                # CC current-limiting are both normal operation, not trips.
                tripped = m["work_st"] != 0
                st = WORK_ST.get(m["work_st"], f"0x{m['work_st']:02x}")
                mode = OUT_MODE.get(m["out_mode"], f"0x{m['out_mode']:02x}")
                print(f"cycle {pulses_done+1}: ON  "
                      f"Vout={m['vout_mv']/1000:.2f} V  Iout={m['iout_ma']/1000:.3f} A  "
                      f"mode={mode} status={st}{'  <-- TRIPPED' if tripped else ''}")
                if tripped:
                    print(f"\nprotection trip ({st}) — output auto-disabled by the "
                          "device. Stopping so the rig doesn't log phantom pulses. "
                          "Check OVP/OCP/OPP/OTP vs. DUT inrush.")
                    break
            # ----- OFF -----
            psu.set_output(False, vset_mv, iset_ma, ovp_mv, ocp_ma)
            pulses_done += 1
            if args.cycles == 0 or pulses_done < args.cycles:
                time.sleep(args.off)

    except KeyboardInterrupt:
        print("\ninterrupted.")
    finally:
        # Non-negotiable: leave the rail OFF on every exit path.
        try:
            psu.set_output(False, vset_mv, iset_ma, ovp_mv, ocp_ma)
            print(f"output OFF. completed {pulses_done} pulse(s).")
        except Exception as e:
            print(f"WARNING: failed to disable output on exit: {e}", file=sys.stderr)
        psu.close()


if __name__ == "__main__":
    main()
