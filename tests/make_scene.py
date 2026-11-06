"""Save Blender's default scene as tests/test_scene.blend (run in background)."""
import os

import bpy

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_scene.blend"))
