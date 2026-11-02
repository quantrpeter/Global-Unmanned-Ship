# -*- coding: utf-8 -*-
"""
環球無人船 try-2 — 滑行式雙體船 (Planing Catamaran) FreeCAD Part Design 參數化模型產生器

執行:
    /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd build_ship.py

產出 GlobalUnmannedCat.FCStd。所有實體皆為 PartDesign Body，每個特徵都由
完全約束 (fully constrained) 的 Sketch 驅動：
  * 外形尺寸 (寬度、高度、弦長、厚度…) → 直接在 Sketch 中雙擊具名約束修改
  * 全船佈置 (片體間距、站位、桅桿位置、帆角…) → Params 試算表

左右片體與成對附屬件只建右舷 (+Y) 一側，左舷由 Mirrored 鏡像產生：改一邊、兩邊同步。

座標系: X = 船長方向 (0 = 船尾 transom, +X 指向船艏), Y = 左舷, Z = 上 (Z=0 為靜止設計水線)
單位: mm
"""
import math
import os

import FreeCAD as App
import Part
import Sketcher

V = App.Vector
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "GlobalUnmannedCat.FCStd")

doc = App.newDocument("GlobalUnmannedCat")

# ---------------------------------------------------------------------------
# 全船佈置參數 (Spreadsheet)
# ---------------------------------------------------------------------------
PARAMS = [
    # alias, value, 說明
    ("LOA", "3000", "全長 Length over all"),
    ("Hull_Y", "600", "片體中心線離船中心距離 (片體間距的一半)"),
    ("BD_X0", "450", "橋甲板起點 X"),
    ("BD_L", "2100", "橋甲板長度"),
    ("BD_Z", "260", "橋甲板底部 (濕甲板) 高度"),
    ("BD_H", "130", "橋甲板厚度"),
    ("Deck_Z", "=BD_Z+BD_H", "甲板基準面高度 (DeckDatum = 橋甲板頂面)"),
    ("Wing_X", "=LOA*0.55", "翼帆轉軸 X (1/4 弦長處)"),
    ("Wing_RootZ", "470", "翼帆根部高度"),
    ("Wing_Height", "2400", "翼帆展長"),
    ("Wing_Angle", "0", "翼帆轉角 (deg)"),
    ("Mast_BaseZ", "280", "翼帆主桅底部"),
    ("Dagger_X", "=LOA*0.5", "穩向板 X"),
    ("Dagger_TopZ", "300", "穩向板頂部 (在片體內的板箱)"),
    ("Dagger_Depth", "700", "穩向板長度"),
    ("Dagger_Up", "0", "穩向板收起量 (高速滑行時收起，例如 450)"),
    ("VAWT_X", "=LOA*0.2167", "垂直軸風機 X"),
    ("VAWT_BaseZ", "300", "風機桅底部"),
    ("VAWT_MastH", "1250", "風機桅高度"),
    ("VAWT_R", "300", "風機轉子半徑"),
    ("VAWT_BladeZ", "800", "葉片底部高度"),
    ("VAWT_BladeH", "600", "葉片高度"),
    ("VAWT_ArmT", "12", "支臂厚度"),
    ("Pod_X", "=LOA*0.1733", "電力推進吊艙支架 X"),
    ("Pod_Z", "-250", "吊艙軸線 Z"),
    ("Strut_TopZ", "-40", "吊艙支架頂部 (埋入片體)"),
    ("Prop_X", "=Pod_X-140", "螺旋槳 X"),
    ("Prop_Pitch", "35", "槳葉螺距角 (deg)"),
    ("Rudder_X", "=LOA*0.04", "舵軸 X"),
    ("Rudder_TopZ", "-60", "舵頂部"),
    ("Rudder_Depth", "270", "舵深"),
    ("Rudder_Angle", "0", "舵角 (deg，兩舵同向)"),
    ("Solar_X0", "1000", "太陽能板起點 X"),
    ("Solar_L", "1450", "太陽能板長度"),
    ("Solar_T", "20", "太陽能板厚度"),
]

