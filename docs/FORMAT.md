# Rust `.shot` file format

Reverse-engineered from shots recorded with the demo debug camera (static, rotating, flying,
mid-demo start, FOV changes, DOF on/off). Every recording tested decodes and re-encodes
byte-for-byte with `rust_shot.py`.

## Encoding

The file is a single [protobuf](https://protobuf.dev/programming-guides/encoding/) message, no header.

- Floats are `fixed32` (wire type 5, little-endian IEEE-754).
- Fields with value `0` are not written (proto3 style). A key with no `time` field is at `t = 0`,
  and a position key with no vector is at the origin.
- The game writes the length prefix of each track (fields 5–7, 9–11) as a varint of **at least 3 bytes**,
  padded with `0x80` continuation bytes. Field 12 uses 5-byte length prefixes. Standard protobuf
  readers accept this. Normal minimal varints are also expected to work, but the encoder mimics the game.

## Top level

Fields are written in this order:

| # | Type | Meaning |
|---|---|---|
| 1 | string | Shot name |
| 2 | string | Demo path, e.g. `demos/test_debug_camera.dem` |
| 3 | float | Start time inside the demo, seconds (absent = 0) |
| 4 | float | Duration, seconds (≈ last key time + one frame) |
| 5 | Track | Position |
| 6 | Track | Rotation |
| 7 | Track | Field of view |
| 9 | Track | DOF focus distance (`graphics.dof_focus_dist`) — only if DOF recording is on |
| 10 | Track | DOF aperture (`graphics.dof_aper`) |
| 11 | Track | DOF blur (`graphics.dof_blur`) |
| 12 | bytes | Unknown. Always `62 88 80 80 80 00 12 82 80 80 80 00 1a 00`, i.e. `{2: {3: {}}}` |
| 8 | string | Folder the shot was saved into (absent if none) |

Field 8 really is written after field 12.

## Track

```
Track    { repeated Key key = 2; }
```

All tracks of a shot share the same key times: one key per game frame (≈45 per second in in-game recordings).

### Position key (track 5)
```
{ 1: float time, 2: Vector3 { 1: x, 2: y, 3: z } }
```

### Rotation key (track 6)
```
{ 1: float time, 2: x, 3: y, 4: z, 5: w }
```
A unit quaternion in Unity convention, with the fields directly in the key (no nested message).
Debug-camera Euler angles (x, y, z) map to it as Unity `Quaternion.Euler`: `q = Y(y) · X(x) · Z(z)`.
Verified with Euler 40/50/60 → `(0.46701, 0.18894, 0.30065, 0.80982)`.

### Scalar key (tracks 7, 9, 10, 11)
```
{ 1: float time, 2: float value }
```
- FOV: vertical, degrees (`debug.debugcamera_fov`; default 90).
- Focus distance is recorded **already smoothed**: after setting `dof_focus_dist 10` it eases to 10 over ~0.5 s.
  When the focus is never set, the first values are uninitialised tiny numbers (~1e-19) decaying to 0.

## Coordinates

Unity world space: X right, Y up, Z forward, left-handed, meters.
The camera looks along local +Z.

## Debug camera save file (for reference)

Saving a debug camera position in game writes JSON like this. It is a different file, but the same quantities:

```json
{ "position": {"x":0,"y":0,"z":0}, "rotation": {"x":0,"y":0,"z":0},
  "zoom": 0.7478992,
  "dofState": {"distance":0.0,"aper":12.0,"blur":16.0,"squeeze":0.0,"barrel":0.0} }
```

`squeeze` and `barrel` are not recorded in shots. How `zoom` maps to FOV degrees is not confirmed.
