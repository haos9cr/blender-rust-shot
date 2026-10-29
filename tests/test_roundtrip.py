"""Byte-exact decode -> encode check for every .shot in samples/.

    python tests/test_roundtrip.py

No Blender needed. Exit code 1 if any file differs.
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import rust_shot  # noqa: E402


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "samples", "*.shot")))
    if not files:
        print("no samples found")
        return 1
    bad = 0
    for path in files:
        data = open(path, "rb").read()
        shot = json.loads(json.dumps(rust_shot.decode(data)))  # also proves JSON round trip
        ok = rust_shot.encode(shot) == data
        bad += not ok
        print("%s  %s" % ("OK  " if ok else "DIFF", os.path.basename(path)))
    print("%d/%d byte-exact" % (len(files) - bad, len(files)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