sheet = doc.addObject("Spreadsheet::Sheet", "Params")
sheet.set("A1", "Alias")
sheet.set("B1", "Value")
sheet.set("C1", "Description")
for i, (alias, value, desc) in enumerate(PARAMS, start=2):
    sheet.set(f"A{i}", alias)
    sheet.set(f"B{i}", value)
    sheet.set(f"C{i}", desc)
    sheet.setAlias(f"B{i}", alias)
doc.recompute()


# ---------------------------------------------------------------------------
# Sketch helpers
# ---------------------------------------------------------------------------
def plane(body, name):
    for f in body.Origin.OriginFeatures:
        if f.Role == name:
            return f
    raise KeyError(name)


def new_sketch(body, name, plane_name, offset=None, rot=None):
    """offset: {'x'|'y'|'z': expression}，為 sketch 局部座標的 AttachmentOffset"""
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(plane(body, plane_name), "")]
    sk.MapMode = "FlatFace"
    for axis, expr in (offset or {}).items():
        sk.setExpression(f".AttachmentOffset.Base.{axis}", expr)
    if rot:
        axis, expr = rot
        sk.AttachmentOffset = App.Placement(V(), App.Rotation(axis, 0))
        sk.setExpression(".AttachmentOffset.Rotation.Angle", expr)
    return sk


def con(sk, *args, name=None):
    if args[0] in ("DistanceX", "DistanceY") and len(args) == 6 and args[5] < 0:
        # 交換兩點使尺寸為正值，在 Sketcher 中編輯較直覺
        args = (args[0], args[3], args[4], args[1], args[2], -args[5])
    i = sk.addConstraint(Sketcher.Constraint(*args))
    if name:
        sk.renameConstraint(i, name)
    return i


def add_line(sk, p1, p2, construction=False):
    return sk.addGeometry(Part.LineSegment(V(*p1, 0), V(*p2, 0)), construction)


def add_arc3(sk, p1, pm, p2):
    """三點圓弧，回傳 (geo_id, p1 對應的端點編號, p2 對應的端點編號)"""
    cw = (pm[0] - p1[0]) * (p2[1] - pm[1]) - (pm[1] - p1[1]) * (p2[0] - pm[0]) < 0
    a, b = (p2, p1) if cw else (p1, p2)  # Sketcher 只接受逆時針 (+Z 法向) 圓弧
    g = sk.addGeometry(Part.Arc(V(*a, 0), V(*pm, 0), V(*b, 0)), False)
    sp = sk.Geometry[g].StartPoint
    if (sp - V(*p1, 0)).Length < 1e-6:
        return g, 1, 2
    return g, 2, 1


