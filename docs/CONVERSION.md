# Figura → VRChat avatar conversion guide

This documents the full pipeline used to convert a Figura (Blockbench) Minecraft
avatar into a working VRChat avatar — rig, physics, textures, and in-game
expression toggles included. It was built during one real conversion (the
"dork" avatar) and generalized to be reusable for any Figura player avatar.

```
model.bbmodel ──(Blockbench glTF export)──> model.gltf
model.gltf ──(convert_to_vrchat.py, headless Blender)──> vrchat_export/avatar.fbx
avatar.fbx ──(Unity + VRChat SDK + FiguraExpressionSetup.cs)──> published avatar
```

## Prerequisites

- **Blockbench** with the model open — export via `File > Export > Export glTF`
  (include animations).
- **Blender 4.2+** (developed on 5.2), used headless; no manual Blender work.
- **Unity 2022.3.x** via **VRChat Creator Companion** with an *Avatars* project
  (SDK3). A VRChat account with "New User" trust rank or higher (uploading is
  locked below that).

## Part 1 — Blender: glTF → FBX

Run from your avatar's folder (the one containing `model.gltf`):

```
blender --background --python path\to\figura2vrc\convert_to_vrchat.py
blender --background --python path\to\figura2vrc\convert_to_vrchat.py -- --help   # all options
```

What the script does, in order:

