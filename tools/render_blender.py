# -*- coding: utf-8 -*-
"""
用 Blender (Cycles) 渲染 FreeCAD 船模

用法 (一般 python3 即可，會自動呼叫 FreeCAD 與 Blender):
    python3 tools/render_blender.py try-2/GlobalUnmannedCat.FCStd
    python3 tools/render_blender.py try-1/GlobalUnmannedShip.FCStd --scenes studio --views hero,side --samples 64
    python3 tools/render_blender.py try-2/GlobalUnmannedCat.FCStd --sail-logo my_logo.png --logo-height 0.5

流程:
  1. freecadcmd 執行本檔 (export 模式): 每個 Body 網格化 → OBJ + manifest.json
  2. Blender 背景執行本檔 (render 模式): 匯入網格、套材質、建場景、Cycles 算圖
  3. 輸出 PNG 到 <FCStd 所在資料夾>/renders/

場景:
  ocean  — 天空 + 海浪，船體水線以下為防污漆
  studio — 攝影棚燈光 + 陰影承接地面，可看見水下附屬件 (吊艙、螺旋槳、舵…)

本檔會在三種直譯器中執行，依可 import 的模組判斷模式：bpy → render，FreeCAD → export，其他 → CLI。
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_FREECAD = "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
DEFAULT_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
DEFAULT_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "sail_logo.png")

# 視角: (方位角 deg，0 = 從船艏方向看、90 = 從左舷看；仰角 deg；
#        對焦高度 = 船模高度的比例；距離係數)
VIEWS = {
    "hero": (35, 14, 0.42, 0.85),
    "stern": (205, 12, 0.42, 0.85),
    "side": (90, 2, 0.45, 0.85),
    "aerial": (55, 48, 0.3, 0.85),
    "below": (-40, -16, 0.12, 0.55),  # 僅 studio 場景 (隱藏地面，看水下附屬件)
}

# 依 Body 名稱前綴決定材質
MATERIALS = {
    "Hull": "hull",
    "WingSail": "wing",
    "WindTurbine": "turbine",
    "Propeller": "bronze",
    "SolarArray": "solar",
    "ElectricPod": "graphite",
    "Rudder": "graphite",
    "Dagger": "graphite",
}


# ---------------------------------------------------------------------------
# 1. CLI (一般 python)
# ---------------------------------------------------------------------------
def find_exe(preferred, *names):
    if os.path.exists(preferred):
        return preferred
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    sys.exit(f"找不到執行檔: {preferred} / {names}，請用 --freecad / --blender 指定")


def cli_main():
    ap = argparse.ArgumentParser(description="用 Blender 渲染 FreeCAD 船模")
    ap.add_argument("fcstd", help="FreeCAD 檔案 (.FCStd)")
    ap.add_argument("--out", help="輸出資料夾 (預設: FCStd 旁的 renders/)")
    ap.add_argument("--scenes", default="ocean,studio", help="ocean,studio")
    ap.add_argument("--views", default="hero,stern,side,below", help=",".join(VIEWS))
    ap.add_argument("--samples", type=int, default=128)
    ap.add_argument("--res", default="1920x1080", help="寬x高")
    ap.add_argument("--wave", type=float, default=0.06, help="海浪尺度 (m)")
    ap.add_argument("--freecad", default=DEFAULT_FREECAD)
    ap.add_argument("--blender", default=DEFAULT_BLENDER)
    ap.add_argument("--keep-blend", action="store_true", help="另存 .blend 場景檔供手動調整")
    ap.add_argument("--sail-logo", default=DEFAULT_LOGO, help="翼帆標誌圖 (PNG，可含透明)；none = 不貼")
    ap.add_argument("--logo-height", type=float, default=0.32, help="標誌中心高度 (翼帆展長比例，0 = 根部)")
    ap.add_argument("--logo-scale", type=float, default=0.68, help="標誌寬度 (該高度弦長的比例)")
    a = ap.parse_args()

    logo = None
    if a.sail_logo and a.sail_logo.lower() != "none":
        logo = os.path.abspath(a.sail_logo)
        if not os.path.exists(logo):
            sys.exit(f"找不到標誌圖 {logo}")

    fcstd = os.path.abspath(a.fcstd)
    if not os.path.exists(fcstd):
        sys.exit(f"找不到 {fcstd}")
    out = os.path.abspath(a.out or os.path.join(os.path.dirname(fcstd), "renders"))
    os.makedirs(out, exist_ok=True)
    freecad = find_exe(a.freecad, "freecadcmd", "FreeCADCmd")
    blender = find_exe(a.blender, "blender")
    me = os.path.abspath(__file__)
    w, h = (int(v) for v in a.res.lower().split("x"))

    with tempfile.TemporaryDirectory(prefix="ship_render_") as tmp:
        print(f"[1/2] FreeCAD 匯出網格 ← {fcstd}")
        env = dict(os.environ, RB_FCSTD=fcstd, RB_EXPORT_DIR=tmp)
        r = subprocess.run([freecad, me], env=env, capture_output=True, text=True)
        manifest = os.path.join(tmp, "manifest.json")
        if not os.path.exists(manifest):
            sys.exit("FreeCAD 匯出失敗:\n" + r.stdout[-3000:] + r.stderr[-3000:])
        for line in r.stdout.splitlines():
            if line.startswith("  "):
                print(line)

        cfg = {
            "manifest": manifest,
            "out": out,
            "stem": os.path.splitext(os.path.basename(fcstd))[0],
            "scenes": [s for s in a.scenes.split(",") if s],
            "views": [v for v in a.views.split(",") if v],
            "samples": a.samples,
            "res": [w, h],
            "wave": a.wave,
            "keep_blend": a.keep_blend,
            "sail_logo": logo,
            "logo_height": a.logo_height,
            "logo_scale": a.logo_scale,
        }
        cfg_path = os.path.join(tmp, "config.json")
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)

        print(f"[2/2] Blender 渲染 → {out}")
        r = subprocess.run([blender, "-b", "--factory-startup", "-P", me, "--", cfg_path])
        if r.returncode != 0:
            sys.exit("Blender 渲染失敗")
    print("完成")


# ---------------------------------------------------------------------------
# 2. FreeCAD export (freecadcmd)
# ---------------------------------------------------------------------------
def freecad_export():
    import FreeCAD as App
    import MeshPart

    fcstd = os.environ["RB_FCSTD"]
    out = os.environ["RB_EXPORT_DIR"]
    doc = App.openDocument(fcstd)
    doc.recompute()
    items = []
    for o in doc.Objects:
        if o.TypeId != "PartDesign::Body" or o.Shape.isNull() or not o.Shape.Solids:
            continue
        mesh = MeshPart.meshFromShape(Shape=o.Shape, LinearDeflection=0.3,
                                      AngularDeflection=math.radians(8), Relative=False)
        path = os.path.join(out, f"{o.Name}.obj")
        mesh.write(path)
        items.append({"name": o.Name, "label": o.Label, "file": path})
        print(f"  {o.Label:14s} {mesh.CountFacets:7d} 三角面")
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(items, f)


# ---------------------------------------------------------------------------
# 3. Blender render (bpy)
# ---------------------------------------------------------------------------
def blender_main():
    import bmesh
    import bpy
    from mathutils import Vector

    cfg = json.load(open(sys.argv[sys.argv.index("--") + 1]))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    # ---- 材質 ------------------------------------------------------------
    def principled(name, color, rough=0.4, metal=0.0, coat=0.0):
        m = bpy.data.materials.new(name)
        if bpy.app.version < (5, 0, 0):
            m.use_nodes = True
        b = m.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = (*color, 1)
        b.inputs["Roughness"].default_value = rough
        b.inputs["Metallic"].default_value = metal
        b.inputs["Coat Weight"].default_value = coat
        return m, b

    def hull_material():
        # 依世界座標 Z 分色: 水線以下防污漆、水線帶、乾舷白色膠衣
        m, b = principled("Hull", (0.9, 0.9, 0.92), rough=0.22, coat=0.6)
        nt = m.node_tree
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.interpolation = "CONSTANT"
        el = ramp.color_ramp.elements
        el[0].position, el[0].color = 0.0, (0.035, 0.05, 0.09, 1)   # 防污漆 (深藍)
        el[1].position, el[1].color = 0.52, (0.85, 0.3, 0.05, 1)    # 水線帶 (橘)
        el.new(0.56).color = (0.9, 0.9, 0.92, 1)                    # 膠衣 (白)
        # Z: -0.5 m → 0, +0.5 m → 1；水線 Z=0.02 m 處 = 0.52
        mapr = nt.nodes.new("ShaderNodeMapRange")
        mapr.inputs["From Min"].default_value = -0.5
        mapr.inputs["From Max"].default_value = 0.5
        nt.links.new(geo.outputs["Position"], sep.inputs[0])
        nt.links.new(sep.outputs["Z"], mapr.inputs["Value"])
        nt.links.new(mapr.outputs["Result"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], b.inputs["Base Color"])
        return m

    def solar_material():
        # 深藍電池片 + 銀色格線 (Brick texture)
        m, b = principled("Solar", (0.02, 0.04, 0.12), rough=0.08, coat=1.0)
        nt = m.node_tree
        tc = nt.nodes.new("ShaderNodeTexCoord")
        brick = nt.nodes.new("ShaderNodeTexBrick")
        brick.offset = 0.0
        brick.inputs["Color1"].default_value = (0.015, 0.03, 0.10, 1)
        brick.inputs["Color2"].default_value = (0.02, 0.04, 0.13, 1)
        brick.inputs["Mortar"].default_value = (0.55, 0.58, 0.62, 1)
        brick.inputs["Scale"].default_value = 6.4  # 約 156 mm 電池片
        brick.inputs["Mortar Size"].default_value = 0.012
        brick.inputs["Brick Width"].default_value = 1.0
        brick.inputs["Row Height"].default_value = 0.5
        nt.links.new(tc.outputs["Object"], brick.inputs["Vector"])
        nt.links.new(brick.outputs["Color"], b.inputs["Base Color"])
        return m

    mats = {
        "hull": hull_material(),
        "wing": principled("Wing", (0.95, 0.33, 0.04), rough=0.35, coat=0.3)[0],
        "turbine": principled("Turbine", (0.12, 0.35, 0.7), rough=0.3, metal=0.3)[0],
        "bronze": principled("Bronze", (0.85, 0.55, 0.25), rough=0.22, metal=1.0)[0],
        "solar": solar_material(),
        "graphite": principled("Graphite", (0.04, 0.04, 0.045), rough=0.35, coat=0.2)[0],
        "default": principled("Default", (0.7, 0.7, 0.7), rough=0.4)[0],
    }

    # ---- 匯入船模 (FreeCAD mm → Blender m，座標軸不轉換) --------------------
    ship = []
    for it in json.load(open(cfg["manifest"])):
        bpy.ops.wm.obj_import(filepath=it["file"], forward_axis="Y", up_axis="Z", global_scale=0.001)
        ob = bpy.context.selected_objects[0]
        ob.name = it["label"]
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
        bm.to_mesh(ob.data)
        bm.free()
        bpy.ops.object.select_all(action="DESELECT")
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(30))  # 保留舭線、龍骨等銳邊
        key = next((v for k, v in MATERIALS.items() if it["name"].startswith(k)), "default")
        ob.data.materials.clear()
        ob.data.materials.append(mats[key])
        ship.append(ob)

    # ---- 翼帆標誌 ---------------------------------------------------------
    def apply_sail_logo(ob, path):
        """以翼帆弦線方向做平面投影貼圖；依面法向判斷左右舷，讓兩側文字都不鏡像"""
        vs = [ob.matrix_world @ v.co for v in ob.data.vertices]
        levels = {}
        for v in vs:
            levels.setdefault(round(v.z, 3), []).append(v)

        def chord_ends(pts):
            # 水平主軸方向上的兩端點 = 前緣 / 尾緣
            cx = sum(p.x for p in pts) / len(pts)
            cy = sum(p.y for p in pts) / len(pts)
            sxx = sum((p.x - cx) ** 2 for p in pts)
            syy = sum((p.y - cy) ** 2 for p in pts)
            sxy = sum((p.x - cx) * (p.y - cy) for p in pts)
            ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
            axis = Vector((math.cos(ang), math.sin(ang), 0))
            proj = [((p - Vector((cx, cy, p.z))).dot(axis), p) for p in pts]
            a_, b_ = min(proj, key=lambda t: t[0])[1], max(proj, key=lambda t: t[0])[1]
            return (a_, b_) if a_.x >= b_.x else (b_, a_)  # (前緣=靠船艏, 尾緣)

        spans = {z: chord_ends(p) for z, p in levels.items() if len(p) >= 4}
        spans = {z: e for z, e in spans.items() if (e[0] - e[1]).xy.length > 0}
        big = max((e[0] - e[1]).xy.length for e in spans.values())
        wing_z = sorted(z for z, e in spans.items() if (e[0] - e[1]).xy.length > 0.5 * big)
        (le0, te0), (le1, te1) = spans[wing_z[0]], spans[wing_z[-1]]
        t = cfg["logo_height"]
        z0 = wing_z[0] + t * (wing_z[-1] - wing_z[0])
        le, te = le0.lerp(le1, t), te0.lerp(te1, t)
        d = (le - te).xy.to_3d().normalized()        # 弦向 (指向前緣)
        n = Vector((0, 0, 1)).cross(d)               # 翼面法向 (左舷側)
        c = Vector(((le.x + te.x) / 2, (le.y + te.y) / 2, z0))
        size = cfg["logo_scale"] * (le - te).xy.length
        print(f"sail logo: center {tuple(round(v, 2) for v in c)}, size {size:.2f} m")

        nt = ob.data.materials[0].node_tree
        bsdf = nt.nodes["Principled BSDF"]
        base = tuple(bsdf.inputs["Base Color"].default_value)
        geo = nt.nodes.new("ShaderNodeNewGeometry")

        def vmath(op, a_, b_=None):
            nd = nt.nodes.new("ShaderNodeVectorMath")
            nd.operation = op
            for i, src in enumerate((a_, b_)):
                if src is None:
                    continue
                if isinstance(src, Vector):
                    nd.inputs[i].default_value = src
                else:
                    nt.links.new(src, nd.inputs[i])
            return nd.outputs["Value"] if op == "DOT_PRODUCT" else nd.outputs["Vector"]

        def math_(op, a_, b_=None):
            nd = nt.nodes.new("ShaderNodeMath")
            nd.operation = op
            for i, src in enumerate((a_, b_)):
                if src is None:
                    continue
                if isinstance(src, (int, float)):
                    nd.inputs[i].default_value = src
                else:
                    nt.links.new(src, nd.inputs[i])
            return nd.outputs[0]

        rel = vmath("SUBTRACT", geo.outputs["Position"], c)
        side = math_("SIGN", vmath("DOT_PRODUCT", geo.outputs["Normal"], n))
        # u = -side·(rel·d)/size + 0.5 ；v = rel.z/size + 0.5
        u = math_("ADD", math_("MULTIPLY", math_("MULTIPLY", vmath("DOT_PRODUCT", rel, d), side), -1 / size), 0.5)
        v = math_("ADD", math_("MULTIPLY", vmath("DOT_PRODUCT", rel, Vector((0, 0, 1))), 1 / size), 0.5)
        uv = nt.nodes.new("ShaderNodeCombineXYZ")
        nt.links.new(u, uv.inputs["X"])
        nt.links.new(v, uv.inputs["Y"])
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(path)
        tex.extension = "CLIP"
        tex.interpolation = "Cubic"
        nt.links.new(uv.outputs["Vector"], tex.inputs["Vector"])
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        sock = lambda coll, name: next(s_ for s_ in coll if s_.name == name and s_.type == "RGBA")
        nt.links.new(tex.outputs["Alpha"], mix.inputs["Factor"])
        sock(mix.inputs, "A").default_value = base
        nt.links.new(tex.outputs["Color"], sock(mix.inputs, "B"))
        nt.links.new(sock(mix.outputs, "Result"), bsdf.inputs["Base Color"])

    wing_ob = next((ob for ob in ship if ob.name.startswith("WingSail")), None)
    if cfg.get("sail_logo") and wing_ob:
        apply_sail_logo(wing_ob, cfg["sail_logo"])

    pts = [ob.matrix_world @ Vector(c) for ob in ship for c in ob.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    center = (lo + hi) / 2
    radius = (hi - lo).length / 2
    print(f"ship bbox {tuple(round(v, 2) for v in lo)} – {tuple(round(v, 2) for v in hi)}")

    # ---- 場景元件 --------------------------------------------------------
    def collection(name):
        c = bpy.data.collections.new(name)
        scene.collection.children.link(c)
        return c

    def link(ob, coll):
        for c in ob.users_collection:
            c.objects.unlink(ob)
        coll.objects.link(ob)

    def build_ocean():
        coll = collection("Ocean")
        bpy.ops.mesh.primitive_plane_add(size=1)
        ocean = bpy.context.active_object
        ocean.name = "Sea"
        mod = ocean.modifiers.new("Ocean", "OCEAN")
        mod.geometry_mode = "GENERATE"
        mod.spatial_size = 30
        mod.repeat_x = mod.repeat_y = 8
        mod.resolution = 14
        mod.viewport_resolution = 6
        mod.wave_scale = cfg["wave"]
        mod.wind_velocity = 8
        mod.choppiness = 0.9
        mod.random_seed = 7
        ocean.location = (center.x - 120, center.y - 120, 0)  # Ocean 網格由原點往 +X/+Y 生成
        m, b = principled("Water", (0.02, 0.16, 0.18), rough=0.03)
        b.inputs["IOR"].default_value = 1.333
        b.inputs["Transmission Weight"].default_value = 1.0
        ocean.data.materials.append(m)
        link(ocean, coll)
        # 深水底面: 讓透射光呈現深海藍綠色
        bpy.ops.mesh.primitive_plane_add(size=600, location=(center.x, center.y, -3))
        floor = bpy.context.active_object
        floor.name = "DeepWater"
        fm, fb = principled("DeepWater", (0.0, 0.03, 0.045), rough=1.0)
        floor.data.materials.append(fm)
        link(floor, coll)
        return coll, None

    def sky_world():
        w = bpy.data.worlds.new("Sky")
        if bpy.app.version < (5, 0, 0):
            w.use_nodes = True
        nt = w.node_tree
        sky = nt.nodes.new("ShaderNodeTexSky")
        for t in ("MULTIPLE_SCATTERING", "NISHITA", "SINGLE_SCATTERING", "HOSEK_WILKIE"):
            try:
                sky.sky_type = t
                break
            except TypeError:
                continue
        if hasattr(sky, "sun_elevation"):
            sky.sun_elevation = math.radians(22)
            sky.sun_rotation = math.radians(70)
        bg = nt.nodes["Background"]
        bg.inputs["Strength"].default_value = 0.12
        nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
        return w

    def build_studio():
        coll = collection("Studio")
        bpy.ops.mesh.primitive_plane_add(size=200, location=(center.x, center.y, lo.z - 0.002))
        floor = bpy.context.active_object
        floor.name = "ShadowCatcher"
        floor.is_shadow_catcher = True
        link(floor, coll)
        s = radius
        for name, loc, energy, size in (
            ("Key", (center.x + 2.2 * s, center.y + 1.6 * s, center.z + 2.4 * s), 160, 2.5),
            ("Fill", (center.x - 0.8 * s, center.y - 2.4 * s, center.z + 1.2 * s), 50, 3.0),
            ("Rim", (center.x - 2.6 * s, center.y + 0.6 * s, center.z + 1.8 * s), 110, 1.5),
            ("Under", (center.x + 1.5 * s, center.y - 1.0 * s, lo.z - 0.6 * s), 40, 2.0),
        ):
            ld = bpy.data.lights.new(name, "AREA")
            ld.energy = energy * s * s
            ld.size = size * s
            lo_ = bpy.data.objects.new(name, ld)
            lo_.location = loc
            direction = center - Vector(loc)
            lo_.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
            coll.objects.link(lo_)
        return coll, floor

    def studio_world():
        # 依視線方向 Z 做上下漸層的淺灰背景
        w = bpy.data.worlds.new("Studio")
        if bpy.app.version < (5, 0, 0):
            w.use_nodes = True
        nt = w.node_tree
        tc = nt.nodes.new("ShaderNodeTexCoord")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].position = 0.45
        ramp.color_ramp.elements[0].color = (0.78, 0.8, 0.83, 1)
        ramp.color_ramp.elements[1].position = 1.0
        ramp.color_ramp.elements[1].color = (0.42, 0.46, 0.52, 1)
        mapr = nt.nodes.new("ShaderNodeMapRange")
        mapr.inputs["From Min"].default_value = -1
        nt.links.new(tc.outputs["Generated"], sep.inputs[0])
        nt.links.new(sep.outputs["Z"], mapr.inputs["Value"])
        nt.links.new(mapr.outputs["Result"], ramp.inputs["Fac"])
        bg = nt.nodes["Background"]
        nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 0.35
        return w

    # ---- 相機 ------------------------------------------------------------
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 50
    cam_data.clip_end = 2000
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    def place_camera(az, el, z_frac, dist_k, min_z=None):
        w, h = cfg["res"]
        fov_v = 2 * math.atan(18.0 / cam_data.lens * h / w if w >= h else 18.0 / cam_data.lens)
        dist = radius / math.sin(fov_v / 2) * dist_k
        a, e = math.radians(az), math.radians(el)
        target = Vector((center.x, center.y, lo.z + z_frac * (hi.z - lo.z)))
        pos = target + dist * Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
        if min_z is not None and pos.z < min_z:
            pos.z = min_z
        cam.location = pos
        cam.rotation_euler = (target - pos).to_track_quat("-Z", "Y").to_euler()

    # ---- 算圖設定 --------------------------------------------------------
    scene.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for dev_type in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            prefs.compute_device_type = dev_type
            prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type != "CPU"]
            if gpus:
                for d in prefs.devices:
                    d.use = True
                scene.cycles.device = "GPU"
                print(f"Cycles device: {dev_type} {[d.name for d in gpus]}")
                break
        except TypeError:
            continue
    scene.cycles.samples = cfg["samples"]
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 12
    scene.cycles.transmission_bounces = 12
    scene.cycles.glossy_bounces = 6
    scene.render.resolution_x, scene.render.resolution_y = cfg["res"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "AgX"
    try:
        scene.view_settings.look = "AgX - Punchy"
    except TypeError:
        pass

    envs = {}
    if "ocean" in cfg["scenes"]:
        envs["ocean"] = (build_ocean(), sky_world())
    if "studio" in cfg["scenes"]:
        envs["studio"] = (build_studio(), studio_world())

    for sname, ((coll, floor), world) in envs.items():
        for other, ((c, _), _) in envs.items():
            c.hide_render = c.hide_viewport = other != sname
        scene.world = world
        scene.view_settings.exposure = -0.6 if sname == "ocean" else -0.4
        for v in cfg["views"]:
            if v not in VIEWS or (sname == "ocean" and v == "below"):
                continue
            if floor:
                floor.hide_render = v == "below"
            az, el, z_frac, dist_k = VIEWS[v]
            place_camera(az, el, z_frac, dist_k, min_z=0.35 if sname == "ocean" else None)
            path = os.path.join(cfg["out"], f"{cfg['stem']}_{sname}_{v}.png")
            scene.render.filepath = path
            print(f"render {sname}/{v} → {path}", flush=True)
            bpy.ops.render.render(write_still=True)

    if cfg.get("keep_blend"):
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(cfg["out"], f"{cfg['stem']}.blend"))


def main():
    try:
        import bpy  # noqa: F401
        return blender_main()
    except ImportError:
        pass
    if os.environ.get("RB_EXPORT_DIR"):
        return freecad_export()
    return cli_main()


main()