def foil(sk, chord, thick, nose_r, pivot, half=False, lead=1):
    """
    對稱翼型 (圓弧前緣 + 兩段大圓弧至尾緣)，完全約束。
    half=True → 只畫上半部 (供 Revolution 旋轉成流線型回轉體)
    lead=+1 前緣朝 +X (船艏)；pivot = 前緣到 sketch 原點 (轉軸) 的距離
    具名約束: Chord, HalfThickness, NoseRadius, Pivot
    """
    c, t, rn = chord, thick / 2.0, nose_r

    # 解出與前緣圓內切、通過尾緣、最大半厚度 = t 的大圓弧
    def big(R):
        xc = (c * c - 2 * R * rn) / (2 * (c - rn))
        yc = math.sqrt(max(R * R - (c - xc) ** 2, 0.0))
        return xc, yc

    def excess(R):  # 弧頂高度 - 目標半厚度；R 太小時無解 (回傳 +inf)
        xc, yc = big(R)
        return R - yc - t if R > abs(c - xc) else math.inf

    # R 由小往大掃描，找出第一個弧頂降到目標半厚度的區間，再二分
    lo = t
    hi = lo * 1.01
    while excess(hi) > 0:
        lo, hi = hi, hi * 1.01
    for _ in range(100):
        R = 0.5 * (lo + hi)
        lo, hi = (R, hi) if excess(R) > 0 else (lo, R)
    xc, yc = big(R)
    d = math.hypot(rn - xc, yc)
    tx, ty = xc + R * (rn - xc) / d, -yc + R * yc / d  # 切點 (上)

    def P(u, v):  # 弦向座標 u (由前緣起算) → sketch 座標
        return (-lead * (u - pivot), v)

    TE, LE, T_up, T_lo = P(c, 0), P(0, 0), P(tx, ty), P(tx, -ty)
    top = P(xc, t)
    na = math.atan2(ty, tx - rn)
    nose_mid_up = P(rn + rn * math.cos((na + math.pi) / 2), rn * math.sin((na + math.pi) / 2))

    up, up_te, up_t = add_arc3(sk, TE, top, T_up)
    if half:
        nose, nose_t, nose_le = add_arc3(sk, T_up, nose_mid_up, LE)
        chord_ln = add_line(sk, LE, TE)
        con(sk, "Coincident", nose, nose_le, chord_ln, 1)
    else:
        nose, nose_t, nose_tl = add_arc3(sk, T_up, LE, T_lo)
        lo_g, lo_t, lo_te = add_arc3(sk, T_lo, P(xc, -t), TE)
        chord_ln = add_line(sk, LE, TE, construction=True)
        con(sk, "Tangent", nose, nose_tl, lo_g, lo_t)
        con(sk, "Coincident", lo_g, lo_te, up, up_te)
        con(sk, "Equal", up, lo_g)
        con(sk, "PointOnObject", chord_ln, 1, nose)
    con(sk, "Tangent", nose, nose_t, up, up_t)
    con(sk, "Coincident", chord_ln, 2, up, up_te)
    con(sk, "PointOnObject", nose, 3, -1)
    con(sk, "PointOnObject", chord_ln, 1, -1)
    con(sk, "PointOnObject", chord_ln, 2, -1)
    # 最大厚度點 (construction point，位於上弧正上方)
    pt = sk.addGeometry(Part.Point(V(*top, 0)), True)
    con(sk, "PointOnObject", pt, 1, up)
    con(sk, "Vertical", pt, 1, up, 3)
    con(sk, "DistanceY", -1, 1, pt, 1, t, name="HalfThickness")
    con(sk, "Radius", nose, rn, name="NoseRadius")
    con(sk, "DistanceX", chord_ln, 1, -1, 1, -LE[0], name="Pivot")
    con(sk, "DistanceX", chord_ln, 1, chord_ln, 2, TE[0] - LE[0], name="Chord")


def hull_section(sk, hb_deck, z_deck, hb_chine, z_chine, z_keel):
    """硬舭 (hard chine) 船體橫剖面，左右對稱，5 個具名尺寸"""
    pts = [(-hb_deck, z_deck), (hb_deck, z_deck), (hb_chine, z_chine), (0, z_keel), (-hb_chine, z_chine)]
    ln = [add_line(sk, pts[i], pts[(i + 1) % 5]) for i in range(5)]
    for i in range(5):
        con(sk, "Coincident", ln[i], 2, ln[(i + 1) % 5], 1)
    con(sk, "Symmetric", ln[0], 1, ln[0], 2, -2)
    con(sk, "Symmetric", ln[1], 2, ln[4], 1, -2)
    con(sk, "PointOnObject", ln[2], 2, -2)
    con(sk, "DistanceX", -1, 1, ln[0], 2, hb_deck, name="DeckHalfBeam")
    con(sk, "DistanceY", -1, 1, ln[0], 2, z_deck, name="DeckHeight")
    con(sk, "DistanceX", -1, 1, ln[1], 2, hb_chine, name="ChineHalfBeam")
    con(sk, "DistanceY", -1, 1, ln[1], 2, z_chine, name="ChineHeight")
    con(sk, "DistanceY", -1, 1, ln[2], 2, z_keel, name="KeelHeight")


