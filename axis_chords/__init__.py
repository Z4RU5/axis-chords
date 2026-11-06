# SPDX-License-Identifier: GPL-3.0-or-later
"""Axis Chords: press two axis keys together during Move to move diagonally.

G then X+Y moves along the X/Y diagonal, the same way G then X moves along X.
The keys are whatever the user's keymap has: Move's own key (G by default) and
the axis keys of the Transform modal keymap (X, Y, Z and Shift for planes by
default; someone with Y on C presses X+C).
Everything else is Blender's own Move: snapping, typed numbers, proportional
editing, the header, undo and Adjust Last Operation.

How it works. Blender's Move is C code and a script cannot change it while it
runs. So G starts Blender's own Move, and a small watcher sits above it and
sees each key first. Single axis keys pass straight through. When two axis
keys go down together, the watcher cancels that Move and starts Blender's Move
again, constrained to the diagonal (a custom orientation matrix whose X axis is
the diagonal) and offset so the selection keeps following the mouse.

Python cannot cancel another operator directly. The watcher adds, for a moment,
a "Cancel on timer" item to the Transform modal keymap and lets one timer event
through; the Move cancels itself; the item is removed before the new Move
starts. The user keymap is checked on both sides of that, so the new Move
never sees the item.
"""

import itertools
import time

import bpy
from bpy_extras import view3d_utils
from mathutils import Matrix, Vector

AXES = ('X', 'Y', 'Z')
TICK = 0.005                 # seconds between timer events while we need them
CANCEL_GIVE_UP = 1.0         # seconds; if the Move has not cancelled by then, stop trying
MODAL_MAP = 'Transform Modal Map'
# Transform modal actions that lock an axis, as (axis, plane). Read from the user's
# keymap, so remapped keys (Y on C, say) chord the same way.
AXIS_ACTIONS = {
    'AXIS_X': ('X', False), 'AXIS_Y': ('Y', False), 'AXIS_Z': ('Z', False),
    'PLANE_X': ('X', True), 'PLANE_Y': ('Y', True), 'PLANE_Z': ('Z', True),
}
# The keymaps where Blender binds Move in the 3D Viewport (G by default).
MOVE_KEYMAPS = (
    'Object Mode', 'Mesh', 'Curve', 'Curves', 'Armature', 'Metaball', 'Lattice',
    'Pose', 'Point Cloud', 'Grease Pencil Edit Mode',
)

_session = None              # state of the Move being watched (one at a time)
_addon_keymaps = []          # (keymap, item) pairs we added on Move's keys
_cancel_item = None          # the temporary "Cancel on timer" modal item
TRACE = []                   # (time, note) of each step, for tests and bug reports


def _trace(note):
    TRACE.append((time.perf_counter(), note))
    del TRACE[:-300]


# ---------------------------------------------------------------------------
# Preferences

class AxisChordsPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    chord_window: bpy.props.FloatProperty(
        name="Chord Window",
        description=("Two axis keys count as pressed together when the second one goes down "
                     "while the first is still held, or within this time of it"),
        default=0.15, min=0.03, max=0.5, subtype='TIME_ABSOLUTE', unit='TIME_ABSOLUTE',
    )
    show_line: bpy.props.BoolProperty(
        name="Mixed-Colour Line",
        description="Draw the diagonal in the mixed axis colour (X+Y yellow, X+Z magenta, Y+Z cyan)",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "chord_window")
        layout.prop(self, "show_line")
        col = layout.column(align=True)
        col.label(text="During Move (G): press two axis keys together, e.g. X+Y.")
        col.label(text="Same keys again: the other diagonal, then free. Shift: everything but the diagonal.")


def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _chord_window():
    p = _prefs()
    return p.chord_window if p else 0.15


def _show_line():
    p = _prefs()
    return p.show_line if p else True


# ---------------------------------------------------------------------------
# Keymap helpers

