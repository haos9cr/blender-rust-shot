"""Byte-exact read -> write check for every sample in samples/.

    python tests/test_roundtrip.py

  *.shot                 decode -> encode (protobuf)
  *.demo-controls.json   load -> write (Json.NET formatting)

No Blender needed. Exit code 1 if any file differs.
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import rust_shot  # noqa: E402


def check_shot(path):
    data = open(path, "rb").read()
    shot = json.loads(json.dumps(rust_shot.decode(data)))  # also proves JSON round trip
    return rust_shot.encode(shot) == data


def check_controls(path):
    raw = open(path, "rb").read().decode("utf-8")
    return rust_shot.controls_text(rust_shot.controls_load(path)) == raw


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
    print("%d/%d byte-exact" % (len(files) - bad, len(files)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