def rect_x(sk, x0, length, width):
    """以 sketch X 軸為中心線對稱的矩形: Start, Length, Width"""
    h = width / 2.0
    p = [(x0, -h), (x0 + length, -h), (x0 + length, h), (x0, h)]
    ln = [add_line(sk, p[i], p[(i + 1) % 4]) for i in range(4)]
    for i in range(4):
        con(sk, "Coincident", ln[i], 2, ln[(i + 1) % 4], 1)
    con(sk, "Horizontal", ln[0])
    con(sk, "Horizontal", ln[2])
    con(sk, "Vertical", ln[1])
    con(sk, "Symmetric", ln[0], 1, ln[3], 1, -1)
    con(sk, "DistanceX", -1, 1, ln[0], 1, x0, name="Start")
    con(sk, "DistanceX", ln[0], 1, ln[0], 2, length, name="Length")
    con(sk, "DistanceY", ln[0], 1, ln[3], 1, width, name="Width")


def arm_y(sk, length, width):
    """由原點沿 sketch +Y 伸出、對 Y 軸對稱的支臂"""
    h = width / 2.0
    p = [(-h, 0), (h, 0), (h, length), (-h, length)]
    ln = [add_line(sk, p[i], p[(i + 1) % 4]) for i in range(4)]
    for i in range(4):
        con(sk, "Coincident", ln[i], 2, ln[(i + 1) % 4], 1)
    con(sk, "Horizontal", ln[2])
    con(sk, "Vertical", ln[1])
    con(sk, "Vertical", ln[3])
    con(sk, "Symmetric", ln[0], 1, ln[0], 2, -2)
    con(sk, "PointOnObject", ln[0], 1, -1)
    con(sk, "DistanceX", ln[0], 1, ln[0], 2, width, name="Width")
    con(sk, "DistanceY", ln[0], 1, ln[2], 1, length, name="Length")


def circle_at_origin(sk, diameter):
    g = sk.addGeometry(Part.Circle(V(), V(0, 0, 1), diameter / 2.0), False)
    con(sk, "Coincident", g, 3, -1, 1)
    con(sk, "Diameter", g, diameter, name="Diameter")


def blade_lens(sk, root_r, tip_r, root_w, edge_r):
    """螺旋槳葉片: 根部直線 + 兩段等半徑圓弧交於葉尖"""
    w = root_w / 2.0
    root = add_line(sk, (-w, root_r), (w, root_r))
    mid_y = 0.5 * (root_r + tip_r)
    bulge = 0.55 * w
    a1, a1_r, a1_t = add_arc3(sk, (w, root_r), (w + bulge, mid_y), (0, tip_r))
    a2, a2_t, a2_r = add_arc3(sk, (0, tip_r), (-w - bulge, mid_y), (-w, root_r))
    con(sk, "Coincident", root, 2, a1, a1_r)
    con(sk, "Coincident", a1, a1_t, a2, a2_t)
    con(sk, "Coincident", a2, a2_r, root, 1)
    con(sk, "Symmetric", root, 1, root, 2, -2)
    con(sk, "PointOnObject", a1, a1_t, -2)
    con(sk, "Equal", a1, a2)
    con(sk, "DistanceY", -1, 1, root, 1, root_r, name="RootRadius")
    con(sk, "DistanceY", -1, 1, a1, a1_t, tip_r, name="TipRadius")
    con(sk, "DistanceX", root, 1, root, 2, root_w, name="RootWidth")
    con(sk, "Radius", a1, edge_r, name="EdgeRadius")


def pad(body, name, sk, length_expr, reversed_=False, midplane=False):
    f = body.newObject("PartDesign::Pad", name)
    f.Profile = sk
    f.setExpression("Length", length_expr)
    f.Reversed = reversed_
    if midplane:
        f.SideType = "Symmetric"
    f.Refine = True
    sk.Visibility = False
    return f


def revolve(body, name, sk):
    f = body.newObject("PartDesign::Revolution", name)
    f.Profile = sk
    f.ReferenceAxis = (sk, ["H_Axis"])
    f.Angle = 360
    f.Refine = True
    sk.Visibility = False
    return f


def polar(body, name, originals, axis_ref, n):
    f = body.newObject("PartDesign::PolarPattern", name)
    f.Originals = originals
    f.Axis = axis_ref
    f.Angle = 360
    f.Occurrences = n
    f.Refine = True
    body.Tip = f
    for o in originals:
        o.Visibility = False
    return f




