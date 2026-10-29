"""
Rust demo camera shot (.shot) codec + Blender import/export add-on.

Format (reverse-engineered, protobuf):
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
  3D View > N sidebar > "Rust Shot" tab: import, per-camera settings (demo, start, folder, DOF),
  export settings, quick export to a shots folder, FPS tools.
  File > Import / Export > Rust camera shot (.shot) also work (same settings).
  Timing: seconds = (frame - range start) / FPS / speed. If the scene FPS is changed after
  animating, the panel warns and "Rescale keys" restores the original real-time speed.
  DOF: focus distance <- dof.focus_distance (or distance to dof.focus_object),
       aper <- dof.aperture_fstop, blur <- camera "DOF blur" (animatable).
"""

import json
import math
import struct
import sys

bl_info = {
    "name": "Rust camera shot (.shot)",
    "author": "haos9cr",
    "version": (0, 3, 1),
    "blender": (3, 0, 0),
    "location": "File > Import/Export",
    "description": "Import/export Rust demo debug-camera shots",
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

    # ------------------------------------------------------------ operators

    def _draw_export_settings(layout, context, compact=False):
        s = context.scene.rust_shot_export
        scene = context.scene
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
        col.prop(s, "sample_rate")
        if cams:
            f0, f1, fps, dur, n = shot_timing(scene, s, cams[0])
            box = layout.box()
            box.label(text="Frames %d–%d @ %.3g fps" % (f0, f1, fps))
            box.label(text="In game: %.2f s, %d keys" % (dur, n), icon="TIME")

        col = layout.column(align=True)
        col.prop(s, "fov_mode")
        if s.fov_mode == "CONSTANT":
            col.prop(s, "fov_value")
        col.prop(s, "dof_mode")

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

            layout.operator(IMPORT_OT_rust_shot.bl_idname, icon="IMPORT")

            ob = context.active_object
            if ob and ob.type == "CAMERA":
                box = layout.box()
                box.label(text=ob.name, icon="CAMERA_DATA")
                rs = ob.rust_shot
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
            _draw_export_settings(layout, context)
            col = layout.column(align=True)
            col.prop(s, "directory")
            col.prop(s, "file_name")
            col.operator(RUSTSHOT_OT_quick_export.bl_idname, icon="EXPORT")
            layout.operator(RUSTSHOT_OT_change_fps.bl_idname, icon="TIME")

    def _menu_import(self, context):
        self.layout.operator(IMPORT_OT_rust_shot.bl_idname, text="Rust camera shot (.shot)")

    def _menu_export(self, context):
        self.layout.operator(EXPORT_OT_rust_shot.bl_idname, text="Rust camera shot (.shot)")

    _classes = (RustShotCamera, RustShotExport, IMPORT_OT_rust_shot, EXPORT_OT_rust_shot,
                RUSTSHOT_OT_quick_export, RUSTSHOT_OT_change_fps, RUSTSHOT_OT_mark_fps,
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