def _preset_modal_map():
    """The Transform modal keymap of the active preset (e.g. "Blender").

    Add-on keyconfigs cannot hold modal keymaps, and the user keyconfig is what
    gets saved with the preferences. The preset is rebuilt at every start and
    never saved, so a short-lived item there leaves nothing behind."""
    wm = bpy.context.window_manager
    for kc in (wm.keyconfigs.active, wm.keyconfigs.default):
        km = kc.keymaps.get(MODAL_MAP) if kc else None
        if km is not None:
            return km
    return None


def _set_cancel_item(on):
    """Add or remove the temporary 'Cancel on timer' item."""
    global _cancel_item
    km = _preset_modal_map()
    if km is None:
        return
    if on and _cancel_item is None:
        _cancel_item = (km, km.keymap_items.new_modal('CANCEL', 'TIMER', 'ANY'))
    elif not on and _cancel_item is not None:
        owner, item = _cancel_item
        _cancel_item = None
        try:
            owner.keymap_items.remove(item)
        except (ReferenceError, RuntimeError):
            pass


def _cancel_item_live():
    """True when Blender's merged (user) keymap, the one events are matched against, has it."""
    km = bpy.context.window_manager.keyconfigs.user.keymaps.get(MODAL_MAP)
    if km is None:
        return False
    return any(k.type == 'TIMER' and k.propvalue == 'CANCEL' and k.active for k in km.keymap_items)


def _mod_ok(want, have):
    # 4.x+: -1 means "any"; 0/1 off/on (older versions use bools).
    return want == -1 or bool(want) == have


def _modal_value(event):
    """The Transform modal action this event triggers, if any."""
    km = bpy.context.window_manager.keyconfigs.user.keymaps.get(MODAL_MAP)
    if km is None:
        return None
    for k in km.keymap_items:
        if not k.active or k.type != event.type:
            continue
        if k.value not in ('ANY', event.value):
            continue
        if not k.any and not (_mod_ok(k.shift, event.shift) and _mod_ok(k.ctrl, event.ctrl)
                              and _mod_ok(k.alt, event.alt) and _mod_ok(k.oskey, event.oskey)):
            continue
        return k.propvalue
    return None


def _axis_press(event):
    """(axis, plane) when this press locks an axis in the user's Transform keymap, else None.

    First matching item wins, as in Blender (with Y on C, C is Y even though C
    also turns constraints off further down the list)."""
    if event.value != 'PRESS':
        return None
    return AXIS_ACTIONS.get(_modal_value(event))


def _move_running(window):
    try:
        return any(op.bl_idname == 'TRANSFORM_OT_translate' for op in window.modal_operators)
    except ReferenceError:
        return False


# ---------------------------------------------------------------------------
# Geometry

def _orientation(context):
    """(matrix, exact) of the header orientation, the one Move's first axis press uses.

    exact is False where Python cannot rebuild Blender's matrix (Normal in Edit Mode,
    Gimbal): diagonals then use Global axes."""
    scene = context.scene
    slot = scene.transform_orientation_slots[0]
    kind = slot.type
    ob = context.active_object
    if kind == 'GLOBAL':
        return Matrix.Identity(3), True
    if kind == 'LOCAL' or (kind == 'NORMAL' and context.mode in {'OBJECT', 'POSE'}):
        if ob is None:
            return Matrix.Identity(3), True
        if context.mode == 'POSE' and context.active_pose_bone:
            return (ob.matrix_world @ context.active_pose_bone.matrix).to_3x3().normalized(), True
        return ob.matrix_world.to_3x3().normalized(), True
    if kind == 'VIEW':
        return context.region_data.view_matrix.to_3x3().transposed(), True
    if kind == 'CURSOR':
        return scene.cursor.matrix.to_3x3().normalized(), True
    if kind == 'PARENT':
        if ob is not None and ob.parent is not None:
            return ob.parent.matrix_world.to_3x3().normalized(), True
        return Matrix.Identity(3), True
    if slot.custom_orientation is not None:
        return slot.custom_orientation.matrix.copy(), True
    return Matrix.Identity(3), False


