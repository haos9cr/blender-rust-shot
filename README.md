# Rust Shot — Blender ⇄ Rust demo camera

[Русская версия](README.ru.md)

Animate camera fly-throughs for **Rust** demos in Blender and play them back in the game.

In game, the demo camera can only be flown by hand (WASD + mouse). This add-on reads and writes the
game's camera files, so you can build smooth, precise camera moves in Blender (paths, constraints,
easing, focus pulls) and export them back to the game.

Two formats are supported:

| Format | Game version | What it holds |
|---|---|---|
| **Demo controls** `demos/controls/<demo>.demo-controls.json` | current (staging, Oct 2026+) | Scenes with a camera path plus animated **time of day, sun, weather, clouds, fog, wind, FOV, focal length, DOF and effects** |
| **Shot** `.shot` (legacy) | older versions | Camera position, rotation, FOV, DOF |

- Import a scene or shot as an animated Blender camera; every game control becomes an animatable value
- Export back into the same controls file; other scenes, clones, lights and settings in it are kept
- The game's files are written exactly as the game writes them (verified byte-for-byte)
- Works without Blender too: a small CLI converts `.shot` ⇄ JSON

> Unofficial fan tool. Not affiliated with or endorsed by Facepunch Studios.
> The file formats were reverse-engineered and may change with game updates.

## Optional: blender-rust-toolkit

[**blender-rust-toolkit**](https://github.com/haos9cr/blender-rust-toolkit) loads a Rust map
and its assets into Blender, so you can see the terrain, monuments and buildings while you animate the camera:
fly through doorways, skim rooftops, frame a base without clipping into rocks.
Rust Shot works fine without it; the toolkit just makes it easier to see where the camera goes.

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

To try it:
1. Install **Rust Shot** and open `advanced_camera_animation.blend`.
2. Press Play, then export the camera with the **Rust Shot** panel.

Loading the map is optional, just for convenience: with [blender-rust-toolkit](https://github.com/haos9cr/blender-rust-toolkit)
you can load the `.map` from `examples/` and see monuments and props around the camera (they come from your own Rust installation).

The demo file itself is not included. The shot lines up only with a demo recorded on this map.

## Install

1. Download `rust_shot.py`.
2. Blender → **Edit → Preferences → Add-ons → Install from Disk…** (older Blender: **Install…**) → pick `rust_shot.py` → enable **Rust camera shot (.shot)**.
3. Open the 3D View sidebar (**N**) → **Rust Shot** tab.

Tested on Blender 5.2. The code targets 3.0+ (including the layered Action API of 4.4+/5.x).

## Usage

### Demo controls (current Rust)

The game keeps one controls file per demo in `RustStaging\demos\controls\` (or `Rust\demos\controls\`).
Each file can hold several scenes.

**Import:** Rust Shot panel → **Import controls** (or **File → Import → Rust demo controls**). Choose the active
scene or all scenes; each scene becomes a camera. With the camera selected, the panel shows:

- **File / Scene** — where it is exported to. An existing scene with this name is replaced, a new name adds a scene.
- **Camera:** FOV, focus distance (or a **Focus Object**), aperture — these are the Blender camera's own settings.
- **Camera / Depth of field / Environment / Effects:** every other game control (focal length, roll, shake,
  DOF blur, time of day, sun rotation, weather, clouds, fog, wind, bloom, motion blur, vignette, ...).
  Hover a value and press **I** to keyframe it. **Add control** adds any control the scene does not have yet.

Times are seconds on the demo timeline: the first frame of the range is demo time **Demo time at start** (0 by default).

**Export:** set **Format: Demo controls**, then **Export to controls file** (or **File → Export → Rust demo controls**).

| Setting | What it does |
|---|---|
| **Path keys / sec** | Camera path keys per second. The game smooths the path between keys, so 5–10 is plenty |
| **Tracks: Keyframes** | Your keyframes go to the game as they are and stay editable there. Blender interpolation becomes the key's tangent mode: Bezier auto = Smooth, aligned horizontal handles = Flat, Linear = Linear, Constant = Step, free handles = Broken |
| **Tracks: Bake** | Samples every animated value at **Bake keys / sec**: exact curve shape, any Blender interpolation |
| **Make it the active scene** | Sets the file's active scene to the exported one |
| **Backup before overwrite** | Copies the existing file into a `backup` folder next to it first |

Constant (step) keys are written with an extra key just before the jump, so steps stay steps even if the game
smooths the curve. On/off controls such as DOF enabled are always exported as steps.
Weather values: 0 Clear, 1 Overcast, 2 Rain, 3 Storm, 4 Snow.

### Legacy shot (.shot)

The settings below (and **Format: Legacy shot**) apply to `.shot` files of older game versions.

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

- Demo controls: staging currently loads every key back as Smooth, even though it saves the tangent mode.
  Use **Tracks: Bake** if the exact curve shape matters. Clones and lights are kept but not edited yet.
- `.shot`: field 12 is not understood. It is always the same small block, so it is copied unchanged.
- The game records the focus distance already smoothed. It is not known whether playback smooths it again.
- Only the debug camera shot format is covered. Loading the map is done by [blender-rust-toolkit](https://github.com/haos9cr/blender-rust-toolkit).

## License

[MIT](LICENSE)
