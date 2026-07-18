"""Figura/Blockbench avatar -> VRChat-ready FBX converter.

Takes a glTF exported from Blockbench (a Figura player avatar with the vanilla
bone names Head/Body/LeftArm/RightArm/LeftLeg/RightLeg) and produces a Unity
Humanoid-compatible, T-posed FBX with rigid Minecraft-style limbs.

Usage (headless):
  blender --background --python convert_to_vrchat.py
  blender --background --python convert_to_vrchat.py -- --height 1.6 --front-marker FrontHair

Everything after "--" is parsed by this script; run with "-- --help" for options.

Output (in --out, default ./vrchat_export):
  avatar.fbx           rigged, T-posed, VRChat-ready (incl. Neck+Shoulders,
                       which VRChat requires beyond Unity's Humanoid minimum)
  avatar_rigged.blend  editable Blender file of the same
  textures/*.png       textures extracted from the glTF (named after matching
                       PNGs found next to the glTF, when pixel-identical)
  preview_front.png / preview_side.png   quick verification renders
  convert_report.json  machine-readable summary

Rig style: rigid limbs. Elbow/knee/hand/foot bones exist so Unity can map a
Humanoid rig, but they carry no weights, so limbs never bend (classic
Minecraft look). See docs/CONVERSION.md for the full pipeline and the Unity
side of the workflow.
"""
import argparse
import json
import os
import sys
from math import pi, radians

import bpy
from mathutils import Matrix, Vector

# --------------------------------------------------------------------------
# Configuration defaults. Override per-run via CLI (after "--"), or edit the
# project-specific entries below when adapting this script to another avatar.
# --------------------------------------------------------------------------
PROJECT = os.path.dirname(os.path.abspath(__file__))

# Vanilla player bones -> Unity Humanoid names. Standard for Figura avatars.
RENAMES = {
    "Body": "Chest",
    "LeftArm": "LeftUpperArm",
    "RightArm": "RightUpperArm",
    "LeftLeg": "LeftUpperLeg",
    "RightLeg": "RightUpperLeg",
}

# Bones whose vertex-group centroid marks the model's FRONT, tried in order
# when --front-marker is "auto". Used to detect (and fix) models that face +Y.
FRONT_MARKER_CANDIDATES = ["TheGlasses", "FrontHair", "LeftIris", "RightIris"]

# Bones used to locate the eyes, tried in order; reported eye height goes in
# convert_report.json for the VRC Avatar Descriptor's View Position.
EYE_BONE_CANDIDATES = ["LeftIris", "RightIris", "Irises", "LeftEye", "RightEye"]

# Project-specific cosmetic vertex/bone offsets, in Blockbench pixels (1/16 m,
# applied pre-scale, +up). Dork: raise the brow bars half a pixel so the round
# glasses lens tops don't clip them into angry-looking wedges.
LIFT_PIXELS = {
    "LeftBrow": 0.5,
    "RightBrow": 0.5,
}


def default_gltf():
    """model.gltf from the working directory (the avatar folder you run the
    tool from), falling back to the script's own directory."""
    for cand in (os.path.join(os.getcwd(), "model.gltf"),
                 os.path.join(PROJECT, "model.gltf")):
        if os.path.exists(cand):
            return cand
    return os.path.join(os.getcwd(), "model.gltf")


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="convert_to_vrchat.py", description=__doc__)
    ap.add_argument("--gltf", default=default_gltf(),
                    help="input glTF exported from Blockbench "
                         "(default: model.gltf in the current directory)")
    ap.add_argument("--out", default=None,
                    help="output directory (default: vrchat_export next to the glTF)")
    ap.add_argument("--height", type=float, default=1.7,
                    help="target avatar height in metres (default 1.7)")
    ap.add_argument("--front-marker", default="auto",
                    help="bone whose geometry marks the model's front, 'auto', "
                         "or 'none' to skip facing detection")
    ap.add_argument("--lift", action="append", default=[], metavar="BONE=PIXELS",
                    help="raise a bone and its vertices by N Blockbench pixels "
                         "(repeatable; overrides the in-file LIFT_PIXELS)")
    ap.add_argument("--no-lift", action="store_true",
                    help="disable all cosmetic lifts, including LIFT_PIXELS")
    args = ap.parse_args(argv)
    if args.no_lift:
        args.lifts = {}
    elif args.lift:
        args.lifts = {}
        for spec in args.lift:
            name, _, px = spec.partition("=")
            args.lifts[name] = float(px)
    else:
        args.lifts = dict(LIFT_PIXELS)
    if args.out is None:
        args.out = os.path.join(os.path.dirname(os.path.abspath(args.gltf)),
                                "vrchat_export")
    return args