def _selected_points(context):
    """World positions Move moves, as an (n, 3) array, or None where we cannot tell."""
    import numpy as np
    if context.mode == 'OBJECT':
        obs = context.selected_objects
        return np.array([ob.matrix_world.translation for ob in obs]) if obs else None
    if context.mode != 'EDIT_MESH':
        return None
    parts = []
    for ob in context.objects_in_mode_unique_data:
        ob.update_from_editmode()
        me = ob.data
        n = len(me.vertices)
        if not n:
            continue
        sel = np.zeros(n, dtype=bool)
        me.vertices.foreach_get("select", sel)
        if not sel.any():
            continue
        co = np.zeros(n * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        mw = np.array(ob.matrix_world)
        parts.append(co.reshape(n, 3)[sel] @ mw[:3, :3].T + mw[:3, 3])
    return np.concatenate(parts) if parts else None


def _active_point(context):
    ob = context.active_object
    if ob is None:
        return None
    if context.mode == 'OBJECT':
        return ob.matrix_world.translation.copy() if ob.select_get() else None
    import bmesh
    bm = bmesh.from_edit_mesh(ob.data)
    elem = bm.select_history.active
    if isinstance(elem, bmesh.types.BMVert):
        p = elem.co
    elif isinstance(elem, bmesh.types.BMEdge):
        p = (elem.verts[0].co + elem.verts[1].co) / 2
    elif isinstance(elem, bmesh.types.BMFace):
        p = elem.calc_center_median()
    else:
        return None
    return ob.matrix_world @ p


def _move_center(context):
    """(center, exact): the point Move draws its axis line through (it follows the pivot).

    exact is False in modes we do not rebuild; the center is then only good enough
    for depth and for picking a diagonal."""
    pts = _selected_points(context)
    if pts is None or not len(pts):
        ob = context.active_object
        if ob is not None:
            box = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
            return sum(box, Vector()) / 8, False
        return context.scene.cursor.location.copy(), False
    pivot = context.scene.tool_settings.transform_pivot_point
    if pivot == 'CURSOR':
        return context.scene.cursor.location.copy(), True
    if pivot == 'BOUNDING_BOX_CENTER':
        return Vector((pts.min(axis=0) + pts.max(axis=0)) / 2), True
    if pivot == 'ACTIVE_ELEMENT':
        p = _active_point(context)
        if p is not None:
            return p, True
    return Vector(pts.mean(axis=0)), True


def _candidates(axes):
    """Diagonals through the chosen axes, in orientation space (+-1 on each, not unit length)."""
    idx = [AXES.index(a) for a in axes]
    out = []
    for signs in itertools.product((1, -1), repeat=len(idx) - 1):
        v = Vector((0.0, 0.0, 0.0))
        v[idx[0]] = 1.0
        for i, s in zip(idx[1:], signs):
            v[i] = float(s)
        out.append(v)
    return out


def _basis(d, axes):
    """Orthonormal columns: the diagonal, a perpendicular, and their cross product.

    Every column must be exactly 1 long. Blender's axis lock assumes unit axes:
    its "view parallel to the axis" check compares dot(axis, view ray) with 1, so
    a longer axis (1.41 for one unit on each of two axes, as 1.1.0 had) trips it
    within ~45 degrees of the diagonal and Blender throws the selection 1e9 away."""
    d = d.normalized()
    if len(axes) == 2:
        i, j = (AXES.index(a) for a in axes)
        u = Vector((0.0, 0.0, 0.0))
        u[i] = -d[j]          # the other diagonal of the same plane
        u[j] = d[i]
    else:
        u = d.cross(Vector((0.0, 0.0, 1.0)))
    u.normalize()
    w = d.cross(u)
    w.normalize()
    return Matrix((d, u, w)).transposed()


def _line_colour(axes):
    """The axis line colour Blender would use, with the axes' theme colours added together.

    Blender draws a locked axis 85% of the way from light grey (220) to the
    theme's axis colour; the same here, from the summed colours."""
    ui = bpy.context.preferences.themes[0].user_interface
    theme = {'X': ui.axis_x, 'Y': ui.axis_y, 'Z': ui.axis_z}
    mix = Vector((0.0, 0.0, 0.0))
    for a in axes:
        mix += Vector(theme[a][:3])
    mix = Vector([min(c, 1.0) for c in mix])
    return Vector((220 / 255,) * 3).lerp(mix, 0.85)


def _free_delta(s):
    """World offset Blender's free Move gives for the mouse travel since G."""
    a = view3d_utils.region_2d_to_location_3d(s.region, s.rv3d, s.mouse0, s.center)
    b = view3d_utils.region_2d_to_location_3d(s.region, s.rv3d, s.mouse, s.center)
    return b - a


def _order_by_mouse(s, cands, orient):
    """Candidate indices, the one closest to the mouse direction first."""
    travel = s.mouse - s.mouse0
    if travel.length < 8.0:
        return list(range(len(cands)))
    c2 = view3d_utils.location_3d_to_region_2d(s.region, s.rv3d, s.center)
    step = max(s.rv3d.view_distance * 0.05, 1e-3)
    scores = []
    for d in cands:
        world = orient @ d
        p = view3d_utils.location_3d_to_region_2d(s.region, s.rv3d, s.center + world.normalized() * step)
        if c2 is None or p is None or (p - c2).length < 1e-6:
            scores.append(0.0)
        else:
            scores.append(abs((p - c2).normalized().dot(travel.normalized())))
    return sorted(range(len(cands)), key=lambda i: -scores[i])


# ---------------------------------------------------------------------------
# Line drawing (the diagonal in the mixed axis colour)

_draw_handle = None


def _draw_line():
    s = _session
    if s is None or s.line is None or bpy.context.region != s.region or not _move_running(s.window):
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    center, direction, rgb = s.line
    far = bpy.context.space_data.clip_end
    a = center - direction.normalized() * far
    b = center + direction.normalized() * far
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    region = bpy.context.region
    shader.uniform_float("viewportSize", (region.width, region.height))
    # Blender's own axis line is 2 x pixel size; ours is a touch wider so it covers it fully.
    shader.uniform_float("lineWidth", bpy.context.preferences.system.pixel_size * 2.0 + 1.0)
    shader.uniform_float("color", (*rgb, 1.0))
    gpu.state.blend_set('ALPHA')
    batch_for_shader(shader, 'LINES', {"pos": [a, b]}).draw(shader)
    gpu.state.blend_set('NONE')


def _line_on(on):
    """(Re)add the draw handler so it draws after (on top of) the Move's own line."""
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if on:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_line, (), 'WINDOW', 'POST_VIEW')


