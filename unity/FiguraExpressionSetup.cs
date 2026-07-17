// Figura -> VRChat expressions setup (canonical copy; see docs/CONVERSION.md).
//
// Copy this file into your Unity avatar project at Assets/Editor/, adjust the
// configuration block below, then run from the menu bar:
//   Tools > Figura Avatar > Setup Expressions
//
// Builds Assets/Expressions/ containing an FX animator controller (auto-blink
// layer, one-at-a-time face expressions, animated glasses/prop toggle), the
// VRC expressions menu + synced parameters, and wires everything into the
// VRC Avatar Descriptor found in the open scene. Safe to re-run at any time.
//
// Requires a Humanoid-imported avatar FBX at FbxPath whose animation takes
// were produced by convert_to_vrchat.py. A Generic-rig copy (AnimFbxPath) is
// created automatically and used as the clip source, because Humanoid import
// converts all bone animation to muscle curves and discards face-bone motion.

using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using VRC.SDK3.Dynamics.PhysBone.Components;

public static class FiguraExpressionSetup
{
    // ----------------------------------------------------------- config
    const string FbxPath = "Assets/avatar.fbx";
    const string AnimFbxPath = "Assets/avatar_anims.fbx";
    const string OutDir = "Assets/Expressions";

    // Blockbench animation names -> radial menu labels. Driven by an Int
    // parameter, one active at a time. Missing clips are skipped with a log.
    static readonly (string clip, string label)[] FaceExpressions =
    {
        ("angry", "Angry"), ("sad", "Sad"), ("surprise", "Surprise"),
        ("sleep", "Sleep"), ("droop", "Droop"),
    };

    // Looping auto-blink built from this clip; "" disables the blink layer.
    const string BlinkClip = "blink";
    const float BlinkPause = 6f; // seconds of open eyes between blinks

    // Animated two-state prop toggle (e.g. taking glasses off). The "off"
    // clip plays and holds when the bool parameter goes true; the "on" clip
    // plays once when it goes false. Set either to "" to disable the layer.
    const string PropOffClip = "GlassesOff";
    const string PropOnClip = "GlassesOn";
    const string PropParameter = "GlassesOff";
    const string PropMenuLabel = "Glasses Off";

    // ------------------------------------------------------------- entry
    [MenuItem("Tools/Figura Avatar/Setup Expressions")]
    public static void Run()
    {
        var desc = Object.FindObjectOfType<VRCAvatarDescriptor>();
        if (desc == null) { Debug.LogError("[Figura] No VRC Avatar Descriptor in the open scene."); return; }

        if (!EnsureGenericAnimFbx()) return;
        var clips = LoadFbxClips();

        var faces = FaceExpressions.Where(e => clips.ContainsKey(e.clip)).ToArray();
        foreach (var e in FaceExpressions.Except(faces))
            Debug.LogWarning($"[Figura] face clip '{e.clip}' not found in {AnimFbxPath}; skipping.");
        bool hasBlink = BlinkClip != "" && clips.ContainsKey(BlinkClip);
        bool hasProp = PropOffClip != "" && PropOnClip != ""
                       && clips.ContainsKey(PropOffClip) && clips.ContainsKey(PropOnClip);
        if (faces.Length == 0 && !hasBlink && !hasProp)
        { Debug.LogError("[Figura] no usable animation clips found — nothing to build."); return; }

        if (!AssetDatabase.IsValidFolder(OutDir))
            AssetDatabase.CreateFolder("Assets", "Expressions");

        // The FBX bake keys every bone in every take. Keep only curves that
        // matter, so the FX layer never writes body-skeleton transforms
        // (which would freeze locomotion).
        var use = new Dictionary<string, AnimationClip>();
        foreach (var key in faces.Select(f => f.clip)
                     .Concat(hasBlink ? new[] { BlinkClip } : System.Array.Empty<string>())
                     .Concat(hasProp ? new[] { PropOffClip, PropOnClip } : System.Array.Empty<string>()))
        {
            use[key] = StripStaticCurves(clips[key], key, desc.transform);
            if (key != BlinkClip) // blink is only a source for AutoBlink below
                AssetDatabase.CreateAsset(use[key], $"{OutDir}/{key}.anim");
        }

        var neutral = new AnimationClip { name = "Neutral" };
        AssetDatabase.CreateAsset(neutral, $"{OutDir}/Neutral.anim");
        AnimationClip autoBlink = null;
        if (hasBlink)
        {
            autoBlink = BuildAutoBlink(use[BlinkClip]);
            AssetDatabase.CreateAsset(autoBlink, $"{OutDir}/AutoBlink.anim");
        }

        var fx = BuildFxController(use, neutral, autoBlink, faces, hasProp);
        var pars = BuildParameters(faces.Length > 0, hasProp);
        var menu = BuildMenu(faces, hasProp);

        // Wire into the descriptor.
        desc.customizeAnimationLayers = true;
        var layers = desc.baseAnimationLayers;
        for (int i = 0; i < layers.Length; i++)
        {
            if (layers[i].type != VRCAvatarDescriptor.AnimLayerType.FX) continue;
            layers[i].isDefault = false;
            layers[i].isEnabled = true;
            layers[i].animatorController = fx;
        }
        desc.baseAnimationLayers = layers;
        desc.customExpressions = true;
        desc.expressionsMenu = menu;
        desc.expressionParameters = pars;

        // PhysBones own their transforms at runtime and ignore animation
        // unless Is Animated is set; enable it wherever our clips animate a
        // PhysBone root (e.g. an expression that flips the hair).
        var animatedBones = new HashSet<string>(
            use.Values.SelectMany(c => AnimationUtility.GetCurveBindings(c))
               .Select(b => b.path.Split('/').Last()));
        foreach (var pb in desc.GetComponentsInChildren<VRCPhysBone>(true))
        {
            if (animatedBones.Contains(pb.gameObject.name) && !pb.isAnimated)
            {
                pb.isAnimated = true;
                EditorUtility.SetDirty(pb);
                Debug.Log($"[Figura] Enabled Is Animated on {pb.gameObject.name} PhysBone.");
            }
        }

        EditorUtility.SetDirty(desc);
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(desc.gameObject.scene);
        AssetDatabase.SaveAssets();
        Debug.Log("[Figura] Expressions setup complete. Save the scene, then Build & Publish.");
    }