report = {"warnings": []}


def warn(msg):
    report["warnings"].append(msg)
    print("WARN:", msg)


def fail(msg):
    print("ERROR:", msg)
    sys.exit(1)


def group_verts_z(mesh_ob, group_name):
    """(min_z, max_z) of vertices weighted to group_name, or None."""
    gi = mesh_ob.vertex_groups.find(group_name)
    if gi < 0:
        return None
    zs = [v.co.z for v in mesh_ob.data.vertices
          if any(g.group == gi and g.weight > 0.5 for g in v.groups)]
    return (min(zs), max(zs)) if zs else None


def group_centroid(mesh_ob, group_name):
    gi = mesh_ob.vertex_groups.find(group_name)
    if gi < 0:
        return None
    cos = [v.co for v in mesh_ob.data.vertices
           if any(g.group == gi and g.weight > 0.5 for g in v.groups)]
    return sum(cos, Vector()) / len(cos) if cos else None


def main():
    args = parse_args()
    texdir = os.path.join(args.out, "textures")

    # ------------------------------------------------------------- import
    if not os.path.exists(args.gltf):
        fail(f"input not found: {args.gltf}")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.gltf)

    arm_ob = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    mesh_ob = next((o for o in bpy.data.objects
                    if o.type == 'MESH' and o.vertex_groups), None)
    if arm_ob is None or mesh_ob is None:
        fail("expected one armature and one skinned mesh in the glTF "
             "(is this a rigged Blockbench export?)")

    # Drop stray un-rigged meshes (leftover primitives in the source file).
    for ob in [o for o in bpy.data.objects
               if o.type == 'MESH' and o is not mesh_ob]:
        print("Removing un-rigged object:", ob.name)
        bpy.data.objects.remove(ob, do_unlink=True)

    arm_ob.name = "Armature"
    mesh_ob.name = "Avatar"

    missing = [b for b in RENAMES if b not in arm_ob.data.bones]
    if missing:
        fail(f"vanilla player bones missing: {missing}. Found bones: "
             f"{sorted(b.name for b in arm_ob.data.bones)[:20]}...")

    # The glTF importer leaves one imported action active on the armature; its
    # pose would contaminate the T-pose/mesh bake below. Detach it and reset.
    if arm_ob.animation_data:
        arm_ob.animation_data.action = None
        for tr in arm_ob.animation_data.nla_tracks:
            tr.mute = True
    for pb in arm_ob.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()

    # Cosmetic per-bone lifts (see LIFT_PIXELS / --lift).
    lifts = {n: px for n, px in args.lifts.items()
             if n in arm_ob.data.bones and mesh_ob.vertex_groups.find(n) >= 0}
    for name, px in lifts.items():
        dz = px / 16.0
        gi = mesh_ob.vertex_groups.find(name)
        for v in mesh_ob.data.vertices:
            if any(g.group == gi and g.weight > 0.5 for g in v.groups):
                v.co.z += dz
    if lifts:
        bpy.context.view_layer.objects.active = arm_ob
        arm_ob.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        for name, px in lifts.items():
            b = arm_ob.data.edit_bones[name]
            b.head.z += px / 16.0
            b.tail.z += px / 16.0
        bpy.ops.object.mode_set(mode='OBJECT')
        print("Applied lifts:", {n: f"{px}px" for n, px in lifts.items()})

    # ------------------------------------------------------------- facing
    # Blender rigs should face -Y; Blockbench exports usually face +Y.
    marker = None
    if args.front_marker == "none":
        pass
    elif args.front_marker != "auto":
        marker = group_centroid(mesh_ob, args.front_marker)
        if marker is None:
            warn(f"front marker '{args.front_marker}' empty; trying auto")
    if marker is None and args.front_marker != "none":
        for cand in FRONT_MARKER_CANDIDATES:
            marker = group_centroid(mesh_ob, cand)
            if marker is not None:
                print("Front marker:", cand)
                break
    if marker is None:
        if args.front_marker != "none":
            warn("no front marker found; assuming model faces +Y")
        faces_plus_y = True
    else:
        faces_plus_y = marker.y > 0
    if faces_plus_y:
        flip = Matrix.Rotation(pi, 4, 'Z')
        mesh_ob.data.transform(flip)
        arm_ob.data.transform(flip)
        print("Flipped model 180° to face -Y")

    # ------------------------------------------------------------- scale
    zs = [v.co.z for v in mesh_ob.data.vertices]
    height = max(zs) - min(zs)
    factor = args.height / height
    scale = Matrix.Scale(factor, 4)
    mesh_ob.data.transform(scale)
    arm_ob.data.transform(scale)
    print(f"Height {height:.3f} m -> scaled x{factor:.4f} to {args.height} m")

    # Location f-curves are in bone-local units; scale them to match.
    try:
        for act in bpy.data.actions:
            for layer in act.layers:
                for strip in layer.strips:
                    for cb in strip.channelbags:
                        for fc in cb.fcurves:
                            if fc.data_path.endswith("location"):
                                for kp in fc.keyframe_points:
                                    kp.co[1] *= factor
                                    kp.handle_left[1] *= factor
                                    kp.handle_right[1] *= factor
    except Exception as e:
        warn(f"could not rescale animation locations: {e}")

    # Single-frame pose actions (held expressions) export as zero-length FBX
    # takes, which Unity discards. Turn each into a two-key rest->pose ramp:
    # the take gains length, and posed bones become distinguishable from
    # static ones downstream.
    try:
        for act in bpy.data.actions:
            rng = act.frame_range
            if rng[1] - rng[0] >= 0.5:
                continue
            for layer in act.layers:
                for strip in layer.strips:
                    for cb in strip.channelbags:
                        for fc in cb.fcurves:
                            rest = 1.0 if ((fc.data_path.endswith("rotation_quaternion")
                                            and fc.array_index == 0)
                                           or fc.data_path.endswith("scale")) else 0.0
                            orig = [(kp.co[0], kp.co[1]) for kp in fc.keyframe_points]
                            for t, v in orig:
                                fc.keyframe_points.insert(t + 1.0, v)
                            for kp in fc.keyframe_points:
                                if any(abs(kp.co[0] - t) < 1e-6 for t, _ in orig):
                                    kp.co[1] = rest
                                kp.interpolation = 'LINEAR'
                            fc.update()
            print(f"Padded single-frame action: {act.name}")
    except Exception as e:
        warn(f"could not pad pose actions: {e}")

    # ---------------------------------------------------------- landmarks
    arm_span = (group_verts_z(mesh_ob, "LeftArm")
                or group_verts_z(mesh_ob, "RightArm"))
    leg_span = (group_verts_z(mesh_ob, "LeftLeg")
                or group_verts_z(mesh_ob, "RightLeg"))
    if arm_span is None or leg_span is None:
        fail("could not measure arm/leg extents from vertex groups")
    eye = None
    for cand in EYE_BONE_CANDIDATES:
        eye = group_centroid(mesh_ob, cand)
        if eye is not None:
            break
    if eye is None:
        warn("no eye bone found; estimating eye height as 0.9 * height")
    report["eye_height_m"] = round(eye.z, 3) if eye else round(0.9 * args.height, 3)

    # --------------------------------------------------------- edit bones
    bpy.context.view_layer.objects.active = arm_ob
    arm_ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm_ob.data.edit_bones

    for old, new in RENAMES.items():
        eb[old].name = new  # Blender auto-renames the matching vertex groups

    hips_z = eb["LeftUpperLeg"].head.z          # top of legs
    chest_old_head = eb["Chest"].head.copy()    # original Body pivot

    def new_bone(name, head, tail, parent):
        b = eb.new(name)
        b.head, b.tail = Vector(head), Vector(tail)
        b.parent = eb[parent]
        b.use_deform = True
        return b

    spine_z = hips_z + 0.2 * (chest_old_head.z - hips_z)
    chest_z = hips_z + 0.45 * (chest_old_head.z - hips_z)
    new_bone("Hips", (0, 0, hips_z), (0, 0, spine_z), "root")
    new_bone("Spine", (0, 0, spine_z), (0, 0, chest_z), "Hips")
    eb["Chest"].head = Vector((0, 0, chest_z))
    eb["Chest"].tail = Vector((0, 0, chest_old_head.z))
    eb["Chest"].parent = eb["Spine"]

    # VRChat additionally requires Neck and Shoulders (Unity Humanoid doesn't).
    head_z = eb["Head"].head.z
    new_bone("Neck", (0, 0, head_z - 0.04), (0, 0, head_z), "Chest")
    eb["Head"].parent = eb["Neck"]
    for side in ("Left", "Right"):
        ua, ul = eb[f"{side}UpperArm"], eb[f"{side}UpperLeg"]
        ax, az = ua.head.x, ua.head.z
        new_bone(f"{side}Shoulder", (0.3 * ax, 0, az + 0.02),
                 (0.75 * ax, 0, az + 0.02), "Chest")
        ua.parent = eb[f"{side}Shoulder"]
        ul.parent = eb["Hips"]

        # Rigid limbs: joints exist for the Humanoid map but carry no weights.
        top, bot = ua.head.z, arm_span[0]
        elbow = top + 0.45 * (bot - top)
        hand = top + 0.80 * (bot - top)
        new_bone(f"{side}LowerArm", (ax, 0, elbow), (ax, 0, elbow - 0.07),
                 f"{side}UpperArm")
        new_bone(f"{side}Hand", (ax, 0, hand), (ax, 0, hand - 0.06),
                 f"{side}LowerArm")

        lx, ltop, lbot = ul.head.x, ul.head.z, leg_span[0]
        knee = ltop + 0.5 * (lbot - ltop)
        ankle = lbot + 0.10 * (ltop - lbot)
        new_bone(f"{side}LowerLeg", (lx, 0, knee), (lx, 0, knee - 0.07),
                 f"{side}UpperLeg")
        new_bone(f"{side}Foot", (lx, 0, ankle),
                 (lx, -0.1, max(ankle - 0.05, lbot)), f"{side}LowerLeg")

    bpy.ops.object.mode_set(mode='OBJECT')

    # Sanity: every rename must have carried over to the vertex groups.
    vg = {g.name for g in mesh_ob.vertex_groups}
    for old, new in RENAMES.items():
        if new not in vg:
            if old in vg:
                mesh_ob.vertex_groups[old].name = new
                warn(f"vertex group {old} renamed manually")
            else:
                warn(f"vertex group for {new} missing entirely")

    # -------------------------------------------------------------- T-pose
    # Arms point straight down; rotate them out to the sides, bake the mesh in
    # that pose, then make it the rest pose.
    bpy.ops.object.mode_set(mode='POSE')
    for name, angle in (("LeftUpperArm", -90), ("RightUpperArm", 90)):
        pb = arm_ob.pose.bones[name]
        pivot = pb.head.copy()
        rot = (Matrix.Translation(pivot)
               @ Matrix.Rotation(radians(angle), 4, 'Y')
               @ Matrix.Translation(-pivot))
        pb.matrix = rot @ pb.matrix
        bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='OBJECT')

    mod = next(m for m in mesh_ob.modifiers if m.type == 'ARMATURE')
    with bpy.context.temp_override(object=mesh_ob, active_object=mesh_ob,
                                   selected_objects=[mesh_ob]):
        bpy.ops.object.modifier_apply(modifier=mod.name)

    bpy.context.view_layer.objects.active = arm_ob
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode='OBJECT')

    mod = mesh_ob.modifiers.new("Armature", 'ARMATURE')
    mod.object = arm_ob
    print("T-pose baked into rest pose")

    # ------------------------------------------------------------ textures
    # Unpack embedded images; name them after pixel-identical PNGs found next
    # to the glTF (Blockbench keeps textures as sibling files). Start from a
    # clean directory so re-runs never leave stale extracts behind.
    if os.path.isdir(texdir):
        for old in os.listdir(texdir):
            if old.lower().endswith(".png"):
                os.remove(os.path.join(texdir, old))
    os.makedirs(texdir, exist_ok=True)
    refs = {}
    src_dir = os.path.dirname(os.path.abspath(args.gltf))
    for fname in os.listdir(src_dir):
        if fname.lower().endswith(".png"):
            try:
                refs[fname] = bpy.data.images.load(os.path.join(src_dir, fname))
            except Exception:
                pass

    tex_map = {}
    used_names = set()
    packed = [im for im in bpy.data.images if im.packed_file]
    for i, im in enumerate(packed):
        name = f"texture_{i}.png"
        for fname, ref in refs.items():
            # Full-pixel comparison: textures often share their first rows
            # (e.g. both start transparent), so sampling a prefix mismatches.
            if (fname not in used_names
                    and list(ref.size) == list(im.size)
                    and tuple(ref.pixels[:]) == tuple(im.pixels[:])):
                name = fname
                break
        if name in used_names:
            warn(f"texture name collision on {name}; keeping generic name")
            name = f"texture_{i}.png"
        used_names.add(name)
        out = os.path.join(texdir, name)
        im.file_format = 'PNG'
        im.filepath_raw = out
        im.save()
        tex_map[im.name] = name
    for ref in refs.values():
        bpy.data.images.remove(ref)
    report["textures"] = tex_map

    # ------------------------------------------------------------ previews
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x, scene.render.resolution_y = 700, 1000
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = args.height * 1.4
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    for label, loc, rot in (
            ("front", (0, -4, args.height / 2), (pi / 2, 0, 0)),
            ("side", (4, 0, args.height / 2), (pi / 2, 0, pi / 2))):
        cam.location, cam.rotation_euler = loc, rot
        scene.render.filepath = os.path.join(args.out, f"preview_{label}.png")
        bpy.ops.render.render(write_still=True)

    # -------------------------------------------------------------- export
    bpy.data.objects.remove(cam, do_unlink=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(args.out, "avatar_rigged.blend"))

    fbx = os.path.join(args.out, "avatar.fbx")
    kwargs = dict(filepath=fbx, object_types={'ARMATURE', 'MESH'},
                  add_leaf_bones=False, apply_scale_options='FBX_SCALE_ALL',
                  bake_anim=True, bake_anim_use_all_actions=True,
                  bake_anim_use_nla_strips=False, path_mode='AUTO')
    try:
        bpy.ops.export_scene.fbx(**kwargs)
    except TypeError as e:
        warn(f"fbx exporter rejected an option ({e}); retrying minimal")
        bpy.ops.export_scene.fbx(filepath=fbx, add_leaf_bones=False)

    report["fbx"] = fbx
    report["bones"] = len(arm_ob.data.bones)
    report["height_m"] = args.height
    report["animations"] = sorted(a.name for a in bpy.data.actions)
    with open(os.path.join(args.out, "convert_report.json"), "w") as f:
        json.dump(report, f, indent=1)
    print("CONVERT_OK", json.dumps(report))


main()