def mirror(body, name, originals):
    """以船中心面 (XZ_Plane) 鏡像，產生左舷 (-Y) 一側"""
    f = body.newObject("PartDesign::Mirrored", name)
    f.Originals = originals
    f.MirrorPlane = (plane(body, "XZ_Plane"), [""])
    f.Refine = True
    body.Tip = f
    for o in originals:
        o.Visibility = False
    return f


def hole_on_x_axis(sk, x, diameter):
    """圓孔，圓心在 sketch X 軸上: HoleX, HoleDiameter"""
    g = sk.addGeometry(Part.Circle(V(x, 0, 0), V(0, 0, 1), diameter / 2.0), False)
    con(sk, "PointOnObject", g, 3, -1)
    con(sk, "DistanceX", -1, 1, g, 3, x, name="HoleX")
    con(sk, "Diameter", g, diameter, name="HoleDiameter")


FOIL_DIMS = ("Chord", "HalfThickness", "NoseRadius", "Pivot")


def link_foil(sk, master):
    """sk 的翼型尺寸跟隨 master sketch (只需編輯 master)"""
    for n in FOIL_DIMS:
        sk.setExpression(f"Constraints.{n}", f"{master.Name}.Constraints.{n}")


# ---------------------------------------------------------------------------
# 1. Hull — 兩個深 V 滑行片體 + 橋甲板
# ---------------------------------------------------------------------------
hull = doc.addObject("PartDesign::Body", "Hull")

# 右舷片體站位: (LOA 比例, 甲板半寬, 甲板高, 舭半寬, 舭高, 龍骨高)
# 船尾段為等底升角 (~17.5°) 的平直滑行面，舭線低而銳利以利脫流；船艏底升角漸增以切浪
STATIONS = [
    (0.0000, 200, 380, 190, -50, -110),  # 船尾 transom
    (0.1667, 210, 385, 195, -50, -112),
    (0.3333, 210, 390, 195, -50, -115),
    (0.5000, 205, 400, 190, -45, -118),
    (0.6667, 190, 415, 165, -20, -115),
    (0.7778, 165, 428, 130, 15, -92),
    (0.8889, 115, 443, 75, 75, -40),
    (1.0000, 10, 460, 6, 300, 220),  # 船艏 stem
]
# 註: Loft 依斷面順序均勻參數化，同一段 loft 內的站距請保持相等，否則龍骨線會起伏
sections = []
for i, (frac, hbd, zd, hbc, zc, zk) in enumerate(STATIONS):
    sk = new_sketch(hull, f"Station{i}", "YZ_Plane", {"x": "Params.Hull_Y", "z": f"Params.LOA * {frac}"})
    hull_section(sk, hbd, zd, hbc, zc, zk)
    sections.append(sk)
doc.recompute()

# 分兩段放樣 (共用 Station4)：斷面太多時單一 loft 的高階插值會擺盪過衝；
# 分段點放在船艏斷面急遽變化之前，避免龍骨線下垂
lofts = []
for name, segs in (("HullAft", sections[:5]), ("HullFwd", sections[4:])):
    loft = hull.newObject("PartDesign::AdditiveLoft", name)
    loft.Profile = segs[0]
    loft.Sections = segs[1:]
    loft.Ruled = False
    loft.Refine = True
    lofts.append(loft)
for s in sections:
    s.Visibility = False

# 橋甲板跨越兩片體中心線，先建再鏡像片體，整個 Body 才是單一實體
sk = new_sketch(hull, "BridgeDeckSketch", "XY_Plane", {"z": "Params.BD_Z"})
rect_x(sk, 450, 2100, 1200)
sk.setExpression("Constraints.Start", "Params.BD_X0")
sk.setExpression("Constraints.Length", "Params.BD_L")
sk.setExpression("Constraints.Width", "2 * Params.Hull_Y")
pad(hull, "BridgeDeck", sk, "Params.BD_H")