    // ------------------------------------------------------------ helpers
    static bool EnsureGenericAnimFbx()
    {
        if (AssetDatabase.LoadAssetAtPath<Object>(FbxPath) == null)
        { Debug.LogError($"[Figura] avatar FBX not found at {FbxPath}"); return false; }
        if (AssetDatabase.LoadAssetAtPath<Object>(AnimFbxPath) == null
            && !AssetDatabase.CopyAsset(FbxPath, AnimFbxPath))
        { Debug.LogError($"[Figura] could not copy {FbxPath} to {AnimFbxPath}"); return false; }
        var importer = (ModelImporter)AssetImporter.GetAtPath(AnimFbxPath);
        if (importer.animationType != ModelImporterAnimationType.Generic
            || importer.materialImportMode != ModelImporterMaterialImportMode.None)
        {
            importer.animationType = ModelImporterAnimationType.Generic;
            importer.materialImportMode = ModelImporterMaterialImportMode.None;
            importer.SaveAndReimport();
        }
        return true;
    }

    static Dictionary<string, AnimationClip> LoadFbxClips()
    {
        var clips = new Dictionary<string, AnimationClip>();
        foreach (var o in AssetDatabase.LoadAllAssetsAtPath(AnimFbxPath))
        {
            if (o is AnimationClip c && !c.name.StartsWith("__preview"))
                clips[c.name.Substring(c.name.LastIndexOf('|') + 1)] = c;
        }
        return clips;
    }

    // Keep only curves that move over the clip OR hold a value different from
    // the model's rest pose (held-pose clips never move, they just sit
    // off-rest from frame 0). Components are kept as whole groups so
    // quaternions stay intact.
    static AnimationClip StripStaticCurves(AnimationClip src, string name, Transform avatarRoot)
    {
        var dst = new AnimationClip { name = name, frameRate = src.frameRate };
        var bindings = AnimationUtility.GetCurveBindings(src);
        string GroupKey(EditorCurveBinding b)
        {
            int dot = b.propertyName.LastIndexOf('.');
            return b.path + "|" + (dot >= 0 ? b.propertyName.Substring(0, dot) : b.propertyName);
        }
        var moving = new HashSet<string>();
        foreach (var b in bindings)
        {
            var keys = AnimationUtility.GetEditorCurve(src, b).keys;
            float min = keys.Min(k => k.value), max = keys.Max(k => k.value);
            bool keep = max - min > 1e-4f;
            if (!keep)
            {
                var t = avatarRoot.Find(b.path);
                float rest = t != null ? RestValue(t, b.propertyName) : float.NaN;
                if (!float.IsNaN(rest))
                    keep = b.propertyName.StartsWith("localEulerAngles")
                        ? Mathf.Abs(Mathf.DeltaAngle(keys[0].value, rest)) > 0.5f
                        : Mathf.Abs(keys[0].value - rest) > 1e-3f;
            }
            if (keep) moving.Add(GroupKey(b));
        }
        foreach (var b in bindings)
        {
            if (moving.Contains(GroupKey(b)))
                AnimationUtility.SetEditorCurve(dst, b, AnimationUtility.GetEditorCurve(src, b));
        }
        if (moving.Count == 0)
            Debug.LogWarning($"[Figura] {name}: nothing moves in this clip after stripping.");
        return dst;
    }

