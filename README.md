# Rust Shot — Blender ⇄ Rust demo camera

[Русская версия](README.ru.md)

Animate camera fly-throughs for **Rust** demos in Blender and play them back in the game.

In-game, the demo debug camera can only be flown by hand (WASD + mouse) and recorded into a
`.shot` file. This add-on reads and writes that file format, so you can build smooth, precise
camera moves in Blender — paths, constraints, easing, focus pulls — and export them back to the game.

- Import any `.shot` as an animated Blender camera (position, rotation, FOV, depth of field)
- Export any camera animation to a `.shot` the game plays back
- Byte-exact re-encode of every in-game recording tested so far
- Works without Blender too: a small CLI converts `.shot` ⇄ JSON

> Unofficial fan tool. Not affiliated with or endorsed by Facepunch Studios.
> The `.shot` format was reverse-engineered and may change with game updates.

## Pairs well with blender-rust-toolkit

Animating blind is hard. [**blender-rust-toolkit**](https://github.com/haos9cr/blender-rust-toolkit) loads a Rust map
and its assets into Blender, so you can see the terrain, monuments and buildings while you animate the camera:
fly through doorways, skim rooftops, frame a base without clipping into rocks.

## Example

[![Rust Shot example: camera fly-through animated in Blender, played back in Rust](https://img.youtube.com/vi/hBKVEP4vYPI/hqdefault.jpg)](https://www.youtube.com/watch?v=hBKVEP4vYPI)

*Click the image to watch the video on YouTube.*

The scene from the video is in [`examples/`](examples):

| File | What it is |
|---|---|
| [`advanced_camera_animation.blend`](examples/advanced_camera_animation.blend) | The animated camera, terrain, roads and the moving entities of the demo. Game models are not included |
| [`proceduralmap.2000.1791947167831.289_20a7932af0530ee6cc900c8d7492d632_2687766392.map`](examples/proceduralmap.2000.1791947167831.289_20a7932af0530ee6cc900c8d7492d632_2687766392.map) | The map the shot was made on |

How the camera is set up: it follows the helper cube **CAMERA_POS** (Copy Location) and looks at the cube **TARGET** (Track To).
You only animate the two cubes, and the camera stays level and always aims at the subject.

To see it with the full map:
1. Install **Rust Shot** and [blender-rust-toolkit](https://github.com/haos9cr/blender-rust-toolkit).
2. Open `advanced_camera_animation.blend`.
3. Load the `.map` from `examples/` with blender-rust-toolkit. Monuments and props come from your own Rust installation.
4. Press Play, then export the camera with the **Rust Shot** panel.

The demo file itself is not included. The shot lines up only with a demo recorded on this map.

## Install

1. Download `rust_shot.py`.
2. Blender → **Edit → Preferences → Add-ons → Install from Disk…** (older Blender: **Install…**) → pick `rust_shot.py` → enable **Rust camera shot (.shot)**.
3. Open the 3D View sidebar (**N**) → **Rust Shot** tab.

Tested on Blender 5.2. The code targets 3.0+ (including the layered Action API of 4.4+/5.x).

## Usage

### Import
**Rust Shot panel → Import Rust shot** (or **File → Import → Rust camera shot**).
Choose the scene FPS in the import options (60 by default). Keys are placed so real-time speed is preserved.

The imported camera remembers its demo path, start time in the demo, shot folder and DOF values.
You can edit them in the panel when the camera is selected.

### Export
Set it up in the panel, then press **Export to Shots folder** (or use **File → Export → Rust camera shot**).

| Setting | What it does |
|---|---|
| **Camera** | Active/selected camera, scene camera, pick from list, or **all selected cameras** (one file each, named after the camera) |
| **Range** | Scene range, preview range, camera's own keyframe range, or custom frames |
| **Time base** | Scene FPS (as Blender plays it) or a custom FPS |
| **Speed** | In-game playback speed: `2` = twice as fast, `0.5` = slow motion |
| **Samples / sec** | Keys written per second (the game itself records ≈45). Independent of the scene FPS; sub-frames are sampled |
| **FOV** | From the camera (animatable) or a constant value |
| **DOF** | Write depth of field if enabled on the camera / always / never |
| **Shots folder**, **File name** | Destination for quick export; empty name = camera name |

The panel shows the resulting **in-game duration and key count** before you export.

Anything that moves the camera works: keyframes, Follow Path, Track To, parenting, drivers.
The final world transform is baked.

### Timing and FPS
Shot time is `(frame − range start) / FPS / speed`. If you change the scene FPS *after* animating,
the keys stay on the same frames and the shot plays faster or slower in game. The add-on remembers the FPS
an animation was made at and warns you when it no longer matches:

- **Rescale keys (keep speed)** stretches all keys, the frame range and markers so the real-time speed stays the same.
- **Animation is fine at current FPS** accepts the new speed.
- **Change FPS (keep timing)** changes the FPS and rescales in one step.

### Depth of field
DOF tracks are written only if **record DOF** was enabled in game when recording (or **DOF = Always** on export).

| Game cvar | Blender |
|---|---|
| `graphics.dof_focus_dist` | Camera → Depth of Field → Focus Distance (or distance to **Focus Object** along the view axis) |
| `graphics.dof_aper` | Camera → Depth of Field → F-Stop |
| `graphics.dof_blur` | **DOF blur** in the Rust Shot panel (animatable) |

## Coordinate system

Rust uses Unity coordinates: X right, Y up, Z forward, left-handed. Blender is Z up, right-handed.

- Position: Blender `(x, y, z)` = Unity `(x, z, y)`. Units are meters in both.
- Rotation: `blender_camera_matrix = S · R_unity · diag(1, 1, −1)`, where `S` swaps Y and Z.
  The extra flip is because a Unity camera looks along +Z and a Blender camera along −Z.
- Debug camera Euler angles are Unity `Quaternion.Euler(x, y, z)` (applied Z, then X, then Y).
- FOV is vertical, in degrees. Imported cameras use a vertical sensor fit.

## Command line (no Blender)

```
python rust_shot.py decode  in.shot  out.json   # inspect / edit as JSON
python rust_shot.py encode  in.json  out.shot
python rust_shot.py roundtrip a.shot b.shot ...  # check byte-exact re-encode
python tests/test_roundtrip.py                   # check every file in samples/
```

The functions are also importable: `rust_shot.load(path)`, `rust_shot.save(shot, path)`,
`rust_shot.decode(bytes)`, `rust_shot.encode(dict)`.

## File format

See [docs/FORMAT.md](docs/FORMAT.md). In short: a protobuf message with name, demo path, start time and duration,
plus keyframe tracks for position, rotation (quaternion), FOV and optional DOF.

## Samples

`samples/test_orbit.shot` is a 10-second orbit around the origin made in Blender with this add-on and
confirmed to play back in game. Record your own shots with the debug camera and drop them into `samples/`;
`tests/test_roundtrip.py` checks every file there.

## Known limitations

- Field 12 of the file is not understood. It is always the same small block, so it is copied unchanged.
- The game records the focus distance already smoothed. It is not known whether playback smooths it again.
- Only the debug camera shot format is covered. Loading the map is done by [blender-rust-toolkit](https://github.com/haos9cr/blender-rust-toolkit).

## License

[MIT](LICENSE)