mh = mirror(hull, "MirrorHull", lofts)

# 甲板基準面 = 橋甲板頂面 (平面，可直接在其上新增草圖)
deck = hull.newObject("PartDesign::Plane", "DeckDatum")
deck.AttachmentSupport = [(plane(hull, "XY_Plane"), "")]
deck.MapMode = "FlatFace"
deck.setExpression(".AttachmentOffset.Base.z", "Params.Deck_Z")
deck.ResizeMode = "Manual"
deck.setExpression("Length", "Params.LOA")
deck.setExpression("Width", "2 * Params.Hull_Y + 600")
hull.Tip = mh

# ---------------------------------------------------------------------------
# 2. WingSail — 剛性翼帆 (風力驅動) + 旋轉主桅，立於橋甲板中央
# ---------------------------------------------------------------------------
wing = doc.addObject("PartDesign::Body", "WingSail")

sk = new_sketch(wing, "MastSketch", "XY_Plane", {"x": "Params.Wing_X", "z": "Params.Mast_BaseZ"})
circle_at_origin(sk, 50)
pad(wing, "Mast", sk, "Params.Wing_RootZ + Params.Wing_Height + 60 - Params.Mast_BaseZ")

rot = (V(0, 0, 1), "Params.Wing_Angle * 1 deg")
root = new_sketch(wing, "WingRoot", "XY_Plane", {"x": "Params.Wing_X", "z": "Params.Wing_RootZ"}, rot)
foil(root, chord=700, thick=105, nose_r=24, pivot=175, lead=1)
tip = new_sketch(wing, "WingTip", "XY_Plane",
                 {"x": "Params.Wing_X", "z": "Params.Wing_RootZ + Params.Wing_Height"}, rot)
foil(tip, chord=420, thick=63, nose_r=15, pivot=105, lead=1)
doc.recompute()
wl = wing.newObject("PartDesign::AdditiveLoft", "WingLoft")
wl.Profile = root
wl.Sections = [tip]
wl.Ruled = True
wl.Refine = True
root.Visibility = tip.Visibility = False

# ---------------------------------------------------------------------------
# 3. WindTurbine — 垂直軸風力發電機 (H-Darrieus, 3 葉)
# ---------------------------------------------------------------------------
vawt = doc.addObject("PartDesign::Body", "WindTurbine")

mast_sk = new_sketch(vawt, "TurbineMastSketch", "XY_Plane", {"x": "Params.VAWT_X", "z": "Params.VAWT_BaseZ"})
circle_at_origin(mast_sk, 40)
pad(vawt, "TurbineMast", mast_sk, "Params.VAWT_MastH")

sk = new_sketch(vawt, "LowerArmSketch", "XY_Plane", {"x": "Params.VAWT_X", "z": "Params.VAWT_BladeZ + 40"})
arm_y(sk, 300, 20)
sk.setExpression("Constraints.Length", "Params.VAWT_R")
arm_lo = pad(vawt, "LowerArm", sk, "Params.VAWT_ArmT")

sk = new_sketch(vawt, "UpperArmSketch", "XY_Plane",
                {"x": "Params.VAWT_X", "z": "Params.VAWT_BladeZ + Params.VAWT_BladeH - 40 - Params.VAWT_ArmT"})
arm_y(sk, 300, 20)
sk.setExpression("Constraints.Length", "Params.VAWT_R")
arm_hi = pad(vawt, "UpperArm", sk, "Params.VAWT_ArmT")

sk = new_sketch(vawt, "TurbineBladeSketch", "XY_Plane",
                {"x": "Params.VAWT_X", "y": "Params.VAWT_R", "z": "Params.VAWT_BladeZ"})
foil(sk, chord=90, thick=16, nose_r=4, pivot=45, lead=1)
blade = pad(vawt, "TurbineBlade", sk, "Params.VAWT_BladeH")

polar(vawt, "RotorPattern", [arm_lo, arm_hi, blade], (mast_sk, ["N_Axis"]), 3)

