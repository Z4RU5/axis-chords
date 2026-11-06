"""Interactive tests for Axis Chords, with simulated keys and mouse.

Run (opens a window behind the others, without focus; real input is ignored):
  blender tests/test_scene.blend --factory-startup --enable-event-simulate --no-window-focus
          --window-geometry 100 100 1280 800 --python tests/run_ui_tests.py -- OUT.json [SHOTS_DIR]

tests/test_scene.blend is made by tests/make_scene.py (Blender's default cube scene).
Each case resets the cube, presses keys as a person would, and checks where it ended.
"""

import json
import math
import os
import sys
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import axis_chords  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else os.path.join(HERE, "_ui_results.json")
SHOTS = argv[1] if len(argv) > 1 else None

axis_chords.register()

results = []
info = {}
WIN = AREA = REGION = None
MX = MY = 0          # current simulated mouse position (window pixels)


def ev(kind, value='PRESS', **kw):
    WIN.event_simulate(type=kind, value=value, x=MX, y=MY, **kw)


def tap(kind, **kw):
    ev(kind, 'PRESS', **kw)
    kw.pop('unicode', None)
    ev(kind, 'RELEASE', **kw)


def move(dx, dy):
    global MX, MY
    MX += dx
    MY += dy
    WIN.event_simulate(type='MOUSEMOVE', value='NOTHING', x=MX, y=MY)


def home():
    global MX, MY
    MX = REGION.x + REGION.width // 2
    MY = REGION.y + REGION.height // 2
    WIN.event_simulate(type='MOUSEMOVE', value='NOTHING', x=MX, y=MY)


def running():
    return [op.bl_idname for op in WIN.modal_operators]


def cube():
    return bpy.data.objects["Cube"]


def ctx():
    return bpy.context.temp_override(window=WIN, area=AREA, region=REGION)


def reset_object_mode():
    ob = cube()
    with ctx():
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in bpy.context.view_layer.objects:
            o.select_set(o == ob)
    bpy.context.view_layer.objects.active = ob
    ob.location = (0, 0, 0)
    ob.rotation_euler = (0, 0, 0)
    bpy.context.scene.transform_orientation_slots[0].type = 'GLOBAL'