    static float RestValue(Transform t, string prop) => prop switch
    {
        "m_LocalPosition.x" => t.localPosition.x,
        "m_LocalPosition.y" => t.localPosition.y,
        "m_LocalPosition.z" => t.localPosition.z,
        "m_LocalRotation.x" => t.localRotation.x,
        "m_LocalRotation.y" => t.localRotation.y,
        "m_LocalRotation.z" => t.localRotation.z,
        "m_LocalRotation.w" => t.localRotation.w,
        "localEulerAngles.x" or "localEulerAnglesRaw.x" => t.localEulerAngles.x,
        "localEulerAngles.y" or "localEulerAnglesRaw.y" => t.localEulerAngles.y,
        "localEulerAngles.z" or "localEulerAnglesRaw.z" => t.localEulerAngles.z,
        "m_LocalScale.x" => t.localScale.x,
        "m_LocalScale.y" => t.localScale.y,
        "m_LocalScale.z" => t.localScale.z,
        _ => float.NaN,
    };

    // Copy the blink clip's curves into a looping clip with a long pause
    // first, so the eyes idle open and blink every BlinkPause seconds.
    static AnimationClip BuildAutoBlink(AnimationClip src)
    {
        var dst = new AnimationClip { name = "AutoBlink", frameRate = src.frameRate };
        foreach (var binding in AnimationUtility.GetCurveBindings(src))
        {
            var srcCurve = AnimationUtility.GetEditorCurve(src, binding);
            // Flat tangents on both sides of the pause, or the spline bows and
            // the eyes drift between blinks.
            var keys = new List<Keyframe> { new Keyframe(0f, srcCurve.keys[0].value, 0f, 0f) };
            for (int i = 0; i < srcCurve.keys.Length; i++)
            {
                var shifted = srcCurve.keys[i];
                shifted.time += BlinkPause;
                if (i == 0) shifted.inTangent = 0f;
                if (i == srcCurve.keys.Length - 1) shifted.outTangent = 0f;
                keys.Add(shifted);
            }
            AnimationUtility.SetEditorCurve(dst, binding, new AnimationCurve(keys.ToArray()));
        }
        var settings = AnimationUtility.GetAnimationClipSettings(dst);
        settings.loopTime = true;
        AnimationUtility.SetAnimationClipSettings(dst, settings);
        return dst;
    }

