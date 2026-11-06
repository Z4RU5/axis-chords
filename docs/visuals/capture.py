"""Captures the raw frames for the README pictures from Blender's real UI.

Run (a window opens behind the others, without focus; real input is ignored):
  blender tests/test_scene.blend --factory-startup --enable-event-simulate --no-window-focus
          --window-geometry 0 0 1600 1000 --python docs/visuals/capture.py

Keys and mouse are simulated, as in tests/run_ui_tests.py. Frames go to
docs/visuals/_render/ with timeline.json (which keys are down in each frame);
compose.py turns them into the pictures.
"""

import json
import os
import sys

import math

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RAW = os.path.join(HERE, "_render")
sys.path.insert(0, ROOT)
import axis_chords  # noqa: E402

axis_chords.register()
os.makedirs(RAW, exist_ok=True)

WIN = AREA = REGION = None
MX = MY = 0
timeline = {"hero": [], "shots": {}}


def ev(kind, value='PRESS', **kw):
    WIN.event_simulate(type=kind, value=value, x=MX, y=MY, **kw)


def tap(kind, **kw):
    ev(kind, 'PRESS', **kw)
    kw.pop('unicode', None)
    ev(kind, 'RELEASE', **kw)


def chord(keys, shift=False):
    for k in keys:
        ev(k, shift=shift)
    for k in keys:
        ev(k, 'RELEASE', shift=shift)


def move_to(x, y):
    global MX, MY
    MX, MY = int(x), int(y)
    WIN.event_simulate(type='MOUSEMOVE', value='NOTHING', x=MX, y=MY)


def home():
    move_to(*start_screen())


def shot(path):
    with bpy.context.temp_override(window=WIN, area=AREA, region=REGION):
        bpy.ops.screen.screenshot_area(filepath=path)


def reset():
    ob = bpy.data.objects["Cube"]
    ob.location = (0, 0, 0)
    ob.rotation_euler = (0, 0, 0)
    for o in bpy.context.view_layer.objects:
        o.select_set(o == ob)
    bpy.context.view_layer.objects.active = ob


# Camera direction per picture: each diagonal is shown from where it reads best.
VIEWS = {
    "xy": ((0.22, 0.62, -0.75), (1.0, 1.0, 0.0), 12.0),
    "xz": ((0.18, 1.0, -0.32), (1.2, 0.0, 1.0), 12.5),
    "yz": ((-1.0, 0.18, -0.32), (0.0, 1.2, 1.0), 12.5),
    "xyz": ((-0.55, 0.7, -0.5), (1.0, 1.0, 1.0), 13.0),
}


def set_view(name):
    fwd, target, dist = VIEWS[name]
    r3d = AREA.spaces.active.region_3d
    r3d.view_perspective = 'PERSP'
    r3d.view_rotation = Vector(fwd).normalized().to_track_quat('-Z', 'Y')
    r3d.view_location = target
    r3d.view_distance = dist


def screen_dir(vec):
    """Screen direction (unit 2D) of a world direction at the origin, in this view."""
    r3d = AREA.spaces.active.region_3d
    a = view3d_utils.location_3d_to_region_2d(REGION, r3d, Vector((0, 0, 0)))
    b = view3d_utils.location_3d_to_region_2d(REGION, r3d, Vector(vec))
    d = b - a
    return d.normalized(), a


def start_screen():
    """Window position of the cube's centre (the mouse starts there, as a person would)."""
    r3d = AREA.spaces.active.region_3d
    p = view3d_utils.location_3d_to_region_2d(REGION, r3d, Vector((0, 0, 0)))
    return REGION.x + p.x, REGION.y + p.y


def setup_scene():
    for name in ("Camera", "Light"):
        ob = bpy.data.objects.get(name)
        if ob:
            bpy.data.objects.remove(ob)
    # A wire ghost where the cube starts, so each picture shows where it came from.
    cube = bpy.data.objects["Cube"]
    ghost = bpy.data.objects.new("Start", cube.data)
    bpy.context.collection.objects.link(ghost)
    ghost.display_type = 'WIRE'
    ghost.hide_select = True
    ghost.color = (0.85, 0.86, 0.9, 1.0)
    space = AREA.spaces.active
    space.show_region_toolbar = False
    space.show_region_ui = False
    space.show_region_tool_header = False
    space.show_gizmo_navigate = False
    space.overlay.show_text = False
    space.overlay.show_stats = False
    space.shading.type = 'SOLID'
    space.shading.color_type = 'SINGLE'
    space.shading.single_color = (0.72, 0.74, 0.8)
    space.shading.show_cavity = True
    space.shading.cavity_type = 'WORLD'
    space.shading.wireframe_color_type = 'OBJECT'
    space.lens = 40


