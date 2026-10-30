"""Byte-exact read -> write check for every sample in samples/.

    python tests/test_roundtrip.py

  *.shot                 decode -> encode (protobuf)
  *.demo-controls.json   load -> write (same values and layout as the game's file)

No Blender needed. Exit code 1 if any file differs.
"""
import glob
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import rust_shot  # noqa: E402


def check_shot(path):
    data = open(path, "rb").read()
    shot = json.loads(json.dumps(rust_shot.decode(data)))  # also proves JSON round trip
    return rust_shot.encode(shot) == data


def _same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return struct.pack("<f", float(a)) == struct.pack("<f", float(b))
    if isinstance(a, dict):
        return isinstance(b, dict) and list(a) == list(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def check_controls(path):
    """Same values (as float32) and same layout. The text may differ in a rare last digit: the game (Mono)
    sometimes writes 9 digits where 7 already round-trip; both read back to the same float."""
    raw = open(path, "rb").read().decode("utf-8-sig")
    out = rust_shot.controls_text(rust_shot.controls_load(path))
    return _same(json.loads(raw), json.loads(out)) and raw.count("\r\n") == out.count("\r\n")


def main():
    files = [(p, check_shot) for p in glob.glob(os.path.join(ROOT, "samples", "*.shot"))]
    files += [(p, check_controls) for p in glob.glob(os.path.join(ROOT, "samples", "*.demo-controls.json"))]
    if not files:
        print("no samples found")
        return 1
    bad = 0
    for path, check in sorted(files):
        ok = check(path)
        bad += not ok
        print("%s  %s" % ("OK  " if ok else "DIFF", os.path.basename(path)))
    print("%d/%d OK" % (len(files) - bad, len(files)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
