"""
Rust demo camera: Blender import/export add-on.

Two formats:
  * Demo controls  demos/controls/<demo>.demo-controls.json   (Rust staging, Oct 2026 and later)
      Plain JSON. Scenes with a camera path, animated tracks (time of day, weather, fog, DOF, FOV, effects...)
      and base values. Imported as an animated camera with custom properties; exported back into the same
      file (other scenes, clones, lights and export range are kept).
  * Legacy shot    .shot (protobuf, older game versions), format below.

Legacy .shot format (reverse-engineered, protobuf):
  ShotFile
    1  string  name                 ("test_1_000_000")
    2  string  demo path            ("demos/xxx.dem")
    3  float   start time inside the demo, seconds (omitted when 0)
    4  float   duration, seconds
    5  Track   position   keys: {1 float time, 2 Vector3 {1 x, 2 y, 3 z}}
    6  Track   rotation   keys: {1 float time, 2 x, 3 y, 4 z, 5 w}   (Unity quaternion)
    7  Track   fov        keys: {1 float time, 2 float fov_deg}     (debug.debugcamera_fov, degrees)
    9  Track   dof focus distance  (graphics.dof_focus_dist)  - only with "record DOF" enabled
    10 Track   dof aperture        (graphics.dof_aper)
    11 Track   dof blur            (graphics.dof_blur)
    12 bytes   unknown, copied verbatim ({2:{3:{}}})
    8  string  folder the shot was saved into (omitted if none)
  Euler angles in the debug camera = Unity Quaternion.Euler(x, y, z) (applied Z, X, Y).
  Track: repeated field 2 = Keyframe
  Zero floats are omitted (proto3 style). Coordinates are Unity: X right, Y up, Z forward.

CLI (no Blender needed):
  python rust_shot.py decode in.shot out.json
  python rust_shot.py encode in.json out.shot
  python rust_shot.py roundtrip in.shot          # checks byte-exact re-encode

Blender: Edit > Preferences > Add-ons > Install... > pick this file.
  3D View > N sidebar > "Rust Shot" tab: import, per-camera settings, export settings, FPS tools.
  File > Import / Export > Rust demo controls (.json) / Rust camera shot (.shot).
  Timing: seconds = (frame - range start) / FPS / speed (+ "Demo time at start" for controls).
"""

import json
import math
import struct
import sys

bl_info = {
    "name": "Rust camera shot (.shot)",
    "author": "haos9cr",
    "version": (0, 4, 0),
    "blender": (3, 0, 0),
    "location": "File > Import/Export",
    "description": "Import/export Rust demo camera paths and controls (.demo-controls.json, .shot)",
    "category": "Import-Export",
}

# ---------------------------------------------------------------- protobuf low level


def _read_varint(b, i):
    r = 0
    s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def _fields(b):
    """Yield (field, wire, value, raw_bytes_of_whole_field)."""
    i = 0
    n = len(b)
    while i < n:
        start = i
        key, i = _read_varint(b, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = _read_varint(b, i)
        elif w == 1:
            v = struct.unpack_from("<d", b, i)[0]
            i += 8
        elif w == 5:
            v = struct.unpack_from("<f", b, i)[0]
            i += 4
        elif w == 2:
            ln, i = _read_varint(b, i)
            v = bytes(b[i:i + ln])
            i += ln
        else:
            raise ValueError("unsupported wire type %d at offset %d" % (w, start))
        yield f, w, v, bytes(b[start:i])


def _varint(v, min_len=1):
    out = bytearray()
    while True:
        c = v & 0x7F
        v >>= 7
        if v or len(out) + 1 < min_len:
            out.append(c | 0x80)
        else:
            out.append(c)
            return bytes(out)


def _tag(f, w):
    return _varint((f << 3) | w)


def _f32(f, v):
    if v == 0.0:  # proto3: default values are not written
        return b""
    return _tag(f, 5) + struct.pack("<f", v)


def _bytes(f, data, min_len=1):
    return _tag(f, 2) + _varint(len(data), min_len) + data


# ---------------------------------------------------------------- decode / encode


def _kv(b):
    d = {}
    for f, w, v, _ in _fields(b):
        d[f] = v
    return d


def decode(data):
    shot = {"name": "", "demo": "", "start": 0.0, "duration": 0.0, "folder": "",
            "position": [], "rotation": [], "fov": [],
            "dof_dist": [], "dof_aper": [], "dof_blur": [], "_raw": {}}
    for f, w, v, raw in _fields(data):
        if f == 1:
            shot["name"] = v.decode("utf-8")
        elif f == 2:
            shot["demo"] = v.decode("utf-8")
        elif f == 3:
            shot["start"] = v
        elif f == 4:
            shot["duration"] = v
        elif f == 8:
            shot["folder"] = v.decode("utf-8")
        elif f in (5, 6, 7, 9, 10, 11):
            for kf, kw, kv, _ in _fields(v):
                if kf != 2:
                    raise ValueError("unexpected field %d in track %d" % (kf, f))
                k = _kv(kv)
                extra = set(k) - {1, 2, 3, 4, 5}
                if extra:
                    print("warning: unknown keyframe fields", extra, file=sys.stderr)
                t = k.get(1, 0.0)
                if f == 5:
                    p = _kv(k.get(2, b""))
                    shot["position"].append([t, p.get(1, 0.0), p.get(2, 0.0), p.get(3, 0.0)])
                elif f == 6:
                    shot["rotation"].append([t, k.get(2, 0.0), k.get(3, 0.0), k.get(4, 0.0), k.get(5, 0.0)])
                else:
                    shot[SCALAR_TRACKS[f]].append([t, k.get(2, 0.0)])
        else:
            shot["_raw"][str(f)] = raw.hex()  # unknown field: keep verbatim
    return shot


SCALAR_TRACKS = {7: "fov", 9: "dof_dist", 10: "dof_aper", 11: "dof_blur"}
DEFAULT_RAW = {"12": "6288808080001282808080001a00"}
ORDER = [1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 8]  # field order the game writes


def encode(shot):
    def pos_track():
        out = bytearray()
        for t, x, y, z in shot["position"]:
            vec = _f32(1, x) + _f32(2, y) + _f32(3, z)
            out += _bytes(2, _f32(1, t) + (_bytes(2, vec) if vec else b""))
        return bytes(out)

    def rot_track():
        out = bytearray()
        for t, x, y, z, w in shot["rotation"]:
            out += _bytes(2, _f32(1, t) + _f32(2, x) + _f32(3, y) + _f32(4, z) + _f32(5, w))
        return bytes(out)

    def scalar_track(key):
        out = bytearray()
        for t, val in shot.get(key) or []:
            out += _bytes(2, _f32(1, t) + _f32(2, val))
        return bytes(out)

    raw = shot.get("_raw") or DEFAULT_RAW
    out = bytearray()
    for f in ORDER:
        if f == 1:
            out += _bytes(1, shot["name"].encode("utf-8"))
        elif f == 2:
            out += _bytes(2, shot["demo"].encode("utf-8"))
        elif f == 3:
            out += _f32(3, shot.get("start", 0.0))
        elif f == 4:
            out += _f32(4, shot["duration"])
        elif f == 8:
            if shot.get("folder"):
                out += _bytes(8, shot["folder"].encode("utf-8"))
        elif f == 5:
            out += _bytes(5, pos_track(), 3)  # the game writes track lengths as >=3-byte varints
        elif f == 6:
            out += _bytes(6, rot_track(), 3)
        elif f == 7:
            out += _bytes(7, scalar_track("fov"), 3)
        elif f in (9, 10, 11):
            if shot.get(SCALAR_TRACKS[f]):
                out += _bytes(f, scalar_track(SCALAR_TRACKS[f]), 3)
        elif str(f) in raw:
            out += bytes.fromhex(raw[str(f)])
    return bytes(out)


def load(path):
    with open(path, "rb") as fh:
        return decode(fh.read())


def save(shot, path):
    with open(path, "wb") as fh:
        fh.write(encode(shot))


# ---------------------------------------------------------------- Unity <-> Blender math (no deps)
# Blender is Z-up right-handed, Unity is Y-up left-handed: swap Y and Z (matrix S).
# Unity camera looks along local +Z, Blender camera along local -Z (matrix K = diag(1,1,-1)).
#   blender_cam_matrix = S * R_unity * K      R_unity = S * blender_cam_matrix * K


def quat_to_mat(x, y, z, w):
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def mat_to_quat(m):
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    n = math.sqrt(x * x + y * y + z * z + w * w)
    return x / n, y / n, z / n, w / n


def _swap_rows_cols(m):
    # S*M*K with S = swap y/z, K = diag(1,1,-1)
    s = [m[0], m[2], m[1]]
    return [[r[0], r[1], -r[2]] for r in s]


def unity_to_blender(pos, quat):
    x, y, z = pos
    return (x, z, y), _swap_rows_cols(quat_to_mat(*quat))


def blender_to_unity(loc, rotmat):
    x, y, z = loc
    return (x, z, y), mat_to_quat(_swap_rows_cols(rotmat))


# ---------------------------------------------------------------- demo controls (.demo-controls.json)
# Rust staging, Oct 2026: demos/controls/<demo>.demo-controls.json, plain JSON written by Json.NET.
#   { Version, CurrentSceneId, Scenes: [ { Id, Name, Data: {
#       Version, Properties: [{Id, Value}], Tracks: [{Id, Keyframes: [{Time, Value, Easing}]}],
#       CameraPath: {Algorithm, Transform: [{Time, Px, Py, Pz, Qx, Qy, Qz, Qw, Focal}]},
#       Clones: [], Lights: [], HasExportRange, ExportStart, ExportEnd } } ] }
# Times are seconds on the demo timeline. Positions/rotations are Unity world space (same as .shot).

# Easing = tangent mode of a key, in the order of the in-game "Tangents" buttons.
# (The game writes it, but staging currently loads every key back as Smooth.)
EASING_NAMES = ["SMOOTH", "FLAT", "LINEAR", "STEP", "BROKEN"]
WEATHER_NAMES = ["Clear", "Overcast", "Rain", "Storm", "Snow"]

# id: (label, default, min, max, kind)   kind: float | int | bool | weather
CONTROL_INFO = {
    "camera.fov":            ("FOV", 90.0, 1.0, 179.0, "float"),
    "camera.focallength":    ("Focal length", 50.0, 1.0, 1000.0, "float"),
    "camera.focusdistance":  ("Focus distance", 10.0, 0.0, 10000.0, "float"),
    "camera.aperture":       ("Aperture", 16.0, 0.1, 128.0, "float"),
    "camera.dofblur":        ("DOF blur", 16.0, 0.0, 100.0, "float"),
    "camera.roll":           ("Roll", 0.0, -360.0, 360.0, "float"),
    "camera.cinematicshake": ("Cinematic shake", 0.0, 0.0, 10.0, "float"),
    "dof.enabled":           ("DOF enabled", 0.0, 0.0, 1.0, "bool"),
    "dof.mode":              ("DOF mode", 0.0, 0.0, 10.0, "int"),
    "dof.quality":           ("DOF quality", 0.0, 0.0, 10.0, "int"),
    "dof.focustime":         ("Focus time", 0.2, 0.0, 10.0, "float"),
    "dof.barrel":            ("Barrel", 0.0, -10.0, 10.0, "float"),
    "dof.squeeze":           ("Squeeze", 0.0, -10.0, 10.0, "float"),
    "dof.debug":             ("DOF debug", 0.0, 0.0, 1.0, "bool"),
    "env.timeofday":         ("Time of day", 12.0, 0.0, 24.0, "float"),
    "env.sunrotation":       ("Sun rotation", 0.0, 0.0, 360.0, "float"),
    "env.weather":           ("Weather", 0.0, 0.0, 4.0, "weather"),
    "env.clouds":            ("Clouds", 0.0, 0.0, 1.0, "float"),
    "env.fog":               ("Fog", 0.0, 0.0, 1.0, "float"),
    "env.wind":              ("Wind", 0.0, 0.0, 1.0, "float"),
    "fx.ao":                 ("Ambient occlusion", 1.0, 0.0, 1.0, "float"),
    "fx.bloom":              ("Bloom", 1.0, 0.0, 1.0, "float"),
    "fx.chromatic":          ("Chromatic aberration", 0.0, 0.0, 1.0, "float"),
    "fx.filmgrain":          ("Film grain", 0.0, 0.0, 1.0, "float"),
    "fx.lensdirt":           ("Lens dirt", 0.0, 0.0, 1.0, "float"),
    "fx.motionblur":         ("Motion blur", 1.0, 0.0, 1.0, "float"),
    "fx.screenvignette":     ("Screen vignette", 0.0, 0.0, 1.0, "float"),
    "fx.shafts":             ("Sun shafts", 0.0, 0.0, 1.0, "float"),
    "fx.sharpen":            ("Sharpen", 1.0, 0.0, 1.0, "float"),
    "fx.vignette":           ("Vignette", 0.0, 0.0, 1.0, "float"),
}
CONTROL_GROUPS = [("camera", "Camera"), ("dof", "Depth of field"), ("env", "Environment"), ("fx", "Effects")]


def _f32_text(v):
    """float32 as .NET 'R' writes it (G7, or G9 if G7 does not round-trip) + Json.NET's '.0'."""
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):
        v = 0.0
    want = struct.pack("<f", v)
    t = "%.7g" % v
    if struct.pack("<f", float(t)) != want:
        t = "%.9g" % v
    if "e" in t:
        m, e = t.split("e")
        t = "%sE%s%02d" % (m, "-" if e[0] == "-" else "+", int(e.lstrip("+-")))
    elif "." not in t:
        t += ".0"
    return t


