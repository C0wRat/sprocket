"""A stand-in for SANE's `scanimage`, used by the tests.

Environment variables:
  FAKE_SCANIMAGE_MODE    ok (default) | stall | busy | fail-once
  FAKE_SCANIMAGE_STATE   file used by fail-once to remember that it already failed
  FAKE_SCANIMAGE_DELAY   seconds to wait after each row of pixels (default 0)
  FAKE_SCANIMAGE_FINISH  seconds spent "returning the film holder" at the end (default 0.2)

Like the real one, the first SIGTERM cancels the scan and the second aborts.
"""

import os
import signal
import struct
import sys
import time

OPTIONS = """
All options specific to device `fake':
  Scan Mode:
    --mode Color|Gray [Gray]
    --resolution 200|100dpi [100]
  Geometry:
    -l 0..36.33mm [0]
    -t 0..25mm [0]
    -x 0..36.33mm [36.33]
    -y 0..25mm [25]
"""

signals_received = 0


def on_signal(signum, frame):
    global signals_received
    signals_received += 1
    if signals_received == 1:
        print(f"scanimage: received signal {signum}", file=sys.stderr, flush=True)
        print("scanimage: trying to stop scanner", file=sys.stderr, flush=True)
    else:
        print("scanimage: aborting", file=sys.stderr, flush=True)
        os._exit(1)


def option(args, name, default):
    return args[args.index(name) + 1] if name in args else default


def main():
    args = sys.argv[1:]
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    if "-A" in args:
        print(OPTIONS)
        return 0

    mode = os.environ.get("FAKE_SCANIMAGE_MODE", "ok")
    if mode == "busy":
        print(f"scanimage: open of device {option(args, '-d', '?')} failed: Device busy", file=sys.stderr)
        return 1
    if mode == "fail-once":
        state = os.environ["FAKE_SCANIMAGE_STATE"]
        if not os.path.exists(state):
            open(state, "w").close()
            print("scanimage: sane_start: Invalid argument", file=sys.stderr)
            return 4
    if mode == "stall":
        while True:  # like the genesys backend waiting forever for data
            time.sleep(0.05)

    resolution = int(option(args, "--resolution", "100"))
    width = max(1, round(float(option(args, "-x", "36.33")) / 25.4 * resolution))
    height = max(1, round(float(option(args, "-y", "25")) / 25.4 * resolution))
    delay = float(os.environ.get("FAKE_SCANIMAGE_DELAY", "0"))

    # A colour negative of a grey ramp: the scene gets brighter to the right, so the
    # film gets denser; the orange mask adds a different density to each channel.
    row = []
    for x in range(width):
        density = 0.15 + 1.2 * x / max(width - 1, 1)
        for mask in (0.25, 0.6, 0.85):
            row.append(int(60000 * 10 ** -(density + mask)))
    row_bytes = struct.pack(f">{len(row)}H", *row)

    out = sys.stdout.buffer
    out.write(f"P6\n# SANE data follows\n{width} {height}\n65535\n".encode())
    out.flush()
    for _ in range(height):
        if signals_received:
            time.sleep(0.1)
            return 2
        out.write(row_bytes)
        out.flush()
        if delay:
            time.sleep(delay)
    time.sleep(float(os.environ.get("FAKE_SCANIMAGE_FINISH", "0.2")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