def shot(name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    try:
        with ctx():
            bpy.ops.screen.screenshot_area(filepath=os.path.join(SHOTS, name + ".png"))
    except Exception as exc:  # noqa: BLE001
        info.setdefault("shot_errors", []).append(f"{name}: {exc}")


def record(name, ok, **data):
    results.append({"case": name, "ok": bool(ok), **data})


def close(a, b, tol=1e-4):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


H2 = math.sqrt(0.5)          # one unit along a two-axis diagonal, per axis
H3 = 1 / math.sqrt(3)        # one unit along the cube diagonal, per axis


def loc():
    return tuple(round(v, 5) for v in cube().location)


# ---------------------------------------------------------------------------

def cases():
    global WIN, AREA, REGION
    WIN = bpy.context.window_manager.windows[0]
    AREA = next(a for a in WIN.screen.areas if a.type == 'VIEW_3D')
    REGION = next(r for r in AREA.regions if r.type == 'WINDOW')
    bpy.context.preferences.view.smooth_view = 0
    with ctx():
        bpy.ops.view3d.view_axis(type='TOP')
    AREA.spaces.active.region_3d.view_perspective = 'ORTHO'
    AREA.spaces.active.overlay.show_stats = False

    kc = bpy.context.window_manager.keyconfigs.user
    info["modal_items"] = [
        (k.propvalue, k.type, k.value, k.shift, k.ctrl, k.alt, k.any)
        for k in kc.keymaps[axis_chords.MODAL_MAP].keymap_items]
    info["g_items"] = [
        (km.name, k.idname, k.type, k.value, k.shift, k.ctrl, k.alt)
        for km in kc.keymaps for k in km.keymap_items
        if k.type == 'G' and k.value == 'PRESS' and not (k.ctrl or k.alt or k.shift)
        and 'translate' in k.idname or 'axis_chord' in k.idname]
    slot = bpy.context.scene.transform_orientation_slots[1]
    info["slot1"] = {p.identifier: str(getattr(slot, p.identifier))
                     for p in slot.bl_rna.properties if p.identifier != 'rna_type'}
    yield 0.3

    # 1. Plain G X: untouched native behaviour.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    move(100, 30)
    yield 0.05
    tap('X')
    yield 0.1
    r = running()
    tap('LEFTMOUSE')
    yield 0.15
    L = loc()
    record("native G X", L[0] > 0.1 and abs(L[1]) < 1e-5 and abs(L[2]) < 1e-5,
           loc=L, running_during=r, last_op=bpy.context.window_manager.operators[-1].bl_idname)

    # 2. G, move, X+Y held together, move on, click: equal X and Y.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    move(60, 40)
    yield 0.05
    ev('X')
    yield 0.03
    ev('Y')
    yield 0.03
    ev('Y', 'RELEASE')
    ev('X', 'RELEASE')
    yield 0.3
    r = running()
    move(60, 60)
    yield 0.15
    shot("chord_xy")
    L_mid = loc()
    tap('LEFTMOUSE')
    yield 0.15
    L = loc()
    lo = bpy.context.window_manager.operators[-1]
    record("G X+Y mouse", L[0] > 0.1 and abs(L[0] - L[1]) < 1e-4 and abs(L[2]) < 1e-5,
           loc=L, mid=L_mid, running_during=r, last_op=lo.bl_idname,
           last_props={k: str(getattr(lo.properties, k)) for k in ('value', 'constraint_axis', 'orient_type')})

    # 3. G X+Y 1 Enter: one unit on each axis.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X')
    ev('Y')
    ev('X', 'RELEASE')
    ev('Y', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    yield 0.05
    shot("chord_xy_typed")
    tap('RET')
    yield 0.15
    record("G X+Y 1 Enter", close(loc(), (H2, H2, 0)), loc=loc())

    # 4. Same chord twice: the other diagonal.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.4
    ev('Y'); ev('X'); ev('Y', 'RELEASE'); ev('X', 'RELEASE')
    yield 0.4
    tap('ONE', unicode='1')
    yield 0.05
    tap('RET')
    yield 0.15
    record("chord twice = other diagonal", close(loc(), (H2, -H2, 0)), loc=loc())

    # 5. X+Y+Z.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('Z')
    ev('X', 'RELEASE'); ev('Y', 'RELEASE'); ev('Z', 'RELEASE')
    yield 0.4
    tap('TWO', unicode='2')
    tap('RET')
    yield 0.15
    record("X+Y+Z 2 Enter", close(loc(), (2 * H3, 2 * H3, 2 * H3)), loc=loc())

    # 6. Escape after a chord: back where it was, nothing left running.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    move(80, 10)
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    move(40, 40)
    yield 0.05
    tap('ESC')
    yield 0.2
    record("Esc after chord", close(loc(), (0, 0, 0)) and not running()
           and axis_chords._session is None and not axis_chords._cancel_item_live(),
           loc=loc(), running=running())

    # 7. A lone Z after a chord: back to a plain Z constraint.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    tap('Z')
    yield 0.4
    tap('THREE', unicode='3')
    tap('RET')
    yield 0.15
    record("chord then Z", close(loc(), (0, 0, 3)), loc=loc())

    # 8. Shift+X+Y: everything but the diagonal (the other diagonal and Z).
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('LEFT_SHIFT')
    ev('X', shift=True); ev('Y', shift=True)
    ev('X', 'RELEASE', shift=True); ev('Y', 'RELEASE', shift=True)
    ev('LEFT_SHIFT', 'RELEASE')
    yield 0.3
    move(100, 0)
    yield 0.1
    tap('LEFTMOUSE')
    yield 0.15
    L = loc()
    record("Shift X+Y plane", abs(L[0] + L[1]) < 1e-4 and abs(L[0]) > 0.05, loc=L)

    # 9. Undo after a chord move.
    reset_object_mode()
    with ctx():
        bpy.ops.ed.undo_push(message="test start")
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.2
    before = loc()
    ev('LEFT_CTRL')
    tap('Z', ctrl=True)
    ev('LEFT_CTRL', 'RELEASE')
    yield 0.3
    record("undo restores", close(before, (H2, H2, 0)) and close(loc(), (0, 0, 0)), before=before, after=loc())

    # 10. Local orientation on a turned cube.
    reset_object_mode()
    cube().rotation_euler = (0, 0, math.radians(45))
    bpy.context.scene.transform_orientation_slots[0].type = 'LOCAL'
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    record("Local orientation", close(loc(), (0, 1, 0)), loc=loc())

    # 11. R during G turns chords off for that transform.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    tap('R')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    r = running()
    tap('ESC')
    yield 0.2
    record("R during G: no chord", close(loc(), (0, 0, 0)) and not running(),
           running_during=r, loc=loc(), rot=tuple(cube().rotation_euler))

    # 11b. The mouse picks the diagonal: moving right-down before X+Y means +X -Y.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    move(80, -70)
    yield 0.05
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    move(20, -20)
    yield 0.1
    tap('LEFTMOUSE')
    yield 0.15
    L = loc()
    record("mouse picks +X-Y", L[0] > 0.1 and abs(L[0] + L[1]) < 1e-4, loc=L)

    # 11c. Keys held through the restart, released later, then a number.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('Y')
    yield 0.04
    ev('X')
    yield 0.15
    ev('X', 'RELEASE')
    yield 0.03
    ev('Y', 'RELEASE')
    yield 0.1
    tap('TWO', unicode='2')
    tap('RET')
    yield 0.15
    record("keys held, then 2", close(loc(), (2 * H2, 2 * H2, 0)), loc=loc())

    # 11d. Ctrl snapping along the diagonal lands on whole steps along the diagonal.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    ev('LEFT_CTRL')
    yield 0.05
    move(97, 83)
    yield 0.1
    tap('LEFTMOUSE', ctrl=True)
    ev('LEFT_CTRL', 'RELEASE')
    yield 0.15
    L = loc()
    steps = L[0] / H2
    record("Ctrl snap whole steps", steps >= 1 and abs(steps - round(steps)) < 1e-3 and close((L[0],), (L[1],)), loc=L)

    # 11e. After a chord, X then X again behaves like Blender's own (Global, then Local).
    reset_object_mode()
    cube().rotation_euler = (0, 0, math.radians(45))
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    tap('X')
    yield 0.3
    tap('X')
    yield 0.1
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    h = math.sqrt(0.5)
    record("chord, X, X = local X", close(loc(), (h, h, 0)), loc=loc())

    # 11f. Perspective view from an angle.
    reset_object_mode()
    r3d = AREA.spaces.active.region_3d
    r3d.view_perspective = 'PERSP'
    r3d.view_rotation = (0.82, 0.42, 0.18, 0.35)
    r3d.view_distance = 12
    yield 0.1
    home()
    yield 0.1
    tap('G')
    yield 0.1
    move(70, 30)
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    move(40, 50)
    yield 0.1
    shot("chord_perspective")
    tap('LEFTMOUSE')
    yield 0.15
    L = loc()
    record("perspective X+Y", abs(L[0]) > 0.05 and abs(L[0] - L[1]) < 1e-4 and abs(L[2]) < 1e-5, loc=L)
    with ctx():
        bpy.ops.view3d.view_axis(type='TOP')
    r3d.view_perspective = 'ORTHO'

    # 11f2. Looking along the diagonal in perspective, mouse back near the start: no jump.
    # (1.1.0 used a 1.41-long axis; Blender's "view parallel to the axis" check then fired
    # within ~45 degrees of the diagonal and threw the selection 1e9 m away.)
    from mathutils import Vector
    worst = 0.0
    seen = []
    for fwd in ((1, 0.4, -0.4), (1, 0.2, -0.6), (0.3, 1, -0.5), (-1, -0.4, -0.5)):
        reset_object_mode()
        r3d = AREA.spaces.active.region_3d
        r3d.view_perspective = 'PERSP'
        r3d.view_location = (0, 0, 0)
        r3d.view_distance = 10
        r3d.view_rotation = Vector(fwd).normalized().to_track_quat('-Z', 'Y')
        yield 0.1
        home()
        yield 0.1
        tap('G')
        yield 0.1
        ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
        yield 0.3
        for dx, dy in ((20, 8), (-20, -8), (-6, -3), (3, 2), (-2, 4), (8, -5), (-10, 0), (0, -9), (40, 25)):
            move(dx, dy)
            yield 0.04
            L = loc()
            seen.append(L)
            worst = max(worst, max(abs(c) for c in L))
        tap('ESC')
        yield 0.15
    record("perspective along the diagonal: no jump", worst < 50 and all(abs(a - b) < 1e-4 for a, b, _c in seen),
           worst=worst, sample=seen[:6])
    with ctx():
        bpy.ops.view3d.view_axis(type='TOP')
    AREA.spaces.active.region_3d.view_perspective = 'ORTHO'
    yield 0.1

    # 11g2. Middle mouse in a diagonal Move does what it does after G X: Blender drops the lock
    # and picks among the header's X, Y, Z (stopConstraint + initSelectConstraint). Our line
    # must go, or it would keep showing the diagonal. Drag right, release, type 1: along X.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    line_before = axis_chords._session.line if axis_chords._session else None
    ev('MIDDLEMOUSE')
    yield 0.05
    for _i in range(6):
        move(15, -2)
        yield 0.03
    line_during = axis_chords._session.line if axis_chords._session else 'no session'
    shot("middle_mouse_in_diagonal")
    ev('MIDDLEMOUSE', 'RELEASE')
    yield 0.1
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    record("middle mouse in a diagonal = Blender's X/Y/Z, line gone", close(loc(), (1, 0, 0)) and line_before is not None
           and line_during is None, loc=loc(), line_during=str(line_during))

    # 11h. Enter, then G again at once (before any other event): the new Move has chords too.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    ev('RET')
    ev('G')
    ev('RET', 'RELEASE')
    ev('G', 'RELEASE')
    yield 0.1
    ev('X'); ev('Z'); ev('X', 'RELEASE'); ev('Z', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    record("Enter then G at once", close(loc(), (2 * H2, H2, H2)), loc=loc())

    # 11i. Esc in the same instant as the chord: everything back, nothing left running.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    move(50, 50)
    ev('X'); ev('Y'); tap('ESC'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    record("Esc during restart", close(loc(), (0, 0, 0)) and not running()
           and axis_chords._session is None, loc=loc(), running=running())

    # 11j. Shift+R after a diagonal (README limit): Blender drops the custom axis on
    # Repeat Last, so the repeat goes along X. Recorded to keep the README honest.
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    ev('LEFT_SHIFT')
    tap('R', shift=True)
    ev('LEFT_SHIFT', 'RELEASE')
    yield 0.2
    record("Shift+R repeats along X (known limit)", close(loc(), (H2 + 1, H2, 0)), loc=loc())

    # 11g. Pivot on the 3D cursor: Blender's line and ours both go through the cursor.
    reset_object_mode()
    ts = bpy.context.scene.tool_settings
    ts.transform_pivot_point = 'CURSOR'
    bpy.context.scene.cursor.location = (-2, 1, 0)
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    move(60, 60)
    yield 0.1
    line = axis_chords._session.line if axis_chords._session else None
    shot("pivot_cursor")
    tap('ESC')
    yield 0.15
    record("pivot cursor line", line is not None and close(line[0], (-2, 1, 0)),
           line=str(line))
    ts.transform_pivot_point = 'MEDIAN_POINT'
    bpy.context.scene.cursor.location = (0, 0, 0)

    # 11k. Remapped axis keys: Y on C (Shift+C for the plane), a common setup next to X.
    um = bpy.context.window_manager.keyconfigs.user.keymaps[axis_chords.MODAL_MAP]
    remapped = [k for k in um.keymap_items if k.propvalue in {'AXIS_Y', 'PLANE_Y'}]
    for k in remapped:
        k.type = 'C'
    yield 0.2

    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('C'); ev('X', 'RELEASE'); ev('C', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    record("Y on C: X+C chords", close(loc(), (H2, H2, 0)), loc=loc())

    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    record("Y on C: Y is no axis key", close(loc(), (1, 0, 0)), loc=loc())

    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    tap('C')
    yield 0.1
    tap('TWO', unicode='2')
    tap('RET')
    yield 0.15
    record("Y on C: C alone is Y", close(loc(), (0, 2, 0)), loc=loc())

    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('LEFT_SHIFT')
    ev('X', shift=True); ev('C', shift=True)
    ev('X', 'RELEASE', shift=True); ev('C', 'RELEASE', shift=True)
    ev('LEFT_SHIFT', 'RELEASE')
    yield 0.3
    move(100, 0)
    yield 0.1
    tap('LEFTMOUSE')
    yield 0.15
    L = loc()
    record("Y on C: Shift+X+C plane", abs(L[0] + L[1]) < 1e-4 and abs(L[0]) > 0.05, loc=L)

    bpy.context.window_manager.keyconfigs.user.keymaps[axis_chords.MODAL_MAP].restore_to_default()
    yield 0.2

    # 11l. Move on another key: our Move follows it.
    okm = bpy.context.window_manager.keyconfigs.user.keymaps['Object Mode']
    mv = next(k for k in okm.keymap_items if k.idname == 'transform.translate' and k.type == 'G')
    mv.type = 'F'
    yield 0.3          # let Blender store the user's change before the add-on reads it
    axis_chords._bind_move_keys()
    yield 0.3
    reset_object_mode()
    home()
    yield 0.1
    tap('F')
    yield 0.1
    r = running()
    ev('X'); ev('Y'); ev('X', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    record("Move on F: F then X+Y", close(loc(), (H2, H2, 0)), loc=loc(), running_during=r)
    bpy.context.window_manager.keyconfigs.user.keymaps['Object Mode'].restore_to_default()
    yield 0.2
    axis_chords._bind_move_keys()
    yield 0.3

    # 11m. Move on a mouse button (Button 4) in Blender's own keymap stays plain Blender:
    # no watcher, and releasing the button confirms as Blender does (Release Confirms).
    axis_chords._unbind_move_keys()
    yield 0.3
    okm = bpy.context.window_manager.keyconfigs.user.keymaps['Object Mode']
    mv = next(k for k in okm.keymap_items if k.idname == 'transform.translate' and k.type == 'G')
    mv.type = 'BUTTON4MOUSE'
    yield 0.3
    axis_chords._bind_move_keys()
    yield 0.3
    reset_object_mode()
    home()
    yield 0.1
    ev('BUTTON4MOUSE')
    yield 0.1
    r = running()
    move(60, 0)
    yield 0.1
    ev('BUTTON4MOUSE', 'RELEASE')
    yield 0.15
    L = loc()
    record("Move on mouse Button 4 stays Blender's", r == ['TRANSFORM_OT_translate'] and not running()
           and L[0] > 0.1, loc=L, running_during=r)
    axis_chords._unbind_move_keys()
    yield 0.3
    bpy.context.window_manager.keyconfigs.user.keymaps['Object Mode'].restore_to_default()
    yield 0.2
    axis_chords._bind_move_keys()
    yield 0.3

    # 11n. The X axis on a mouse button (Button 5) in the Transform keymap: Button 5 + Y chords.
    um = bpy.context.window_manager.keyconfigs.user.keymaps[axis_chords.MODAL_MAP]
    next(k for k in um.keymap_items if k.propvalue == 'AXIS_X').type = 'BUTTON5MOUSE'
    yield 0.3
    reset_object_mode()
    home()
    yield 0.1
    tap('G')
    yield 0.1
    ev('BUTTON5MOUSE'); ev('Y'); ev('BUTTON5MOUSE', 'RELEASE'); ev('Y', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    record("X axis on mouse Button 5", close(loc(), (H2, H2, 0)), loc=loc())
    bpy.context.window_manager.keyconfigs.user.keymaps[axis_chords.MODAL_MAP].restore_to_default()
    yield 0.2

    # 12. Edit Mode, all vertices.
    reset_object_mode()
    me = cube().data
    start = [tuple(v.co) for v in me.vertices]
    with ctx():
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
    home()
    yield 0.2
    tap('G')
    yield 0.1
    ev('X'); ev('Z'); ev('X', 'RELEASE'); ev('Z', 'RELEASE')
    yield 0.3
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    with ctx():
        bpy.ops.object.mode_set(mode='OBJECT')
    moved = [tuple(round(a - b, 5) for a, b in zip(v.co, s)) for v, s in zip(me.vertices, start)]
    record("Edit Mode X+Z 1", all(close(m, (H2, 0, H2)) for m in moved), sample=moved[:2])
    with ctx():
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.transform.translate(value=(-H2, 0, -H2))
        bpy.ops.object.mode_set(mode='OBJECT')

    # 13. Edit Mode, one vertex, Y+Z by mouse.
    with ctx():
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='DESELECT')
        bpy.ops.object.mode_set(mode='OBJECT')
    me.vertices[0].select = True
    start = [tuple(v.co) for v in me.vertices]
    with ctx():
        bpy.ops.object.mode_set(mode='EDIT')
    yield 0.2
    home()
    yield 0.05
    tap('G')
    yield 0.1
    ev('Y'); ev('Z'); ev('Y', 'RELEASE'); ev('Z', 'RELEASE')
    yield 0.3
    tap('MINUS', unicode='-')
    tap('ONE', unicode='1')
    tap('RET')
    yield 0.15
    with ctx():
        bpy.ops.object.mode_set(mode='OBJECT')
    moved = [tuple(round(a - b, 5) for a, b in zip(v.co, s)) for v, s in zip(me.vertices, start)]
    record("Edit Mode one vertex Y+Z -1",
           close(moved[0], (0, -H2, -H2)) and all(close(m, (0, 0, 0)) for m in moved[1:]),
           first=moved[0])

    yield 0.1


def main():
    gen = cases()

    def tick():
        try:
            return next(gen)
        except StopIteration:
            pass
        except Exception:  # noqa: BLE001
            info["error"] = traceback.format_exc()
        um = bpy.context.window_manager.keyconfigs.user.keymaps[axis_chords.MODAL_MAP]
        results.append({"case": "user keymap untouched",
                        "ok": not um.is_user_modified and not axis_chords._cancel_item_live()
                        and axis_chords._cancel_item is None and axis_chords._session is None})
        t0 = axis_chords.TRACE[0][0] if axis_chords.TRACE else 0
        info["trace"] = [f"{t - t0:8.3f} {n}" for t, n in axis_chords.TRACE]
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump({"info": info, "results": results}, f, indent=1, default=str)
        # Exit without Blender's quit handling, which would overwrite the user's quit.blend
        # (File > Recover > Last Session).
        os._exit(0)

    bpy.app.timers.register(tick, first_interval=1.5)


main()