# ---------------------------------------------------------------------------
# Session state

class _Session:
    def __init__(self, context, event):
        self.window = context.window
        self.area = context.area
        self.region = context.region
        self.rv3d = context.region_data
        self.mouse0 = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        self.mouse = self.mouse0.copy()
        self.mode = 'NATIVE'      # NATIVE: Blender's Move as is; CHORD: our diagonal Move;
        #                           DORMANT: we gave up on this Move and only watch it end
        self.phase = 'WATCH'      # WATCH, CANCEL (waiting for the Move to cancel), SETTLE
        self.down = {}            # key type -> axis, for axis keys held now
        self.pressed = {}         # axis -> (time, plane) of its last press
        self.pending = None       # (axis, plane, time): lone axis key in CHORD mode
        self.chord = None         # (axes, shift) of the current diagonal
        self.order = []
        self.pos = 0
        self.orient = Matrix.Identity(3)
        self.orient_exact = True
        self.center = None
        self.center_exact = False
        self.target = None
        self.abort = False
        self.since = 0.0
        self.line = None
        self.kick = False          # the new Move needs one event before typed digits count


def _end_session():
    global _session
    _session = None
    _set_cancel_item(False)
    _line_on(False)


# ---------------------------------------------------------------------------
# Operators

class AXISCHORDS_OT_move(bpy.types.Operator):
    """Move selected items. Press two axis keys together (X+Y, X+Z, Y+Z, X+Y+Z) to move diagonally"""
    bl_idname = "transform.axis_chord_move"
    bl_label = "Move (Axis Chords)"

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and context.region is not None and context.region.type == 'WINDOW'
                and (context.mode in {'OBJECT', 'POSE'} or context.mode.startswith('EDIT'))
                and bpy.ops.transform.translate.poll())

    def invoke(self, context, event):
        global _session
        if _session is not None:
            _end_session()
        ret = bpy.ops.transform.translate('INVOKE_DEFAULT')
        if 'RUNNING_MODAL' not in ret:
            return {'CANCELLED'}
        _session = _Session(context, event)
        bpy.ops.transform.axis_chord_watch('INVOKE_DEFAULT')
        return {'FINISHED'}