def frames():
    global WIN, AREA, REGION
    WIN = bpy.context.window_manager.windows[0]
    AREA = next(a for a in WIN.screen.areas if a.type == 'VIEW_3D')
    REGION = next(r for r in AREA.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=WIN, area=AREA, region=REGION):
        bpy.ops.screen.screen_full_area()
    yield 0.5
    AREA = next(a for a in WIN.screen.areas if a.type == 'VIEW_3D')
    REGION = next(r for r in AREA.regions if r.type == 'WINDOW')
    bpy.context.preferences.view.smooth_view = 0
    setup_scene()
    set_view("xy")
    reset()
    yield 0.2
    home()
    yield 0.5

    # --- Hero animation -------------------------------------------------
    n = [0]

    def grab(keys, caption):
        path = os.path.join(RAW, f"hero_{n[0]:03d}.png")
        shot(path)
        timeline["hero"].append({"file": os.path.basename(path), "keys": keys, "caption": caption})
        n[0] += 1

    sx, sy = start_screen()
    d1, _o = screen_dir((1, 1, 0))
    d2, _o = screen_dir((1, -1, 0))
    for _ in range(8):
        grab([], "")
        yield 0.05
    tap('G')
    yield 0.12
    for i in range(10):
        move_to(sx + 9 * i, sy + 5 * i + 0.4 * i * i)
        yield 0.06
        grab(["G"], "move")
    chord(['X', 'Y'])
    yield 0.3
    for i in range(34):
        r = 230 * math.sin(i / 33 * math.pi * 1.5)
        move_to(sx + d1.x * r + 25, sy + d1.y * r - 25)
        yield 0.06
        grab(["X", "Y"], "along the X+Y diagonal")
    chord(['X', 'Y'])
    yield 0.3
    for i in range(30):
        r = 200 * math.sin(i / 29 * math.pi * 1.25)
        move_to(sx + d2.x * r, sy + d2.y * r)
        yield 0.06
        grab(["X", "Y"], "again: the other diagonal")
    tap('RET')
    yield 0.2
    for _ in range(14):
        grab(["Enter"], "done")
        yield 0.05

    # --- Stills -----------------------------------------------------------
    def still(name, keys, view, vec, dist, shift=False, wait=0.3):
        set_view(view)
        reset()
        yield 0.15
        home()
        yield 0.15
        d, _o = screen_dir(vec)
        mouse = (d.x * dist, d.y * dist)
        tap('G')
        yield 0.12
        if shift:
            ev('LEFT_SHIFT')
        chord(keys, shift=shift)
        if shift:
            ev('LEFT_SHIFT', 'RELEASE')
        yield wait
        sx, sy = start_screen()
        move_to(sx + mouse[0], sy + mouse[1])
        yield 0.25
        shot(os.path.join(RAW, f"{name}.png"))
        loc = tuple(round(v, 3) for v in bpy.data.objects["Cube"].location)
        timeline["shots"][name] = {"keys": keys, "shift": shift, "loc": loc}
        tap('ESC')
        yield 0.2

    yield from still("chord_xy", ['X', 'Y'], "xy", (1, 1, 0), 240)
    yield from still("chord_xz", ['X', 'Z'], "xz", (1, 0, 1), 240)
    yield from still("chord_yz", ['Y', 'Z'], "yz", (0, 1, 1), 240)
    yield from still("chord_xyz", ['X', 'Y', 'Z'], "xyz", (1, 1, 1), 260)
    yield from still("plane_xy", ['X', 'Y'], "xy", (1, -1, 0), 200, shift=True)

    # The same keys again: first diagonal, the other one, free.
    set_view("xy")
    reset()
    yield 0.15
    home()
    yield 0.15
    tap('G')
    yield 0.12
    for step, name in enumerate(("cycle_1", "cycle_2", "cycle_3")):
        chord(['X', 'Y'])
        yield 0.35
        sx, sy = start_screen()
        move_to(sx + 190, sy + 60)
        yield 0.25
        shot(os.path.join(RAW, f"{name}.png"))
        timeline["shots"][name] = {"loc": tuple(round(v, 3) for v in bpy.data.objects["Cube"].location)}
        home()
        yield 0.15
    tap('ESC')
    yield 0.2

    # Typed numbers: G, X+Y, 2 (header shows it), Enter.
    set_view("xy")
    reset()
    yield 0.15
    home()
    yield 0.15
    tap('G')
    yield 0.12
    chord(['X', 'Y'])
    yield 0.35
    tap('TWO', unicode='2')
    yield 0.25
    shot(os.path.join(RAW, "typed_1.png"))
    tap('RET')
    yield 0.25
    shot(os.path.join(RAW, "typed_2.png"))
    timeline["shots"]["typed"] = {"loc": tuple(round(v, 4) for v in bpy.data.objects["Cube"].location)}

    timeline["region"] = [REGION.width, REGION.height]
    yield 0.1


def main():
    gen = frames()

    def tick():
        try:
            return next(gen)
        except StopIteration:
            pass
        except Exception:  # noqa: BLE001
            import traceback
            timeline["error"] = traceback.format_exc()
        with open(os.path.join(RAW, "timeline.json"), "w", encoding="utf-8") as f:
            json.dump(timeline, f, indent=1)
        os._exit(0)

    bpy.app.timers.register(tick, first_interval=1.5)


main()