def _json_text(o, ind=0):
    sp = "  "
    if isinstance(o, dict):
        if not o:
            return "{}"
        body = ",\r\n".join('%s%s: %s' % (sp * (ind + 1), json.dumps(k, ensure_ascii=False), _json_text(v, ind + 1))
                            for k, v in o.items())
        return "{\r\n" + body + "\r\n" + sp * ind + "}"
    if isinstance(o, list):
        if not o:
            return "[]"
        body = ",\r\n".join(sp * (ind + 1) + _json_text(v, ind + 1) for v in o)
        return "[\r\n" + body + "\r\n" + sp * ind + "]"
    if isinstance(o, bool):
        return "true" if o else "false"
    if isinstance(o, int):
        return str(o)
    if isinstance(o, float):
        return _f32_text(o)
    if o is None:
        return "null"
    return json.dumps(o, ensure_ascii=False)


def controls_load(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def controls_text(data):
    return _json_text(data)


def controls_save(data, path, backup_dir=None):
    """Write like the game does. If the file exists and backup_dir is given, keep a timestamped copy first."""
    import os
    import shutil
    import time
    if backup_dir and os.path.exists(path):
        os.makedirs(backup_dir, exist_ok=True)
        base = os.path.basename(path)
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        dst = os.path.join(backup_dir, "%s.%s.bak" % (base, stamp))
        n = 1
        while os.path.exists(dst):
            n += 1
            dst = os.path.join(backup_dir, "%s.%s_%d.bak" % (base, stamp, n))
        shutil.copy2(path, dst)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(controls_text(data))


def controls_new_file():
    return {"Version": 2, "CurrentSceneId": "", "Scenes": []}


def controls_new_scene(name, end_time=0.0):
    import uuid
    return {"Id": uuid.uuid4().hex, "Name": name, "Data": {
        "Version": 2, "Properties": [], "Tracks": [],
        "CameraPath": {"Algorithm": 1, "Transform": []},
        "Clones": [], "Lights": [],
        "HasExportRange": False, "ExportStart": 0.0, "ExportEnd": float(end_time)}}


def controls_find_scene(data, name=None, scene_id=None):
    for sc in data.get("Scenes", []):
        if scene_id and sc.get("Id") == scene_id:
            return sc
    for sc in data.get("Scenes", []):
        if name is not None and sc.get("Name") == name:
            return sc
    return None


def controls_current_scene(data):
    return controls_find_scene(data, scene_id=data.get("CurrentSceneId")) or (data.get("Scenes") or [None])[0]


# ---------------------------------------------------------------- Blender add-on

try:
    import bpy
    from bpy.props import (StringProperty, FloatProperty, BoolProperty, IntProperty,
                           EnumProperty, PointerProperty)
    from bpy_extras.io_utils import ImportHelper, ExportHelper
    import mathutils
    HAVE_BPY = True
except ImportError:
    HAVE_BPY = False


def _sample(track, t):
    """Linear sample of [[t, v...], ...] at time t."""
    if t <= track[0][0]:
        return track[0][1:]
    for a, b in zip(track, track[1:]):
        if t <= b[0]:
            u = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
            return [p + (q - p) * u for p, q in zip(a[1:], b[1:])]
    return track[-1][1:]


if HAVE_BPY:

    # ------------------------------------------------------------ helpers

    def _scene_fps(scene):
        return scene.render.fps / scene.render.fps_base

    def _iter_fcurves(action):
        """F-curves of an action in Blender 3.x .. 5.x (layered / slotted actions)."""
        layers = getattr(action, "layers", None)
        if layers is not None and len(layers):
            for layer in layers:
                for strip in layer.strips:
                    for bag in getattr(strip, "channelbags", []):
                        yield from bag.fcurves
        elif hasattr(action, "fcurves"):
            yield from action.fcurves

    def _scene_actions(scene):
        seen = {}
        for ob in scene.objects:
            for idb in (ob, ob.data):
                ad = getattr(idb, "animation_data", None) if idb else None
                if ad and ad.action:
                    seen[ad.action.name] = ad.action
        return list(seen.values())

    def _is_camera(self, obj):
        return obj.type == "CAMERA"

    # ------------------------------------------------------------ properties

    class RustShotCamera(bpy.types.PropertyGroup):
        """Per-camera settings (stored in the .blend)."""
        demo: StringProperty(name="Demo", description="Demo path as the game writes it, e.g. demos/mydemo.dem")
        folder: StringProperty(name="Folder", description="Shot folder in the game (may be empty)")
        start: FloatProperty(name="Start in demo", unit="TIME_ABSOLUTE", min=0.0,
                             description="Demo time (seconds) where the shot begins")
        dof_blur: FloatProperty(name="DOF blur", default=16.0, min=0.0,
                                description="graphics.dof_blur (animatable)")
        raw: StringProperty(options={"HIDDEN"})
        # demo controls (.demo-controls.json)
        controls_file: StringProperty(name="Controls file", subtype="FILE_PATH",
                                      description="demos/controls/<demo>.demo-controls.json in the game folder")
        scene_name: StringProperty(name="Scene", description="Scene name inside the controls file "
                                                              "(an existing scene with this name is replaced)")
        scene_id: StringProperty(options={"HIDDEN"})
        path_algorithm: IntProperty(name="Path algorithm", default=1, options={"HIDDEN"})
        path_focal: FloatProperty(name="Path focal", default=50.0, options={"HIDDEN"})
        controls: StringProperty(options={"HIDDEN"}, description="JSON list of control ids on this camera")

    class RustShotExport(bpy.types.PropertyGroup):
        """Scene-wide export settings."""
        camera_mode: EnumProperty(
            name="Camera",
            items=[("ACTIVE", "Active / selected", "The active object if it is a camera, otherwise the scene camera"),
                   ("SCENE", "Scene camera", "The camera set in Scene properties"),
                   ("PICK", "Pick from list", "Choose a camera below"),
                   ("SELECTED", "All selected cameras", "One .shot per selected camera, named after the camera")],
            default="ACTIVE")
        camera: PointerProperty(name="Camera object", type=bpy.types.Object, poll=_is_camera)

        frame_range: EnumProperty(
            name="Range",
            items=[("SCENE", "Scene range", "Scene start/end frame"),
                   ("PREVIEW", "Preview range", "Timeline preview range"),
                   ("ACTION", "Camera keys", "From first to last keyframe of the camera"),
                   ("CUSTOM", "Custom", "Frames below")],
            default="SCENE")
        frame_start: IntProperty(name="Start", default=1)
        frame_end: IntProperty(name="End", default=250)

        sample_rate: FloatProperty(name="Samples / sec", default=60.0, min=1.0, max=1000.0,
                                   description="How many keys per second are written (the game itself records ~45). "
                                               "Independent from the scene FPS")
        time_base: EnumProperty(
            name="Time base",
            items=[("SCENE", "Scene FPS", "frame / scene FPS = seconds, exactly like Blender playback"),
                   ("CUSTOM", "Custom FPS", "Treat the animation as authored at the FPS below")],
            default="SCENE")
        custom_fps: FloatProperty(name="FPS", default=60.0, min=1.0)
        speed: FloatProperty(name="Speed", default=1.0, min=0.01, max=100.0,
                             description="Playback speed in the game: 2 = twice as fast, 0.5 = slow motion")

        fov_mode: EnumProperty(
            name="FOV",
            items=[("CAMERA", "From camera", "Vertical FOV of the camera (animatable)"),
                   ("CONSTANT", "Constant", "Write a fixed FOV for the whole shot")],
            default="CAMERA")
        fov_value: FloatProperty(name="FOV value", default=90.0, min=1.0, max=179.0)

        dof_mode: EnumProperty(
            name="DOF",
            items=[("AUTO", "If enabled on camera", "Write DOF tracks when Depth of Field is on"),
                   ("ON", "Always", "Always write DOF tracks"),
                   ("OFF", "Never", "Do not write DOF tracks")],
            default="AUTO")

        directory: StringProperty(name="Shots folder", subtype="DIR_PATH",
                                  description="Where the quick-export button writes .shot files")
        file_name: StringProperty(name="File name", description="Empty = camera name")

        authored_fps: FloatProperty(options={"HIDDEN"}, default=0.0)

        fmt: EnumProperty(
            name="Format",
            items=[("CONTROLS", "Demo controls (.json)", "demos/controls/<demo>.demo-controls.json (current Rust)"),
                   ("SHOT", "Legacy shot (.shot)", "Old debug-camera shot files")],
            default="CONTROLS")
        path_rate: FloatProperty(name="Path keys / sec", default=10.0, min=0.5, max=120.0,
                                 description="Camera path keys per second. The game smooths the path between keys, "
                                             "so a few per second is enough")
        track_mode: EnumProperty(
            name="Tracks",
            items=[("KEYS", "Keyframes", "Write your keyframes as they are (editable in game). "
                                         "Interpolation becomes the key's tangent mode"),
                   ("BAKE", "Bake", "Sample animated values at the rate below: exact curve shape in game")],
            default="KEYS")
        track_rate: FloatProperty(name="Bake keys / sec", default=10.0, min=0.5, max=120.0)
        set_current: BoolProperty(name="Make it the active scene", default=True,
                                  description="Set CurrentSceneId in the file to the exported scene")
        backup: BoolProperty(name="Backup before overwrite", default=True,
                             description="Copy the existing file into a 'backup' folder next to it first")

    # ------------------------------------------------------------ export core

    def get_export_cameras(context, s):
        scene = context.scene
        if s.camera_mode == "SELECTED":
            cams = [o for o in context.selected_objects if o.type == "CAMERA"]
        elif s.camera_mode == "PICK":
            cams = [s.camera] if s.camera else []
        elif s.camera_mode == "SCENE":
            cams = [scene.camera] if scene.camera else []
        else:
            ob = context.active_object
            cams = [ob] if ob and ob.type == "CAMERA" else ([scene.camera] if scene.camera else [])
        return cams

    def get_frame_range(scene, s, obj):
        if s.frame_range == "PREVIEW" and scene.use_preview_range:
            return scene.frame_preview_start, scene.frame_preview_end
        if s.frame_range == "ACTION" and obj.animation_data and obj.animation_data.action:
            a, b = obj.animation_data.action.frame_range
            return a, b
        if s.frame_range == "CUSTOM":
            return s.frame_start, s.frame_end
        return scene.frame_start, scene.frame_end

    def get_fps(scene, s):
        return s.custom_fps if s.time_base == "CUSTOM" else _scene_fps(scene)

    def shot_timing(scene, s, obj):
        """(frame_start, frame_end, blender_fps, game_duration_seconds, n_samples)"""
        f0, f1 = get_frame_range(scene, s, obj)
        fps = get_fps(scene, s)
        dur = (f1 - f0) / fps / s.speed
        n = int(math.floor(dur * s.sample_rate + 1e-6)) + 1
        return f0, f1, fps, dur, n

    def export_camera(context, obj, path, s, name=None):
        import os
        scene = context.scene
        rs = obj.rust_shot
        # cameras imported by v0.1/0.2 kept settings in plain custom properties
        for old, new in (("rust_demo", "demo"), ("rust_folder", "folder"), ("rust_raw", "raw")):
            if old in obj and not getattr(rs, new):
                setattr(rs, new, obj[old])
        if "rust_start" in obj and not rs.start:
            rs.start = float(obj["rust_start"])
        raw = {}
        if rs.raw:
            try:
                raw = json.loads(rs.raw)
            except Exception:
                raw = {}
        name = name or os.path.splitext(os.path.basename(path))[0]
        write_dof = s.dof_mode == "ON" or (s.dof_mode == "AUTO" and obj.data.dof.use_dof)
        shot = {"name": name, "demo": rs.demo, "folder": rs.folder, "start": rs.start,
                "position": [], "rotation": [], "fov": [],
                "dof_dist": [], "dof_aper": [], "dof_blur": [], "_raw": raw}

        f0, f1, fps, dur, n = shot_timing(scene, s, obj)
        cur = scene.frame_current
        prev = None
        for i in range(n):
            t = i / s.sample_rate
            frame = f0 + t * s.speed * fps
            if i == n - 1 and abs(frame - f1) < 1e-3:
                frame = f1
            fi = int(math.floor(frame))
            scene.frame_set(fi, subframe=frame - fi)

            mw = obj.matrix_world
            p, q = blender_to_unity(tuple(mw.to_translation()), [list(r) for r in mw.to_3x3().normalized()])
            if prev is not None and sum(a * b for a, b in zip(prev, q)) < 0:
                q = tuple(-c for c in q)
            prev = q
            shot["position"].append([t] + list(p))
            shot["rotation"].append([t] + list(q))

            cd = obj.data
            if s.fov_mode == "CONSTANT":
                fov = s.fov_value
            elif cd.sensor_fit == "VERTICAL":
                fov = math.degrees(cd.angle_y)
            else:
                r = scene.render
                aspect = (r.resolution_x * r.pixel_aspect_x) / (r.resolution_y * r.pixel_aspect_y)
                fov = math.degrees(2 * math.atan(math.tan(cd.angle_x / 2) / aspect))
            shot["fov"].append([t, fov])

            if write_dof:
                dof = cd.dof
                if dof.focus_object is not None:
                    fwd = -mw.col[2].xyz.normalized()
                    dist = (dof.focus_object.matrix_world.translation - mw.translation).dot(fwd)
                else:
                    dist = dof.focus_distance
                shot["dof_dist"].append([t, dist])
                shot["dof_aper"].append([t, dof.aperture_fstop])
                shot["dof_blur"].append([t, rs.dof_blur])
        scene.frame_set(cur)
        shot["duration"] = (n - 1) / s.sample_rate + 1.0 / s.sample_rate
        save(shot, path)
        return dur, n

    def run_export(op, context, base_path=None):
        """Export according to scene settings. base_path: file path from the file dialog (or None = quick export)."""
        import os
        s = context.scene.rust_shot_export
        cams = get_export_cameras(context, s)
        if not cams:
            op.report({"ERROR"}, "No camera to export (check the Camera setting)")
            return {"CANCELLED"}
        if base_path:
            folder = os.path.dirname(base_path)
            single_name = os.path.splitext(os.path.basename(base_path))[0]
        else:
            folder = bpy.path.abspath(s.directory) if s.directory else ""
            if not folder:
                op.report({"ERROR"}, "Set the Shots folder first")
                return {"CANCELLED"}
            single_name = s.file_name
        os.makedirs(folder, exist_ok=True)
        done = []
        for cam in cams:
            name = single_name if (len(cams) == 1 and single_name) else bpy.path.clean_name(cam.name)
            path = os.path.join(folder, name + ".shot")
            dur, n = export_camera(context, cam, path, s, name)
            done.append("%s (%.2fs, %d keys)" % (name, dur, n))
        op.report({"INFO"}, "Exported: " + ", ".join(done))
        return {"FINISHED"}

    # ------------------------------------------------------------ import core

    def import_shot(context, path, set_fps=0):
        shot = load(path)
        scene = context.scene
        if set_fps:
            scene.render.fps = int(set_fps)
            scene.render.fps_base = 1.0
        fps = _scene_fps(scene)
        scene.rust_shot_export.authored_fps = fps

        cam_data = bpy.data.cameras.new(shot["name"] or "RustShot")
        cam_data.sensor_fit = "VERTICAL"
        cam_data.clip_end = 5000.0
        cam = bpy.data.objects.new(shot["name"] or "RustShot", cam_data)
        context.collection.objects.link(cam)
        cam.rotation_mode = "QUATERNION"
        rs = cam.rust_shot
        rs.demo = shot["demo"]
        rs.folder = shot["folder"]
        rs.start = float(shot["start"])
        rs.raw = json.dumps(shot["_raw"])
        has_dof = bool(shot["dof_dist"])
        cam_data.dof.use_dof = has_dof

        times = [i / fps for i in range(int(math.floor(shot["duration"] * fps)) + 1)]
        prev_q = None
        for t in times:
            frame = scene.frame_start + t * fps
            loc, m = unity_to_blender(_sample(shot["position"], t), _sample(shot["rotation"], t))
            q = mathutils.Matrix(m).to_quaternion()
            if prev_q is not None and prev_q.dot(q) < 0:
                q.negate()
            prev_q = q
            cam.location = loc
            cam.rotation_quaternion = q
            cam_data.angle_y = math.radians(_sample(shot["fov"], t)[0])
            cam.keyframe_insert("location", frame=frame)
            cam.keyframe_insert("rotation_quaternion", frame=frame)
            cam_data.keyframe_insert("lens", frame=frame)
            if has_dof:
                cam_data.dof.focus_distance = _sample(shot["dof_dist"], t)[0]
                cam_data.dof.aperture_fstop = max(0.1, _sample(shot["dof_aper"], t)[0])
                rs.dof_blur = _sample(shot["dof_blur"], t)[0]
                cam_data.dof.keyframe_insert("focus_distance", frame=frame)
                cam_data.dof.keyframe_insert("aperture_fstop", frame=frame)
                cam.keyframe_insert("rust_shot.dof_blur", frame=frame)
        scene.frame_end = max(scene.frame_end, int(math.ceil(scene.frame_start + times[-1] * fps)))
        scene.camera = cam
        for o in context.selected_objects:
            o.select_set(False)
        cam.select_set(True)
        context.view_layer.objects.active = cam
        return cam

    def rescale_fps(scene, from_fps, to_fps, set_scene=True):
        """Stretch all keys of scene objects so timing in seconds stays the same."""
        k = to_fps / from_fps
        f0 = scene.frame_start

        def m(x):
            return f0 + (x - f0) * k

        for act in _scene_actions(scene):
            for fc in _iter_fcurves(act):
                for kp in fc.keyframe_points:
                    kp.co.x = m(kp.co.x)
                    kp.handle_left.x = m(kp.handle_left.x)
                    kp.handle_right.x = m(kp.handle_right.x)
                fc.update()
        scene.frame_end = int(round(m(scene.frame_end)))
        if scene.use_preview_range:
            scene.frame_preview_start = int(round(m(scene.frame_preview_start)))
            scene.frame_preview_end = int(round(m(scene.frame_preview_end)))
        for mk in scene.timeline_markers:
            mk.frame = int(round(m(mk.frame)))
        if set_scene:
            if abs(to_fps - round(to_fps)) < 1e-6:
                scene.render.fps = int(round(to_fps))
                scene.render.fps_base = 1.0
            else:  # e.g. 29.97
                scene.render.fps = int(math.ceil(to_fps))
                scene.render.fps_base = scene.render.fps / to_fps
        scene.rust_shot_export.authored_fps = to_fps

    # ------------------------------------------------------------ demo controls: Blender side

    # controls that live on the Blender camera itself; everything else is a custom property on the object
    NATIVE_CONTROLS = {
        "camera.fov": "lens",
        "camera.focusdistance": "dof.focus_distance",
        "camera.aperture": "dof.aperture_fstop",
        "dof.enabled": "dof.use_dof",
    }
    STEP_KINDS = ("bool",)  # always constant (on/off)

    def _vertical_fov(scene, cd):
        if cd.sensor_fit == "VERTICAL":
            return math.degrees(cd.angle_y)
        r = scene.render
        aspect = (r.resolution_x * r.pixel_aspect_x) / (r.resolution_y * r.pixel_aspect_y)
        return math.degrees(2 * math.atan(math.tan(cd.angle_x / 2) / aspect))

    def _native_get(scene, obj, cid):
        cd = obj.data
        if cid == "camera.fov":
            return _vertical_fov(scene, cd)
        if cid == "camera.focusdistance":
            if cd.dof.focus_object is not None:
                mw = obj.matrix_world
                fwd = -mw.col[2].xyz.normalized()
                return (cd.dof.focus_object.matrix_world.translation - mw.translation).dot(fwd)
            return cd.dof.focus_distance
        if cid == "camera.aperture":
            return cd.dof.aperture_fstop
        if cid == "dof.enabled":
            return 1.0 if cd.dof.use_dof else 0.0

    def _native_set_key(obj, cid, value, frame=None):
        cd = obj.data
        if cid == "camera.fov":
            cd.sensor_fit = "VERTICAL"
            cd.angle_y = math.radians(max(1.0, min(179.0, value)))
            idb, path = cd, "lens"
        elif cid == "camera.focusdistance":
            cd.dof.focus_distance = max(0.0, value)
            idb, path = cd, "dof.focus_distance"
        elif cid == "camera.aperture":
            cd.dof.aperture_fstop = max(0.1, value)
            idb, path = cd, "dof.aperture_fstop"
        else:  # dof.enabled
            cd.dof.use_dof = value >= 0.5
            idb, path = cd, "dof.use_dof"
        if frame is not None:
            idb.keyframe_insert(path, frame=frame)
        return idb, path

    def _custom_path(cid):
        return '["%s"]' % cid

    def _ensure_custom(obj, cid, value=None):
        label, default, lo, hi, kind = CONTROL_INFO.get(cid, (cid, 0.0, -1e6, 1e6, "float"))
        if cid not in obj.keys():
            obj[cid] = float(default if value is None else value)
            try:
                ui = obj.id_properties_ui(cid)
                ui.update(min=-1e9, max=1e9, soft_min=lo, soft_max=hi, description=label,
                          step=100 if kind in ("int", "weather", "bool") else 10)
            except Exception:
                pass
        elif value is not None:
            obj[cid] = float(value)

    def _control_ids(obj):
        try:
            return list(json.loads(obj.rust_shot.controls or "[]"))
        except Exception:
            return []

    def _set_control_ids(obj, ids):
        obj.rust_shot.controls = json.dumps(sorted(set(ids)))

    def _fcurve(idb, path):
        ad = getattr(idb, "animation_data", None)
        if not ad or not ad.action:
            return None
        for fc in _iter_fcurves(ad.action):
            if fc.data_path == path:
                return fc
        return None

    def _control_fcurve(obj, cid):
        if cid in NATIVE_CONTROLS:
            return _fcurve(obj.data, NATIVE_CONTROLS[cid])
        return _fcurve(obj, _custom_path(cid))

    def _apply_easing(fc, easings, kind):
        """Set Blender interpolation/handles from game Easing values (one per key, same order)."""
        pts = sorted(fc.keyframe_points, key=lambda k: k.co.x)
        for i, kp in enumerate(pts):
            e = easings[i] if i < len(easings) else 0
            mode = EASING_NAMES[e] if 0 <= e < len(EASING_NAMES) else "SMOOTH"
            if kind in STEP_KINDS or mode == "STEP":
                kp.interpolation = "CONSTANT"
            elif mode == "LINEAR":
                kp.interpolation = "LINEAR"
            else:
                kp.interpolation = "BEZIER"
                kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
        fc.update()
        pts = sorted(fc.keyframe_points, key=lambda k: k.co.x)  # update() reallocates: old refs are stale
        for i, kp in enumerate(pts):  # FLAT / BROKEN need final auto positions first
            e = easings[i] if i < len(easings) else 0
            mode = EASING_NAMES[e] if 0 <= e < len(EASING_NAMES) else "SMOOTH"
            if kp.interpolation != "BEZIER" or mode not in ("FLAT", "BROKEN"):
                continue
            kp.handle_left_type = kp.handle_right_type = "ALIGNED" if mode == "FLAT" else "FREE"
            if mode == "FLAT":
                x, y = kp.co
                dl = (x - pts[i - 1].co.x) / 3 if i > 0 else 1.0
                dr = (pts[i + 1].co.x - x) / 3 if i + 1 < len(pts) else 1.0
                kp.handle_left = (x - dl, y)
                kp.handle_right = (x + dr, y)
        fc.update()

    def _easing_of(kp):
        if kp.interpolation == "CONSTANT":
            return EASING_NAMES.index("STEP")
        if kp.interpolation == "LINEAR":
            return EASING_NAMES.index("LINEAR")
        if kp.interpolation == "BEZIER":
            types = {kp.handle_left_type, kp.handle_right_type}
            if types <= {"AUTO", "AUTO_CLAMPED"}:
                return EASING_NAMES.index("SMOOTH")
            if types == {"VECTOR"}:
                return EASING_NAMES.index("LINEAR")
            if "FREE" in types or "VECTOR" in types:  # independent tangents
                return EASING_NAMES.index("BROKEN")
            y = kp.co.y  # ALIGNED
            if abs(kp.handle_left.y - y) < 1e-6 and abs(kp.handle_right.y - y) < 1e-6:
                return EASING_NAMES.index("FLAT")
            return EASING_NAMES.index("SMOOTH")
        return EASING_NAMES.index("SMOOTH")  # elastic, bounce, ... -> use Bake for exact shape

    def import_controls(context, path, which="CURRENT", set_fps=0):
        """Create one animated camera per scene of a .demo-controls.json file."""
        import os
        data = controls_load(path)
        scenes_ = data.get("Scenes") or []
        if which == "CURRENT":
            cur = controls_current_scene(data)
            scenes_ = [cur] if cur else []
        if not scenes_:
            raise RuntimeError("No scenes in this file")
        scene = context.scene
        if set_fps:
            scene.render.fps = int(set_fps)
            scene.render.fps_base = 1.0
        fps = _scene_fps(scene)
        scene.rust_shot_export.authored_fps = fps
        scene.rust_shot_export.fmt = "CONTROLS"
        f0 = scene.frame_start
        cams = []
        last_t = 0.0
        for sc in scenes_:
            d = sc.get("Data", {})
            name = sc.get("Name") or "Rust scene"
            cam_data = bpy.data.cameras.new(name)
            cam_data.sensor_fit = "VERTICAL"
            cam_data.clip_end = 5000.0
            cam = bpy.data.objects.new(name, cam_data)
            context.collection.objects.link(cam)
            cam.rotation_mode = "QUATERNION"
            rs = cam.rust_shot
            rs.controls_file = path
            rs.scene_name = name
            rs.scene_id = sc.get("Id", "")
            rs.demo = "demos/" + os.path.basename(path).replace(".demo-controls.json", ".dem")
            rs.start = 0.0
            cp = d.get("CameraPath") or {}
            rs.path_algorithm = int(cp.get("Algorithm", 1))

            # camera path
            prev_q = None
            for k in cp.get("Transform") or []:
                t = float(k.get("Time", 0.0))
                last_t = max(last_t, t)
                loc, m = unity_to_blender((k.get("Px", 0.0), k.get("Py", 0.0), k.get("Pz", 0.0)),
                                          (k.get("Qx", 0.0), k.get("Qy", 0.0), k.get("Qz", 0.0), k.get("Qw", 1.0)))
                q = mathutils.Matrix(m).to_quaternion()
                if prev_q is not None and prev_q.dot(q) < 0:
                    q.negate()
                prev_q = q
                cam.location = loc
                cam.rotation_quaternion = q
                cam.keyframe_insert("location", frame=f0 + t * fps)
                cam.keyframe_insert("rotation_quaternion", frame=f0 + t * fps)
                rs.path_focal = float(k.get("Focal", rs.path_focal))

            # base values
            ids = []
            for p in d.get("Properties") or []:
                cid, v = p.get("Id"), float(p.get("Value", 0.0))
                if cid in NATIVE_CONTROLS:
                    _native_set_key(cam, cid, v)
                else:
                    _ensure_custom(cam, cid, v)
                ids.append(cid)

            # animated tracks
            for tr in d.get("Tracks") or []:
                cid = tr.get("Id")
                keys = sorted(tr.get("Keyframes") or [], key=lambda k: k.get("Time", 0.0))
                if not cid or not keys:
                    continue
                kind = CONTROL_INFO.get(cid, ("", 0, 0, 0, "float"))[4]
                for k in keys:
                    t = float(k.get("Time", 0.0))
                    last_t = max(last_t, t)
                    v = float(k.get("Value", 0.0))
                    if cid in NATIVE_CONTROLS:
                        _native_set_key(cam, cid, v, frame=f0 + t * fps)
                    else:
                        _ensure_custom(cam, cid, v)
                        cam.keyframe_insert(_custom_path(cid), frame=f0 + t * fps)
                fc = _control_fcurve(cam, cid)
                if fc:
                    _apply_easing(fc, [int(k.get("Easing", 0)) for k in keys], kind)
                ids.append(cid)
            _set_control_ids(cam, [i for i in ids if i not in NATIVE_CONTROLS])
            cams.append(cam)

        scene.frame_end = max(scene.frame_end, int(math.ceil(f0 + last_t * fps)))
        scene.camera = cams[0]
        for o in context.selected_objects:
            o.select_set(False)
        for c in cams:
            c.select_set(True)
        context.view_layer.objects.active = cams[0]
        return cams

    def _track_keys(context, obj, cid, fc, mode, rate, speed, fps, f0, f1, t_of):
        """Keyframes of one control for the game: [(time, value, easing)]."""
        scene = context.scene
        native = cid in NATIVE_CONTROLS
        kind = CONTROL_INFO.get(cid, ("", 0, 0, 0, "float"))[4]

        def value_at(frame):
            fi = int(math.floor(frame))
            scene.frame_set(fi, subframe=frame - fi)
            v = _native_get(scene, obj, cid) if native else float(obj.get(cid, 0.0))
            return float(v)

        pts = sorted(fc.keyframe_points, key=lambda k: k.co.x) if fc else []
        out = []
        steps = []  # (frame_of_next_key, frame_of_this_key) for constant segments
        for i, kp in enumerate(pts):
            if (kp.interpolation == "CONSTANT" or kind in STEP_KINDS) and i + 1 < len(pts):
                steps.append((pts[i + 1].co.x, kp.co.x))
        if mode == "KEYS" and pts:
            for kp in pts:
                fr = min(max(kp.co.x, f0), f1)
                e = EASING_NAMES.index("STEP") if kind in STEP_KINDS else _easing_of(kp)
                out.append((t_of(fr), value_at(fr), e))
        else:
            n = int(math.floor((f1 - f0) / fps / speed * rate + 1e-6)) + 1
            frames = {f0 + i / rate * speed * fps for i in range(n)} | {f1}
            frames |= {kp.co.x for kp in pts if f0 <= kp.co.x <= f1}
            for fr in sorted(frames):
                out.append((t_of(fr), value_at(fr), EASING_NAMES.index("LINEAR")))
        # constant segments: hold the old value until just before the jump (survives game smoothing)
        eps = 0.001
        for nxt, this in steps:
            if not (f0 < nxt <= f1):
                continue
            t_jump = t_of(nxt)
            v_old = value_at(max(this, f0))
            out = [k for k in out if not (t_jump - eps * 1.5 < k[0] < t_jump)]
            out.append((t_jump - eps, v_old, EASING_NAMES.index("STEP")))
        out.sort(key=lambda k: k[0])
        return [{"Time": float(t), "Value": float(v), "Easing": int(e)} for t, v, e in out]

    def export_controls(context, obj, path, s, scene_name=None):
        import os
        scene = context.scene
        rs = obj.rust_shot
        f0, f1, fps, dur, _ = shot_timing(scene, s, obj)

        def t_of(frame):
            return rs.start + (frame - f0) / fps / s.speed

        cur = scene.frame_current
        # camera path
        n = int(math.floor(dur * s.path_rate + 1e-6)) + 1
        frames = [f0 + i / s.path_rate * s.speed * fps for i in range(n)]
        if f1 - frames[-1] > 1e-3:
            frames.append(f1)
        transform = []
        prev = None
        for fr in frames:
            fi = int(math.floor(fr))
            scene.frame_set(fi, subframe=fr - fi)
            mw = obj.matrix_world
            p, q = blender_to_unity(tuple(mw.to_translation()), [list(r) for r in mw.to_3x3().normalized()])
            if prev is not None and sum(a * b for a, b in zip(prev, q)) < 0:
                q = tuple(-c for c in q)
            prev = q
            transform.append({"Time": float(t_of(fr)), "Px": p[0], "Py": p[1], "Pz": p[2],
                              "Qx": q[0], "Qy": q[1], "Qz": q[2], "Qw": q[3], "Focal": float(rs.path_focal)})

        # controls on this camera
        ids = set(_control_ids(obj)) | {k for k in obj.keys() if k in CONTROL_INFO} | set(NATIVE_CONTROLS)
        tracks = {}
        for cid in sorted(ids):
            fc = _control_fcurve(obj, cid)
            mode = s.track_mode
            if cid == "camera.focusdistance" and obj.data.dof.focus_object is not None:
                fc, mode = None, "BAKE"  # follows an object: has to be sampled
            elif fc is None or len(fc.keyframe_points) < 2:
                continue
            tracks[cid] = _track_keys(context, obj, cid, fc, mode, s.track_rate, s.speed, fps, f0, f1, t_of)

        scene.frame_set(int(f0))
        props = {}
        for cid in ids:
            if cid in tracks:
                continue  # animated: the game keeps its own base value, filled in below
            if cid in NATIVE_CONTROLS:
                props[cid] = float(_native_get(scene, obj, cid))
            elif cid in obj.keys():
                props[cid] = float(obj[cid])
        scene.frame_set(cur)

        # merge into the file
        data = controls_load(path) if os.path.exists(path) else controls_new_file()
        name = scene_name or rs.scene_name or obj.name
        sc = controls_find_scene(data, name=name, scene_id=rs.scene_id)
        if sc is None:
            sc = controls_new_scene(name, end_time=transform[-1]["Time"] if transform else 0.0)
            data.setdefault("Scenes", []).append(sc)
        sc["Name"] = name
        dd = sc.setdefault("Data", {})
        dd.setdefault("Version", 2)
        old_props = {p.get("Id"): p.get("Value") for p in dd.get("Properties") or []}
        old_props.update(props)
        for cid, keys in tracks.items():  # new animated control: base = its last key
            old_props.setdefault(cid, keys[-1]["Value"] if keys else 0.0)
        dd["Properties"] = [{"Id": k, "Value": float(old_props[k])} for k in sorted(old_props)]
        order = [t.get("Id") for t in dd.get("Tracks") or [] if t.get("Id") in tracks]
        order += [k for k in sorted(tracks) if k not in order]
        dd["Tracks"] = [{"Id": k, "Keyframes": tracks[k]} for k in order]
        dd["CameraPath"] = {"Algorithm": int(rs.path_algorithm), "Transform": transform}
        for k, v in (("Clones", []), ("Lights", []), ("HasExportRange", False), ("ExportStart", 0.0)):
            dd.setdefault(k, v)
        dd.setdefault("ExportEnd", float(transform[-1]["Time"]) if transform else 0.0)
        # keep Json.NET's key order: Version, Properties, Tracks, CameraPath, Clones, Lights, ...
        key_order = ["Version", "Properties", "Tracks", "CameraPath", "Clones", "Lights",
                     "HasExportRange", "ExportStart", "ExportEnd"]
        sc["Data"] = {k: dd[k] for k in key_order if k in dd} | {k: v for k, v in dd.items() if k not in key_order}
        if s.set_current or not data.get("CurrentSceneId"):
            data["CurrentSceneId"] = sc["Id"]
        backup_dir = os.path.join(os.path.dirname(path), "backup") if s.backup else None
        controls_save(data, path, backup_dir)
        rs.scene_id = sc["Id"]
        rs.scene_name = name
        return dur, len(transform), len(tracks)

    def run_export_controls(op, context, base_path=None):
        import os
        s = context.scene.rust_shot_export
        cams = get_export_cameras(context, s)
        if not cams:
            op.report({"ERROR"}, "No camera to export (check the Camera setting)")
            return {"CANCELLED"}
        done = []
        for cam in cams:
            path = base_path or bpy.path.abspath(cam.rust_shot.controls_file)
            if not path:
                op.report({"ERROR"}, "%s: set the Controls file "
                                     "(demos/controls/<demo>.demo-controls.json)" % cam.name)
                return {"CANCELLED"}
            if not os.path.isdir(os.path.dirname(path) or "."):
                op.report({"ERROR"}, "Folder does not exist: %s" % os.path.dirname(path))
                return {"CANCELLED"}
            if base_path:
                cam.rust_shot.controls_file = path
            dur, n_path, n_tr = export_controls(context, cam, path, s)
            done.append("%s (%.2fs, %d path keys, %d tracks)" % (cam.rust_shot.scene_name, dur, n_path, n_tr))
        op.report({"INFO"}, "Exported: " + ", ".join(done))
        return {"FINISHED"}

    # ------------------------------------------------------------ operators

    def _draw_export_settings(layout, context, compact=False):
        s = context.scene.rust_shot_export
        scene = context.scene
        controls = s.fmt == "CONTROLS"
        if not compact:
            layout.prop(s, "fmt", text="")
        col = layout.column(align=True)
        col.prop(s, "camera_mode")
        if s.camera_mode == "PICK":
            col.prop(s, "camera", text="")
        cams = get_export_cameras(context, s)
        col.label(text="→ " + (", ".join(c.name for c in cams) if cams else "no camera!"),
                  icon="CAMERA_DATA" if cams else "ERROR")

        col = layout.column(align=True)
        col.prop(s, "frame_range")
        if s.frame_range == "CUSTOM":
            row = col.row(align=True)
            row.prop(s, "frame_start")
            row.prop(s, "frame_end")

        col = layout.column(align=True)
        col.prop(s, "time_base")
        if s.time_base == "CUSTOM":
            col.prop(s, "custom_fps")
        col.prop(s, "speed")
        if controls:
            col.prop(s, "path_rate")
            col.prop(s, "track_mode")
            if s.track_mode == "BAKE":
                col.prop(s, "track_rate")
        else:
            col.prop(s, "sample_rate")
        if cams:
            f0, f1, fps, dur, n = shot_timing(scene, s, cams[0])
            box = layout.box()
            box.label(text="Frames %d–%d @ %.3g fps" % (f0, f1, fps))
            if controls:
                t0 = cams[0].rust_shot.start
                box.label(text="Demo time %.2f – %.2f s, %d path keys"
                               % (t0, t0 + dur, int(dur * s.path_rate) + 1), icon="TIME")
            else:
                box.label(text="In game: %.2f s, %d keys" % (dur, n), icon="TIME")

        col = layout.column(align=True)
        if controls:
            col.prop(s, "set_current")
            col.prop(s, "backup")
        else:
            col.prop(s, "fov_mode")
            if s.fov_mode == "CONSTANT":
                col.prop(s, "fov_value")
            col.prop(s, "dof_mode")

    def _draw_controls(layout, obj):
        """Per-camera demo controls: native camera settings + custom animatable properties."""
        cd = obj.data
        rs = obj.rust_shot
        col = layout.column(align=True)
        col.prop(rs, "controls_file", text="File")
        col.prop(rs, "scene_name")
        col.prop(rs, "start", text="Demo time at start")
        ids = [i for i in _control_ids(obj) if i in obj.keys()]
        ids += [k for k in obj.keys() if k in CONTROL_INFO and k not in ids]
        for prefix, title in CONTROL_GROUPS:
            box = layout.box()
            box.label(text=title)
            c = box.column(align=True)
            if prefix == "camera":
                c.prop(cd, "angle", text="FOV")
                c.prop(cd.dof, "focus_object")
                c.prop(cd.dof, "focus_distance", text="Focus distance")
                c.prop(cd.dof, "aperture_fstop", text="Aperture")
            if prefix == "dof":
                c.prop(cd.dof, "use_dof", text="DOF enabled")
            for cid in sorted(i for i in ids if i.split(".")[0] == prefix and i not in NATIVE_CONTROLS):
                label, _, _, _, kind = CONTROL_INFO.get(cid, (cid, 0, 0, 0, "float"))
                row = c.row(align=True)
                row.prop(obj, _custom_path(cid), text=label)
                if kind == "weather":
                    w = int(round(float(obj.get(cid, 0.0))))
                    row.label(text=WEATHER_NAMES[w] if 0 <= w < len(WEATHER_NAMES) else "?")
                op = row.operator(RUSTSHOT_OT_remove_control.bl_idname, text="", icon="X")
                op.cid = cid
        other = sorted(i for i in ids if i.split(".")[0] not in dict(CONTROL_GROUPS) and i not in NATIVE_CONTROLS)
        if other:
            box = layout.box()
            box.label(text="Other")
            for cid in other:
                box.prop(obj, _custom_path(cid), text=cid)
        layout.menu(RUSTSHOT_MT_add_control.bl_idname, icon="ADD")
        layout.label(text="Hover a value and press I to keyframe it", icon="INFO")

    class IMPORT_OT_rust_shot(bpy.types.Operator, ImportHelper):
        """Import a Rust demo camera shot as an animated camera"""
        bl_idname = "import_scene.rust_shot"
        bl_label = "Import Rust shot"
        bl_options = {"REGISTER", "UNDO"}
        filename_ext = ".shot"
        filter_glob: StringProperty(default="*.shot", options={"HIDDEN"})
        set_fps: EnumProperty(
            name="Scene FPS",
            items=[("0", "Keep current", ""), ("24", "24", ""), ("30", "30", ""),
                   ("50", "50", ""), ("60", "60", ""), ("120", "120", "")],
            default="60",
            description="Keys are placed so real-time speed is preserved at this FPS")

        def execute(self, context):
            import_shot(context, self.filepath, int(self.set_fps))
            return {"FINISHED"}

    class EXPORT_OT_rust_shot(bpy.types.Operator, ExportHelper):
        """Export camera animation to a Rust demo shot"""
        bl_idname = "export_scene.rust_shot"
        bl_label = "Export Rust shot"
        filename_ext = ".shot"
        filter_glob: StringProperty(default="*.shot", options={"HIDDEN"})

        def invoke(self, context, event):
            cams = get_export_cameras(context, context.scene.rust_shot_export)
            if cams:
                self.filepath = bpy.path.clean_name(cams[0].name) + ".shot"
            return ExportHelper.invoke(self, context, event)

        def draw(self, context):
            _draw_export_settings(self.layout, context)

        def execute(self, context):
            return run_export(self, context, self.filepath)

    class RUSTSHOT_OT_quick_export(bpy.types.Operator):
        """Export to the Shots folder using the settings in the panel"""
        bl_idname = "rust_shot.quick_export"
        bl_label = "Export to Shots folder"

        def execute(self, context):
            if context.scene.rust_shot_export.fmt == "CONTROLS":
                return run_export_controls(self, context, None)
            return run_export(self, context, None)

    class RUSTSHOT_OT_change_fps(bpy.types.Operator):
        """Change scene FPS and stretch all keys so the animation keeps its real-time speed"""
        bl_idname = "rust_shot.change_fps"
        bl_label = "Change FPS (keep timing)"
        bl_options = {"REGISTER", "UNDO"}
        from_fps: FloatProperty(name="Animation made at", default=24.0, min=1.0)
        to_fps: FloatProperty(name="New FPS", default=60.0, min=1.0)

        def invoke(self, context, event):
            s = context.scene.rust_shot_export
            cur = _scene_fps(context.scene)
            if s.authored_fps and abs(s.authored_fps - cur) > 1e-3:
                self.from_fps, self.to_fps = s.authored_fps, cur  # repair after a plain FPS change
            else:
                self.from_fps = cur
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            rescale_fps(context.scene, self.from_fps, self.to_fps)
            self.report({"INFO"}, "Keys rescaled %.3g → %.3g fps" % (self.from_fps, self.to_fps))
            return {"FINISHED"}

    class RUSTSHOT_OT_mark_fps(bpy.types.Operator):
        """Accept the current scene FPS as the one the animation is made for"""
        bl_idname = "rust_shot.mark_fps"
        bl_label = "Animation is fine at current FPS"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            context.scene.rust_shot_export.authored_fps = _scene_fps(context.scene)
            return {"FINISHED"}

    class IMPORT_OT_rust_controls(bpy.types.Operator, ImportHelper):
        """Import scenes of a Rust demo controls file (demos/controls/*.demo-controls.json) as animated cameras"""
        bl_idname = "import_scene.rust_controls"
        bl_label = "Import Rust demo controls"
        bl_options = {"REGISTER", "UNDO"}
        filename_ext = ".json"
        filter_glob: StringProperty(default="*.demo-controls.json;*.json", options={"HIDDEN"})
        which: EnumProperty(
            name="Scenes",
            items=[("CURRENT", "Active scene", "Only the scene that is active in the file"),
                   ("ALL", "All scenes", "One camera per scene")],
            default="CURRENT")
        set_fps: EnumProperty(
            name="Scene FPS",
            items=[("0", "Keep current", ""), ("24", "24", ""), ("30", "30", ""),
                   ("50", "50", ""), ("60", "60", ""), ("120", "120", "")],
            default="60",
            description="Keys are placed so real-time speed is preserved at this FPS")

        def execute(self, context):
            try:
                cams = import_controls(context, self.filepath, self.which, int(self.set_fps))
            except (RuntimeError, ValueError, KeyError) as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            self.report({"INFO"}, "Imported: " + ", ".join(c.name for c in cams))
            return {"FINISHED"}

    class EXPORT_OT_rust_controls(bpy.types.Operator, ExportHelper):
        """Write the camera animation into a Rust demo controls file (other scenes in the file are kept)"""
        bl_idname = "export_scene.rust_controls"
        bl_label = "Export Rust demo controls"
        filename_ext = ".json"
        check_extension = False
        filter_glob: StringProperty(default="*.demo-controls.json;*.json", options={"HIDDEN"})

        def invoke(self, context, event):
            cams = get_export_cameras(context, context.scene.rust_shot_export)
            if cams and cams[0].rust_shot.controls_file:
                self.filepath = bpy.path.abspath(cams[0].rust_shot.controls_file)
            else:
                self.filepath = "mydemo.demo-controls.json"
            return ExportHelper.invoke(self, context, event)

        def draw(self, context):
            _draw_export_settings(self.layout, context, compact=True)

        def execute(self, context):
            return run_export_controls(self, context, self.filepath)

    class RUSTSHOT_OT_add_control(bpy.types.Operator):
        """Add an animatable demo control to the active camera"""
        bl_idname = "rust_shot.add_control"
        bl_label = "Add control"
        bl_options = {"REGISTER", "UNDO"}
        cid: StringProperty()

        def execute(self, context):
            ob = context.active_object
            if not ob or ob.type != "CAMERA" or not self.cid:
                return {"CANCELLED"}
            _ensure_custom(ob, self.cid)
            _set_control_ids(ob, _control_ids(ob) + [self.cid])
            return {"FINISHED"}

    class RUSTSHOT_OT_remove_control(bpy.types.Operator):
        """Remove this control (and its animation) from the camera"""
        bl_idname = "rust_shot.remove_control"
        bl_label = "Remove control"
        bl_options = {"REGISTER", "UNDO"}
        cid: StringProperty()

        def execute(self, context):
            ob = context.active_object
            if not ob or self.cid not in ob.keys():
                return {"CANCELLED"}
            fc = _fcurve(ob, _custom_path(self.cid))
            if fc:
                try:
                    ob.keyframe_delete(_custom_path(self.cid))
                except Exception:
                    pass
                fc = _fcurve(ob, _custom_path(self.cid))
                if fc:
                    for kp in list(fc.keyframe_points)[::-1]:
                        fc.keyframe_points.remove(kp)
            del ob[self.cid]
            _set_control_ids(ob, [i for i in _control_ids(ob) if i != self.cid])
            return {"FINISHED"}

    class RUSTSHOT_MT_add_control(bpy.types.Menu):
        bl_idname = "RUSTSHOT_MT_add_control"
        bl_label = "Add control"

        def draw(self, context):
            ob = context.active_object
            have = set(ob.keys()) if ob else set()
            for prefix, title in CONTROL_GROUPS:
                self.layout.label(text=title)
                for cid, info in CONTROL_INFO.items():
                    if cid.split(".")[0] == prefix and cid not in NATIVE_CONTROLS and cid not in have:
                        self.layout.operator(RUSTSHOT_OT_add_control.bl_idname, text=info[0]).cid = cid

    # ------------------------------------------------------------ UI panel (N sidebar > Rust Shot)

    class VIEW3D_PT_rust_shot(bpy.types.Panel):
        bl_label = "Rust Shot"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Rust Shot"

        def draw(self, context):
            layout = self.layout
            scene = context.scene
            s = scene.rust_shot_export

            cur = _scene_fps(scene)
            if s.authored_fps and abs(s.authored_fps - cur) > 1e-3:
                box = layout.box()
                box.alert = True
                box.label(text="FPS changed %.3g → %.3g" % (s.authored_fps, cur), icon="ERROR")
                box.label(text="Game playback: %.2fx speed" % (cur / s.authored_fps))
                box.operator(RUSTSHOT_OT_change_fps.bl_idname, text="Rescale keys (keep speed)")
                box.operator(RUSTSHOT_OT_mark_fps.bl_idname)

            row = layout.row(align=True)
            row.operator(IMPORT_OT_rust_controls.bl_idname, text="Import controls", icon="IMPORT")
            row.operator(IMPORT_OT_rust_shot.bl_idname, text="Import .shot", icon="IMPORT")
            layout.prop(s, "fmt", text="Format")

            ob = context.active_object
            if ob and ob.type == "CAMERA":
                box = layout.box()
                box.label(text=ob.name, icon="CAMERA_DATA")
                rs = ob.rust_shot
                if s.fmt == "CONTROLS":
                    _draw_controls(box, ob)
                else:
                    box.prop(rs, "demo")
                    box.prop(rs, "folder")
                    box.prop(rs, "start")
                    if ob.data.dof.use_dof:
                        box.prop(ob.data.dof, "focus_object")
                        box.prop(ob.data.dof, "focus_distance")
                        box.prop(ob.data.dof, "aperture_fstop", text="DOF aper")
                        box.prop(rs, "dof_blur")
                    else:
                        box.prop(ob.data.dof, "use_dof", text="Depth of Field")

            layout.separator()
            layout.label(text="Export", icon="EXPORT")
            _draw_export_settings(layout, context, compact=True)
            col = layout.column(align=True)
            if s.fmt == "CONTROLS":
                col.operator(RUSTSHOT_OT_quick_export.bl_idname, text="Export to controls file", icon="EXPORT")
            else:
                col.prop(s, "directory")
                col.prop(s, "file_name")
                col.operator(RUSTSHOT_OT_quick_export.bl_idname, icon="EXPORT")
            layout.operator(RUSTSHOT_OT_change_fps.bl_idname, icon="TIME")

    def _menu_import(self, context):
        self.layout.operator(IMPORT_OT_rust_controls.bl_idname, text="Rust demo controls (.json)")
        self.layout.operator(IMPORT_OT_rust_shot.bl_idname, text="Rust camera shot (.shot)")

    def _menu_export(self, context):
        self.layout.operator(EXPORT_OT_rust_controls.bl_idname, text="Rust demo controls (.json)")
        self.layout.operator(EXPORT_OT_rust_shot.bl_idname, text="Rust camera shot (.shot)")

    _classes = (RustShotCamera, RustShotExport, IMPORT_OT_rust_shot, EXPORT_OT_rust_shot,
                IMPORT_OT_rust_controls, EXPORT_OT_rust_controls,
                RUSTSHOT_OT_quick_export, RUSTSHOT_OT_change_fps, RUSTSHOT_OT_mark_fps,
                RUSTSHOT_OT_add_control, RUSTSHOT_OT_remove_control, RUSTSHOT_MT_add_control,
                VIEW3D_PT_rust_shot)

    def register():
        for c in _classes:
            bpy.utils.register_class(c)
        bpy.types.Object.rust_shot = PointerProperty(type=RustShotCamera)
        bpy.types.Scene.rust_shot_export = PointerProperty(type=RustShotExport)
        bpy.types.TOPBAR_MT_file_import.append(_menu_import)
        bpy.types.TOPBAR_MT_file_export.append(_menu_export)

    def unregister():
        bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
        bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
        del bpy.types.Scene.rust_shot_export
        del bpy.types.Object.rust_shot
        for c in reversed(_classes):
            bpy.utils.unregister_class(c)


# ---------------------------------------------------------------- CLI

def _main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "decode":
        shot = load(argv[2])
        txt = json.dumps(shot, indent=1)
        if len(argv) > 3:
            open(argv[3], "w").write(txt)
        else:
            print(txt)
    elif cmd == "encode":
        shot = json.load(open(argv[2]))
        save(shot, argv[3])
    elif cmd == "roundtrip":
        for p in argv[2:]:
            data = open(p, "rb").read()
            again = encode(json.loads(json.dumps(decode(data))))
            print(("OK   " if again == data else "DIFF ") + p, len(data), len(again))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("decode", "encode", "roundtrip"):
        sys.exit(_main(sys.argv))
    elif HAVE_BPY:
        register()  # run from Blender's Text Editor
    else:
        print(__doc__)