# ---------------------------------------------------------------------------
# 4. Daggerboards — 可收放穩向板 (帆航抗橫漂；高速滑行時收起)
# ---------------------------------------------------------------------------
dag = doc.addObject("PartDesign::Body", "Daggerboards")
dag.AllowCompound = True
sk = new_sketch(dag, "DaggerSketch", "XY_Plane",
                {"x": "Params.Dagger_X", "y": "Params.Hull_Y", "z": "Params.Dagger_TopZ + Params.Dagger_Up"})
foil(sk, chord=200, thick=24, nose_r=6, pivot=100, lead=1)
db = pad(dag, "Daggerboard", sk, "Params.Dagger_Depth", reversed_=True)
mirror(dag, "MirrorDagger", [db])

# ---------------------------------------------------------------------------
# 5. ElectricPods — 雙電力推進吊艙 (每片體一具)
# ---------------------------------------------------------------------------
podb = doc.addObject("PartDesign::Body", "ElectricPods")
podb.AllowCompound = True

sk = new_sketch(podb, "StrutSketch", "XY_Plane",
                {"x": "Params.Pod_X", "y": "Params.Hull_Y", "z": "Params.Strut_TopZ"})
foil(sk, chord=120, thick=14, nose_r=3, pivot=60, lead=1)
strut = pad(podb, "Strut", sk, "Params.Strut_TopZ - Params.Pod_Z", reversed_=True)

# XZ_Plane 的法向為 -Y，所以 AttachmentOffset z = -Hull_Y 會落在 +Y 片體
sk = new_sketch(podb, "PodSketch", "XZ_Plane", {"x": "Params.Pod_X", "y": "Params.Pod_Z", "z": "-Params.Hull_Y"})
foil(sk, chord=300, thick=64, nose_r=22, pivot=130, half=True, lead=1)
pod = revolve(podb, "MotorPod", sk)
mirror(podb, "MirrorPod", [strut, pod])

# ---------------------------------------------------------------------------
# 6. Propellers — 雙 3 葉槳，左右鏡像 = 反向旋轉 (抵銷扭矩)
# ---------------------------------------------------------------------------
prop = doc.addObject("PartDesign::Body", "Propellers")
prop.AllowCompound = True

hub_sk = new_sketch(prop, "HubSketch", "XZ_Plane", {"x": "Params.Prop_X", "y": "Params.Pod_Z", "z": "-Params.Hull_Y"})
foil(hub_sk, chord=80, thick=40, nose_r=16, pivot=45, half=True, lead=1)
hub = revolve(prop, "Hub", hub_sk)
mirror(prop, "MirrorHub", [hub])

sk = new_sketch(prop, "PropBladeSketch", "YZ_Plane", {"x": "Params.Hull_Y", "y": "Params.Pod_Z", "z": "Params.Prop_X"},
                (V(0, 1, 0), "Params.Prop_Pitch * 1 deg"))
blade_lens(sk, root_r=10, tip_r=85, root_w=45, edge_r=85)
pb = pad(prop, "PropBlade", sk, "5", midplane=True)

# 槳轂先鏡像；槳葉再做環形陣列 ×3 + 鏡像 (MultiTransform)
mt = prop.newObject("PartDesign::MultiTransform", "PropPattern")
mt.Originals = [pb]
pp = doc.addObject("PartDesign::PolarPattern", "PropPolar")
pp.Axis = (hub_sk, ["H_Axis"])
pp.Angle = 360
pp.Occurrences = 3
pm = doc.addObject("PartDesign::Mirrored", "PropMirror")
pm.MirrorPlane = (plane(prop, "XZ_Plane"), [""])
prop.addObject(pp)
prop.addObject(pm)
mt.Transformations = [pp, pm]
mt.Refine = True
prop.Tip = mt
pb.Visibility = False

# ---------------------------------------------------------------------------
# 7. Rudders — 雙懸吊舵 (同向轉動，故不用鏡像；左舷尺寸跟隨右舷 sketch)
# ---------------------------------------------------------------------------
rud = doc.addObject("PartDesign::Body", "Rudders")
rud.AllowCompound = True
rrot = (V(0, 0, 1), "Params.Rudder_Angle * 1 deg")
sk_s = new_sketch(rud, "RudderSketch", "XY_Plane",
                  {"x": "Params.Rudder_X", "y": "Params.Hull_Y", "z": "Params.Rudder_TopZ"}, rrot)