class AXISCHORDS_OT_watch(bpy.types.Operator):
    """Watches keys above Blender's Move (internal)"""
    bl_idname = "transform.axis_chord_watch"
    bl_label = "Axis Chords Watch"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        if _session is None:
            return {'CANCELLED'}
        self._timer = None
        self._session = _session      # a watcher only ever serves the Move it was started for
        context.window_manager.modal_handler_add(self)
        if _session.kick:
            # A Move started outside a key or mouse event skips typed digits until it has
            # handled one event (its first redraw is still pending). Hand it a timer tick.
            self._timer_on(context)
        return {'RUNNING_MODAL'}

    # -- timer -------------------------------------------------------------
    def _timer_on(self, context):
        if self._timer is None:
            self._timer = context.window_manager.event_timer_add(TICK, window=_session.window)

    def _timer_off(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _done(self, context, pass_through, end=True):
        self._timer_off(context)
        if end:
            _end_session()
        return {'FINISHED', 'PASS_THROUGH'} if pass_through else {'FINISHED'}

    # -- events ------------------------------------------------------------
    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:
            # Never leave a Move stuck because of us: step aside and let Blender handle it.
            import traceback
            traceback.print_exc()
            self._timer_off(context)
            _end_session()
            return {'FINISHED', 'PASS_THROUGH'}

    def _modal(self, context, event):
        s = _session
        if s is None or s is not self._session:
            return self._done(context, True, end=False)
        et, ev = event.type, event.value
        if et in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            s.mouse = Vector((event.mouse_x - s.region.x, event.mouse_y - s.region.y))

        if s.phase == 'CANCEL':
            return self._while_cancelling(context, event)
        if s.phase == 'SETTLE':
            return self._while_settling(context, event)

        if not _move_running(s.window):
            return self._done(context, True)

        if et == 'TIMER' and s.kick:
            s.kick = False
            if s.pending is None:
                self._timer_off(context)
            return {'PASS_THROUGH'}

        if ev == 'RELEASE' and et in s.down:
            del s.down[et]
            return {'PASS_THROUGH'}

        # R, S, G G (slide)... turn the Move into another operator; _move_running then
        # reports it gone and this watcher steps aside on the next event.
        if s.mode == 'DORMANT':
            return {'PASS_THROUGH'}

        # Middle-mouse picking (or constraint off) in a diagonal Move: Blender now chooses
        # among our matrix's axes (this diagonal, the other one, the third axis) and draws
        # them itself, so our line, which would keep showing the first diagonal, goes.
        if s.mode == 'CHORD' and s.line is not None and ev in {'PRESS', 'CLICK_DRAG'} \
                and _modal_value(event) in {'AUTOCONSTRAIN', 'AUTOCONSTRAINPLANE', 'CONS_OFF'}:
            s.line = None
            return {'PASS_THROUGH'}

        hit = _axis_press(event)
        if hit is not None:
            axis, plane = hit
            if event.is_repeat:
                return {'PASS_THROUGH'} if s.mode == 'NATIVE' else {'RUNNING_MODAL'}
            now = time.perf_counter()
            window = _chord_window()
            partners = {a for k, a in s.down.items() if k != et and a != axis}
            partners |= {a for a, (t, _p) in s.pressed.items() if a != axis and now - t <= window}
            plane = plane or any(s.pressed[a][1] for a in partners if a in s.pressed)
            s.down[et] = axis
            s.pressed[axis] = (now, plane)
            if partners:
                axes = tuple(a for a in AXES if a in partners or a == axis)
                s.pending = None
                self._begin(context, ('CHORD', axes, plane))
                return {'RUNNING_MODAL'}
            if s.mode == 'NATIVE':
                return {'PASS_THROUGH'}
            # CHORD mode: axis keys mean global/header axes, not our matrix's, so we own them.
            # Wait one chord window in case a second key joins.
            s.pending = (axis, plane, now)
            self._timer_on(context)
            return {'RUNNING_MODAL'}

        if et == 'TIMER' and s.pending is not None:
            if time.perf_counter() - s.pending[2] > _chord_window():
                axis, shift, _t = s.pending
                s.pending = None
                self._begin(context, ('AXIS', axis, shift))
                return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    def _track_axis_keys(self, event):
        s = _session
        if event.value == 'RELEASE':
            s.down.pop(event.type, None)
            return
        hit = _axis_press(event)
        if hit is None or event.is_repeat:
            return
        axis, plane = hit
        s.down[event.type] = axis
        s.pressed[axis] = (time.perf_counter(), plane)
        # A key that joins a chord while its Move restarts (X+Y+Z pressed as one).
        if s.target == ('DIAG',) and s.chord and axis not in s.chord[0]:
            axes = tuple(a for a in AXES if a in s.chord[0] or a == axis)
            s.chord = (axes, s.chord[1] or plane)
            s.order = None
            s.pos = 0

    def _while_cancelling(self, context, event):
        s = _session
        if not _move_running(s.window):
            _set_cancel_item(False)
            _trace("cancelled")
            s.phase = 'SETTLE'
            return self._while_settling(context, event)
        if time.perf_counter() - s.since > CANCEL_GIVE_UP:
            # The Move did not cancel; leave it running untouched and stop chords for it.
            _set_cancel_item(False)
            s.phase = 'WATCH'
            s.mode = 'DORMANT'
            self._timer_off(context)
            return {'PASS_THROUGH'}
        if event.type == 'TIMER':
            # Once the cancel item is in the live keymap, this timer event cancels the Move.
            live = _cancel_item_live()
            _trace(f"cancel tick live={live}")
            return {'PASS_THROUGH'} if live else {'RUNNING_MODAL'}
        self._note_abort(event)
        self._track_axis_keys(event)
        return {'RUNNING_MODAL'}

    def _while_settling(self, context, event):
        s = _session
        if event.type != 'TIMER':
            self._note_abort(event)
            self._track_axis_keys(event)
        if s.abort:
            return self._done(context, False)
        if _cancel_item_live():
            # The item is still in the live keymap; a new Move would cancel at once.
            return {'RUNNING_MODAL'}
        _trace("restart")
        return self._restart(context)

    def _note_abort(self, event):
        # Esc or right-click while we restart: stop there (the cancel already put things back).
        if event.value == 'PRESS' and _modal_value(event) == 'CANCEL':
            _session.abort = True

    # -- restart -----------------------------------------------------------
    def _begin(self, context, target):
        s = _session
        kind = target[0]
        if kind == 'CHORD':
            _k, axes, shift = target
            if s.mode == 'CHORD' and s.chord == (axes, shift):
                s.pos += 1
                target = ('FREE',) if s.pos >= len(s.order) else ('DIAG',)
            else:
                s.orient, s.orient_exact = _orientation(context)
                s.chord = (axes, shift)
                s.order = None          # picked after the cancel, when the selection is back
                s.pos = 0
                target = ('DIAG',)
        s.target = target
        _trace(f"begin {target} mode={s.mode}")
        s.phase = 'CANCEL'
        s.since = time.perf_counter()
        s.abort = False
        _set_cancel_item(True)
        self._timer_on(context)

    def _restart(self, context):
        s = _session
        target, s.target = s.target, None
        s.phase = 'WATCH'
        s.line = None
        with context.temp_override(window=s.window, area=s.area, region=s.region):
            if s.center is None:
                s.center, s.center_exact = _move_center(context)
            offset = _free_delta(s)
            kw = {}
            if target[0] == 'DIAG':
                axes, shift = s.chord
                cands = _candidates(axes)
                if s.order is None:
                    s.order = _order_by_mouse(s, cands, s.orient)
                d = cands[s.order[s.pos]]
                m = s.orient @ _basis(d, axes)
                kw['orient_matrix'] = m
                kw['constraint_axis'] = (False, True, True) if shift else (True, False, False)
                kw['value'] = m.inverted_safe() @ offset
                s.mode = 'CHORD'
                if not shift and s.center_exact and _show_line():
                    s.line = (s.center.copy(), s.orient @ d, _line_colour(axes))
            else:
                s.mode = 'NATIVE'
                s.chord = None
                # Only the lock and no orientation: that is exactly the state Blender's own
                # first axis press leaves (header orientation now, Local/Global on the next
                # press of the same key, off on the third).
                if target[0] == 'AXIS':
                    _k, axis, shift = target
                    i = AXES.index(axis)
                    kw['constraint_axis'] = tuple((j != i) if shift else (j == i) for j in range(3))
                if s.orient_exact:
                    kw['value'] = s.orient.inverted_safe() @ offset
            ret = bpy.ops.transform.translate('INVOKE_DEFAULT', **kw)
            if 'RUNNING_MODAL' not in ret:
                return self._done(context, False)
            _line_on(s.line is not None)
            s.kick = True
            bpy.ops.transform.axis_chord_watch('INVOKE_DEFAULT')
        return self._done(context, False, end=False)


# ---------------------------------------------------------------------------
# Registration

classes = (AxisChordsPreferences, AXISCHORDS_OT_move, AXISCHORDS_OT_watch)


def _unbind_move_keys():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _addon_keymaps.clear()


def _move_keys(name):
    """The keyboard keys Blender's Move has in this keymap, as the user set them.

    Mouse bindings stay Blender's: a Move started by a mouse button confirms when
    that button is released (Release Confirms, on by default), and Blender ties
    that to the event that started the Move, which a restarted Move cannot keep.
    A mouse button that sends a key through the mouse's own software (G HUB,
    Options+) is a key to Blender and works. With no keymap to read (background
    runs) this falls back to G."""
    wm = bpy.context.window_manager
    for kc in (wm.keyconfigs.user, wm.keyconfigs.active):
        km = kc.keymaps.get(name) if kc else None
        if km is not None and len(km.keymap_items):
            return [dict(type=k.type, value=k.value, any=k.any, shift=k.shift, ctrl=k.ctrl,
                         alt=k.alt, oskey=k.oskey, key_modifier=k.key_modifier)
                    for k in km.keymap_items
                    if k.idname == 'transform.translate' and k.active and k.map_type == 'KEYBOARD']
    return [dict(type='G', value='PRESS')]


def _bind_move_keys():
    """Put our Move on the same keys as Blender's Move, in each mode's keymap.

    Add-on items come first, so ours is found before Blender's. Runs from a
    timer once the user's keymap has been built; re-enable the add-on (or
    restart Blender) after changing Move's key."""
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return None
    _unbind_move_keys()
    for name in MOVE_KEYMAPS:
        keys = _move_keys(name)
        if not keys:
            continue
        km = kc.keymaps.new(name=name, space_type='EMPTY', region_type='WINDOW')
        for key in keys:
            kmi = km.keymap_items.new(AXISCHORDS_OT_move.bl_idname, **key)
            _addon_keymaps.append((km, kmi))
    return None


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.app.timers.register(_bind_move_keys, first_interval=1.0)


def unregister():
    _end_session()
    if bpy.app.timers.is_registered(_bind_move_keys):
        bpy.app.timers.unregister(_bind_move_keys)
    _unbind_move_keys()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