    static AnimatorController BuildFxController(Dictionary<string, AnimationClip> clips,
                                                AnimationClip neutral, AnimationClip autoBlink,
                                                (string clip, string label)[] faces, bool hasProp)
    {
        string path = $"{OutDir}/FX.controller";
        AssetDatabase.DeleteAsset(path);
        var fx = AnimatorController.CreateAnimatorControllerAtPath(path);
        if (faces.Length > 0) fx.AddParameter("Face", AnimatorControllerParameterType.Int);
        if (hasProp) fx.AddParameter(PropParameter, AnimatorControllerParameterType.Bool);

        // Layer 0: auto-blink (or an empty base). Unity's editor forces layer
        // 0 to weight 1, but VRChat's runtime honors the serialized value —
        // leave it 0 and the layer dies in-game.
        var baseLayers = fx.layers;
        baseLayers[0].name = autoBlink != null ? "Blink" : "Base";
        baseLayers[0].defaultWeight = 1f;
        fx.layers = baseLayers;
        if (autoBlink != null)
        {
            var blinkState = fx.layers[0].stateMachine.AddState("AutoBlink");
            blinkState.motion = autoBlink;
            blinkState.writeDefaultValues = true;
        }

        // Face layer: Neutral + one state per expression, driven by the Face
        // int via AnyState. Sits above Blink so expressions that close the
        // eyes win over blinking.
        if (faces.Length > 0)
        {
            var faceSm = AddLayer(fx, "Face");
            var neutralState = faceSm.AddState("Neutral");
            neutralState.motion = neutral;
            neutralState.writeDefaultValues = true;
            faceSm.defaultState = neutralState;
            AddIntTransition(faceSm, neutralState, "Face", 0);
            for (int i = 0; i < faces.Length; i++)
            {
                var st = faceSm.AddState(faces[i].label);
                st.motion = clips[faces[i].clip];
                st.writeDefaultValues = true;
                AddIntTransition(faceSm, st, "Face", i + 1);
            }
        }

        // Prop toggle layer, same AnyState pattern: each state plays its
        // animation once and holds the final frame.
        if (hasProp)
        {
            var propSm = AddLayer(fx, "PropToggle");
            var idle = propSm.AddState("Idle"); // spawn landing spot only
            idle.motion = neutral;
            idle.writeDefaultValues = true;
            propSm.defaultState = idle;
            var off = propSm.AddState("Off");
            off.motion = clips[PropOffClip];
            off.writeDefaultValues = true;
            var on = propSm.AddState("On");
            on.motion = clips[PropOnClip];
            on.writeDefaultValues = true;

            var t = propSm.AddAnyStateTransition(off);
            t.AddCondition(AnimatorConditionMode.If, 0, PropParameter);
            t.hasExitTime = false; t.duration = 0f; t.canTransitionToSelf = false;
            t = propSm.AddAnyStateTransition(on);
            t.AddCondition(AnimatorConditionMode.IfNot, 0, PropParameter);
            t.hasExitTime = false; t.duration = 0f; t.canTransitionToSelf = false;
        }

        return fx;
    }

    static AnimatorStateMachine AddLayer(AnimatorController fx, string name)
    {
        var sm = new AnimatorStateMachine { name = name, hideFlags = HideFlags.HideInHierarchy };
        AssetDatabase.AddObjectToAsset(sm, fx);
        fx.AddLayer(new AnimatorControllerLayer { name = name, defaultWeight = 1f, stateMachine = sm });
        return sm;
    }

    static void AddIntTransition(AnimatorStateMachine sm, AnimatorState target, string param, int value)
    {
        var t = sm.AddAnyStateTransition(target);
        t.AddCondition(AnimatorConditionMode.Equals, value, param);
        t.hasExitTime = false;
        t.hasFixedDuration = true;
        t.duration = 0.1f;
        t.canTransitionToSelf = false;
    }

    static VRCExpressionParameters BuildParameters(bool hasFaces, bool hasProp)
    {
        var list = new List<VRCExpressionParameters.Parameter>();
        if (hasFaces)
            list.Add(new VRCExpressionParameters.Parameter
            {
                name = "Face", valueType = VRCExpressionParameters.ValueType.Int,
                saved = false, defaultValue = 0, networkSynced = true,
            });
        if (hasProp)
            list.Add(new VRCExpressionParameters.Parameter
            {
                name = PropParameter, valueType = VRCExpressionParameters.ValueType.Bool,
                saved = true, defaultValue = 0, networkSynced = true,
            });
        var pars = ScriptableObject.CreateInstance<VRCExpressionParameters>();
        pars.parameters = list.ToArray();
        AssetDatabase.CreateAsset(pars, $"{OutDir}/Parameters.asset");
        return pars;
    }

    static VRCExpressionsMenu BuildMenu((string clip, string label)[] faces, bool hasProp)
    {
        var menu = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
        if (hasProp)
            menu.controls.Add(new VRCExpressionsMenu.Control
            {
                name = PropMenuLabel,
                type = VRCExpressionsMenu.Control.ControlType.Toggle,
                parameter = new VRCExpressionsMenu.Control.Parameter { name = PropParameter },
                value = 1,
            });
        for (int i = 0; i < faces.Length; i++)
        {
            menu.controls.Add(new VRCExpressionsMenu.Control
            {
                name = faces[i].label,
                type = VRCExpressionsMenu.Control.ControlType.Toggle,
                parameter = new VRCExpressionsMenu.Control.Parameter { name = "Face" },
                value = i + 1,
            });
        }
        AssetDatabase.CreateAsset(menu, $"{OutDir}/Menu.asset");
        return menu;
    }
}