1. Imports the glTF; deletes stray un-rigged meshes (leftover primitives).
2. **Detaches the active animation and resets the pose** — the glTF importer
   leaves one imported action applied, which would otherwise contaminate every
   bake below (see trap #4).
3. Applies cosmetic per-bone lifts (`LIFT_PIXELS` / `--lift`, see trap #10).
4. Detects which way the model faces via a front-marker bone (glasses, front
   hair, iris...) and rotates it to Blender's -Y convention if needed.
5. Scales the model to `--height` (default 1.7 m) and rescales animation
   location curves to match.
6. **Pads single-frame pose animations** into two-key rest→pose ramps so they
   survive FBX export (see trap #5).
7. Renames the vanilla player bones to Unity Humanoid names and builds the
   missing skeleton: Hips, Spine, Chest, **Neck, Shoulders** (VRChat requires
   these two beyond Unity's minimum — trap #3), plus **zero-weight** elbows,
   knees, hands and feet so Unity can map a Humanoid rig while the limbs stay
   perfectly rigid (the classic Minecraft look).
8. Rotates the arms out and bakes a real T-pose into mesh and rest pose.
9. Extracts embedded textures (named after pixel-identical PNGs found next to
   the glTF) and renders front/side preview images.
10. Exports `avatar.fbx` (with all animation takes) plus an editable
    `avatar_rigged.blend` and `convert_report.json` — note `eye_height_m`,
    you need it for the View Position later.

## Part 2 — Unity

### Import

1. Copy `vrchat_export/avatar.fbx` and the extracted textures into `Assets/`.
2. Select the FBX → **Rig** tab → Animation Type: **Humanoid** → Apply.
   - **Uncheck "Strip Bones"** first (trap #1).
   - Open **Configure...** and verify the mapping. The auto-mapper is
     unreliable: fix Hips if it grabbed `root`, fill the limb slots via the ⊙
     pickers, and — critically — **set Jaw, Left Eye and Right Eye to None**
     (trap #2). Enforce T-Pose, Apply, Done.
3. Textures: **Filter Mode: Point**, **Compression: None** (trap #8).
4. Materials tab → Extract Materials → set every material's shader to
   **Unlit/Transparent Cutout** (trap #7). PC-only; use `VRChat/Mobile/*`
   shaders for a Quest version.

### Avatar setup

5. Drag the avatar into the scene at origin. Add a **VRC Avatar Descriptor**:
   View Position `(0, <eye_height_m>, ~0.2)`, Lip Sync: Default.
6. **PhysBones** on every dangly bone (hair, tails, skirt): add
   `VRC Phys Bone`, set **Endpoint Position** (single bones won't swing
   without it — check the capsule gizmo direction), start with Pull 0.2 /
   Spring 0.8 / Gravity 0.2. Test in Play mode by dragging the avatar around.
7. Copy `unity/FiguraExpressionSetup.cs` into `Assets/Editor/`, adjust its
   config block (expression clip names, blink, prop toggle), then run
   **Tools > Figura Avatar > Setup Expressions**. This builds the FX
   controller (auto-blink layer, face expression layer, animated prop toggle),
   expression parameters + radial menu, wires the descriptor, and flags
   animated PhysBones as *Is Animated*. Re-run any time; it is idempotent.
8. Save the scene. **VRChat SDK > Show Control Panel** → log in → Builder →
   run the Auto Fix buttons (Read/Write, blendshape normals, mipmap
   streaming) → thumbnail → **Build & Test**, then **Build & Publish**.

### Re-export loop (model changed in Blockbench)

```
1. Blockbench: re-export model.gltf
2. blender --background --python path\to\figura2vrc\convert_to_vrchat.py   (from the avatar folder)
3. copy vrchat_export/avatar.fbx over BOTH Assets/avatar.fbx and Assets/avatar_anims.fbx
4. Unity: Tools > Figura Avatar > Setup Expressions   (rebuilds clips)
5. Ctrl+S, Build & Publish
```

The `.meta` files preserve the Humanoid mapping and import settings across
overwrites; only newly added bones ever need re-configuring.

## Traps (every one of these cost us a debugging round)

| # | Symptom | Cause | Fix |
|---|---------|-------|-----|
| 1 | "Required human bone 'X' not found" | **Strip Bones** import option deletes zero-weight bones — our elbow/knee/hand/foot bones are intentionally weightless | Uncheck Strip Bones on the Rig tab |
| 2 | Prop won't animate ("Binding warning: transforms already bound by a Humanoid avatar"); accessories twitch on their own; resting face looks wrong | Unity's humanoid auto-mapper stuffs random head-child bones into the **Jaw / Left Eye / Right Eye** slots. Humanoid-bound transforms silently ignore FX animation, and VRChat's eye-look actively rotates whatever is in the Eye slots | Configure → Head tab → set Jaw and both Eyes to **None** |
| 3 | SDK error "Spine hierarchy missing elements: Neck, Shoulders" | VRChat requires Neck + Shoulders; Unity Humanoid does not | The converter adds them (zero-weight); map them in Configure |
| 4 | Face parts baked ~10 cm off in the exported model | The Blender glTF importer leaves an imported action **active**; its pose contaminates the armature-modifier and rest-pose bakes | Converter detaches the action, mutes NLA, resets the pose before any bake |
| 5 | Some expressions import as "0 frames, empty animation" | Single-frame held poses export as zero-length FBX takes; Unity drops them | Converter pads them into two-key rest→pose ramps |
| 6 | Expressions do nothing in-game although the menu works | **Humanoid** clip import converts all bone animation to muscle curves, discarding face-bone motion | Keep a second copy of the FBX imported as **Generic** (`avatar_anims.fbx`) purely as the clip source — the setup script manages this |
| 7 | Body freezes/T-poses while an expression plays; avatar looks moody or "angry" | (a) FBX bake keys **every** bone in every take — playing raw clips in FX writes body transforms; (b) Standard shader lighting crushes fullbright pixel-art | (a) setup script strips curves to only those that move or sit off-rest; (b) use **Unlit/Transparent Cutout** |
| 8 | 1-pixel face details render as smeared/wrong colors | Default DXT texture compression works on 4×4 blocks | Texture Compression: **None** (also Filter: Point) |
| 9 | Eyes/brows slowly drift upward between blinks; no blinking in-game at all | (a) Generated pause keyframes without flat tangents make the spline bow; (b) Unity forces animator layer 0 to weight 1 in-editor but **VRChat honors the serialized weight** | Setup script writes flat tangents and sets layer 0 `defaultWeight = 1` explicitly |
| 10 | Resting face reads "angry" although the model data is flat | Flat brow bars sat level with the round glasses lens tops, which render in front and clip them into diagonal wedges | `LIFT_PIXELS`/`--lift` raises the brow geometry+bones half a pixel |

## What intentionally does NOT carry over from Figura

- Lua scripting (SquAPI, EZAnims, equipment-reactive pivots): VRChat has no
  user scripting. Swinging physics → PhysBones; toggles/expressions → FX
  animator layers + the radial menu; anything reacting to game state has no
  equivalent.
- Vanilla-model layering tricks (armor/elytra/held-item pivots) remain as
  plain bones you can animate or hang props on, but nothing drives them
  automatically.

## Optional polish (not done here)

- PhysBone **colliders** on Head/Chest so hair doesn't clip the body; angle
  **limits** so it can't swing through the face.
- Quest/Android build: re-shader with `VRChat/Mobile/Standard Lite` (supports
  cutout) and publish for the Android platform from the same project.
