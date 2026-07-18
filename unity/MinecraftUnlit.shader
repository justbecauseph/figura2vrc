// Minecraft-style unlit cutout shader for converted Figura avatars.
//
// Minecraft is not purely unlit: every block/entity face gets a fixed
// brightness by its facing direction (top 1.0, north/south 0.8,
// east/west 0.6, bottom 0.5). That directional shading is what makes
// layered cubes (jacket/hat overlays, chest slopes) read as volume.
// Plain Unlit/Transparent Cutout flattens them; this shader restores it.
//
// Usage: put this file anywhere in Assets/, then set each avatar
// material's shader to "figura2vrc/MinecraftUnlit". PC avatars only
// (Quest restricts avatars to the VRChat/Mobile shader set).
Shader "figura2vrc/MinecraftUnlit"
{
    Properties
    {
        _MainTex ("Texture", 2D) = "white" {}
        _Cutoff ("Alpha Cutoff", Range(0, 1)) = 0.5
        _ShadeStrength ("Directional Shade Strength", Range(0, 1)) = 1.0
        // Double-sided by default: Blockbench authors routinely UV hidden
        // faces to transparent texture corners, relying on the editor's
        // double-sided preview — with backface culling those spots become
        // see-through holes.
        [Enum(UnityEngine.Rendering.CullMode)] _Cull ("Cull", Float) = 0
    }
    SubShader
    {
        Tags { "RenderType" = "TransparentCutout" "Queue" = "AlphaTest" }
        Cull [_Cull]

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            sampler2D _MainTex;
            float4 _MainTex_ST;
            fixed _Cutoff;
            fixed _ShadeStrength;

            struct appdata
            {
                float4 vertex : POSITION;
                float2 uv : TEXCOORD0;
                float3 normal : NORMAL;
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
                float3 wnormal : TEXCOORD1;
            };

            v2f vert(appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                o.wnormal = UnityObjectToWorldNormal(v.normal);
                return o;
            }

            fixed4 frag(v2f i) : SV_Target
            {
                fixed4 c = tex2D(_MainTex, i.uv);
                clip(c.a - _Cutoff);
                float3 n = normalize(i.wnormal);
                float up = saturate(n.y);
                float down = saturate(-n.y);
                // squared components sum to 1, blending the four face levels
                float shade = n.x * n.x * 0.6
                            + n.z * n.z * 0.8
                            + up * up * 1.0
                            + down * down * 0.5;
                c.rgb *= lerp(1.0, shade, _ShadeStrength);
                return c;
            }
            ENDCG
        }
    }
    Fallback "Unlit/Transparent Cutout"
}