foil(sk_s, chord=120, thick=12, nose_r=3, pivot=30, lead=1)
pad(rud, "RudderStbd", sk_s, "Params.Rudder_Depth", reversed_=True)
sk_p = new_sketch(rud, "RudderSketchPort", "XY_Plane",
                  {"x": "Params.Rudder_X", "y": "-Params.Hull_Y", "z": "Params.Rudder_TopZ"}, rrot)
foil(sk_p, chord=120, thick=12, nose_r=3, pivot=30, lead=1)
link_foil(sk_p, sk_s)
pad(rud, "RudderPort", sk_p, "Params.Rudder_Depth", reversed_=True)

# ---------------------------------------------------------------------------
# 8. SolarArray — 橋甲板太陽能板 (中間開孔讓翼帆主桅穿過)
# ---------------------------------------------------------------------------
sol = doc.addObject("PartDesign::Body", "SolarArray")
sk = new_sketch(sol, "SolarSketch", "XY_Plane", {"z": "Params.Deck_Z"})
rect_x(sk, 1000, 1450, 1000)
sk.setExpression("Constraints.Start", "Params.Solar_X0")
sk.setExpression("Constraints.Length", "Params.Solar_L")
hole_on_x_axis(sk, 1650, 90)
sk.setExpression("Constraints.HoleX", "Params.Wing_X")
pad(sol, "SolarPanel", sk, "Params.Solar_T")

doc.recompute()

# ---------------------------------------------------------------------------
# 檢查
# ---------------------------------------------------------------------------
ok = True
for o in doc.Objects:
    if o.TypeId == "Sketcher::SketchObject" and not o.FullyConstrained:
        print(f"!! {o.Name} NOT fully constrained")
        ok = False
    if "Invalid" in o.State or "Error" in o.State:
        print(f"!! {o.Name} state {o.State}")
        ok = False

PAIRED = {"Daggerboards", "ElectricPods", "Propellers", "Rudders"}
bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]
for b in bodies:
    s = b.Shape
    bb = s.BoundBox
    print(f"{b.Name:13s} solids={len(s.Solids)} valid={s.isValid()} vol={s.Volume / 1e9:7.4f} m3  "
          f"X[{bb.XMin:6.0f},{bb.XMax:6.0f}] Y[{bb.YMin:6.0f},{bb.YMax:6.0f}] Z[{bb.ZMin:6.0f},{bb.ZMax:6.0f}]")
    ok &= s.isValid() and (len(s.Solids) == 1 or b.Name in PAIRED)

# 靜水力粗估: 水線 (Z=0) 以下排水體積
below = Part.makeBox(10000, 10000, 10000, V(-5000, -5000, -10000))
wet = sum(b.Shape.common(below).Volume for b in bodies if b.Name != "WingSail")
print(f"Displaced volume @ Z=0: {wet / 1e9:.4f} m3  → displacement ≈ {wet / 1e6 * 1.025:.0f} kg (seawater)")

# 在 FreeCAD GUI 中以巨集執行時才有 ViewObject，可上色
if App.GuiUp:
    COLORS = {
        "Hull": (0.93, 0.93, 0.95), "WingSail": (0.95, 0.45, 0.10), "WindTurbine": (0.20, 0.55, 0.85),
        "Daggerboards": (0.35, 0.35, 0.38), "ElectricPods": (0.35, 0.35, 0.38), "Propellers": (0.80, 0.62, 0.20),
        "Rudders": (0.35, 0.35, 0.38), "SolarArray": (0.10, 0.15, 0.40),
    }
    for name, rgb in COLORS.items():
        doc.getObject(name).ViewObject.ShapeAppearance = [App.Material(DiffuseColor=rgb)]

doc.saveAs(OUT)
print("saved", OUT, "OK" if ok else "WITH ERRORS")
