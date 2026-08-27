import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from backend.database import get_db_connection
from backend.parser_engine import normalize_text
import backend.supabase_store as supabase_store
from backend.quote_memory import (
    quote_memory_description_key,
    quote_memory_item_description_lookup_key,
    quote_memory_item_description_only_lookup_key,
    quote_memory_lookup,
    quote_memory_candidate_rows,
    quote_memory_signature,
)
from backend.rag_engine import best_quote_memory_candidate


def _supabase_strict_enabled() -> bool:
    return getattr(supabase_store, "strict_enabled", lambda: True)()


def product_only_mode() -> bool:
    return os.getenv("PRODUCT_ONLY_MODE", "1").strip().lower() not in {"0", "false", "no", "off"}

DAMPER_CATEGORIES = {"FIRE_DAMPER", "MOTORIZED_DAMPER", "VOLUME_CONTROL_DAMPER", "BACK_DRAFT_DAMPER"}
DEFAULT_DAMPER_LENGTH_MM = {
    "FIRE_DAMPER": 250.0,
    "MOTORIZED_DAMPER": 250.0,
    "VOLUME_CONTROL_DAMPER": 200.0,
    "BACK_DRAFT_DAMPER": 200.0,
}
MFD_DEFAULT_SHEET_RATE = 3_000_000.0
MFD_DEFAULT_MOTOR_RATE = 6_000_000.0
FD_DEFAULT_SHEET_RATE = 3_000_000.0
FD_DEFAULT_ACCESSORY_RATE = 500_000.0
BACK_DRAFT_FIREPROOF_SHEET_RATE = 850_000.0
BACK_DRAFT_FIREPROOF_ACCESSORY_RATE = 300_000.0
OVAL_GRILLE_OBD_FRAME_RATE = 50_000.0
OVAL_GRILLE_OBD_BLADE_RATE = 13_000.0
OVAL_GRILLE_OBD_FACTOR = 1.7
STRAIGHT_DUCT_CATEGORIES = {"SUPPLY_DUCT", "RETURN_DUCT", "FRESH_AIR_DUCT", "EXHAUST_AIR_DUCT", "SMOKE_DUCT"}
DUCT_FITTING_CATEGORIES = STRAIGHT_DUCT_CATEGORIES | {
    "ELBOW",
    "TEE",
    "REDUCER",
    "TRANSITION",
    "CROSS",
    "OFFSET",
    "PLENUM_BOX",
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_meter_unit(unit: Any) -> bool:
    return normalize_text(unit) in {"m", "met", "meter", "met dai"}


def _kaiyo_quote_code(item: Dict[str, Any], quote_code: Any = "") -> str:
    code = normalize_text(quote_code or item.get("quote_code") or "")
    if code:
        return code
    category = item.get("category")
    if category in STRAIGHT_DUCT_CATEGORIES:
        return "t"
    if category == "ELBOW":
        return "cv"
    if category == "REDUCER":
        return "g"
    if category == "TRANSITION":
        return "vt"
    if category == "TEE":
        return "tt"
    if category == "PLENUM_BOX":
        return "tb"
    return ""


def _kaiyo_default_length(item: Dict[str, Any], code: str) -> float:
    if code in {"t", "ta"} and _is_meter_unit(item.get("unit")):
        return 1000.0
    if code in {"t", "ta"}:
        return _num(item.get("length"), 1200.0)
    if code in {"g", "vt"}:
        return _num(item.get("length"), 500.0)
    if code == "n":
        return _num(item.get("length"), 200.0)
    if code == "tb":
        return _num(item.get("length"), 200.0)
    return _num(item.get("length"), 0.0)


def _kaiyo_formula_context(item: Dict[str, Any], quote_code: Any = "", default_multiplier: Any = 1.0) -> Optional[Dict[str, Any]]:
    """
    Mirror the Kaiyo Excel helper columns:
    L=code, M=W1, N=H1, O=W2, P=H2, Q=W3, R=H3, S=L/H, T=R/D, U=E.
    """
    code = _kaiyo_quote_code(item, quote_code)
    if not code:
        return None
    category = item.get("category")
    supported = code in {"t", "ta", "f", "m", "d", "tb", "g", "n", "tt"} or code.startswith(("c", "vt"))
    if category not in DUCT_FITTING_CATEGORIES and not supported:
        return None

    w1 = _num(item.get("formula_width") or item.get("width") or item.get("diameter"))
    h1 = _num(item.get("formula_height") or item.get("height") or item.get("diameter"))
    w2 = _num(item.get("formula_width2") or item.get("width2"))
    h2 = _num(item.get("formula_height2") or item.get("height2"))
    w3 = _num(item.get("formula_width3") or item.get("width3"))
    h3 = _num(item.get("formula_height3") or item.get("height3"))
    length = _num(item.get("formula_length"), 0.0) or _kaiyo_default_length(item, code)
    radius = _num(item.get("formula_radius") or item.get("radius"))
    angle = _num(item.get("formula_angle") or item.get("angle"))
    edge = _num(item.get("formula_edge") or item.get("edge"))

    if w1 <= 0 or h1 <= 0:
        return None

    area = 0.0
    note = ""
    if code in {"t", "ta"}:
        length = length or (1000.0 if _is_meter_unit(item.get("unit")) else 1200.0)
        area = (w1 + h1) * 2.0 / 1000.0 * length / 1000.0
        note = f"Kaiyo mã {code}: L/H={length:g}mm"
    elif code in {"f", "m"}:
        area = (w1 * h1) / 1_000_000.0
        note = f"Kaiyo mã {code}: diện tích mặt"
    elif code == "d":
        if length <= 0:
            return None
        area = (w1 + h1 + edge / 2.0) * 2.0 * length / 1_000_000.0
        note = "Kaiyo mã d"
    elif code == "tb":
        length = length or 200.0
        area = ((w1 + h1) * 2.0 * length + w1 * h1) / 1_000_000.0
        note = f"Kaiyo mã tb: L/H={length:g}mm"
    elif code in {"g", "n"}:
        w2 = w2 or w1
        h2 = h2 or h1
        length = length or (500.0 if code == "g" else 200.0)
        area = (
            (w1 + w2) * math.sqrt(length**2 + ((h1 - h2) / 2.0) ** 2)
            + (h1 + h2) * math.sqrt(length**2 + ((w1 - w2) / 2.0) ** 2)
        ) / 1_000_000.0
        note = f"Kaiyo mã {code}: W2/H2={w2:g}x{h2:g}, L/H={length:g}mm"
    elif code.startswith("c"):
        radius = radius or w1 / 2.0
        angle = angle or 90.0
        area = (
            2.0
            * (
                math.pi * ((w1 + radius) ** 2 - radius**2)
                + h1 * (math.pi * (w1 + radius) + math.pi * radius)
            )
            * (angle / 360.0)
        ) / 1_000_000.0
        note = f"Kaiyo mã {code}: R/D={radius:g}, E={angle:g}°"
    elif code.startswith("vt"):
        w2 = w2 or w1
        h2 = h2 or h1
        length = length or 500.0
        radius = radius or _num(item.get("diameter"))
        transition_area = (
            (w1 + w2) * math.sqrt(length**2 + ((h1 - h2) / 2.0) ** 2)
            + (h1 + h2) * math.sqrt(length**2 + ((w1 - w2) / 2.0) ** 2)
        )
        round_extra = ((radius + 200.0) ** 2 - (radius / 2.0) ** 2 * 3.14) if radius > 0 else 0.0
        area = (transition_area + round_extra) / 1_000_000.0
        note = f"Kaiyo mã {code}: W2/H2={w2:g}x{h2:g}, L/H={length:g}mm"
    elif code == "tt":
        if not all(value > 0 for value in [length, w2, h2, w3, h3]):
            return None
        area = (
            2 * (w1 + h1) * (length - 1.5 * w2)
            + (math.pi * (3 * w2 + 2 / 3 * w1) * (w2 + 2 / 3 * w1 + h2 + h1) - (h2 + h1) * (math.pi * 1.5 * w2)) / 8
            + (math.pi * (3 * w3 + 2 / 3 * w1) * (w3 + 2 / 3 * w1 + h3 + h1) - (h3 + h1) * (math.pi * 1.5 * w3)) / 8
        ) / 1_000_000.0
        note = "Kaiyo mã tt"
    else:
        return None

    if area <= 0:
        return None
    return {
        "quote_code": code,
        "area_per_item": round(area, 4),
        "formula_width": w1,
        "formula_height": h1,
        "formula_width2": w2 or None,
        "formula_height2": h2 or None,
        "formula_width3": w3 or None,
        "formula_height3": h3 or None,
        "formula_length": length or None,
        "formula_radius": radius or None,
        "formula_angle": angle or None,
        "area_multiplier": _kaiyo_quote_area_multiplier(item, code, default_multiplier),
        "quote_note": note,
    }

def calculate_duct_area(item: Dict[str, Any]) -> float:
    """
    Calculate the surface area of a single item in square meters (m2).
    Inputs: dimensions in mm, returns area in m2.
    """
    category = item.get("category", "UNKNOWN")
    w = item.get("width")
    h = item.get("height")
    d = item.get("diameter")
    l = item.get("length") or 1200.0 # default length is 1.2m
    q = item.get("quantity", 1.0)
    desc = item.get("description", "")
    if _unit_key(item.get("unit")) == "m2":
        return 1.0
    if category in DAMPER_CATEGORIES and (not item.get("length") or (l == 1200.0 and not _has_explicit_length(item))):
        l = DEFAULT_DAMPER_LENGTH_MM.get(category, l)

    # Convert to meters for calculation
    w_m = (w or 0.0) / 1000.0
    h_m = (h or 0.0) / 1000.0
    d_m = (d or 0.0) / 1000.0
    l_m = (l or 0.0) / 1000.0

    area = 0.0

    # Rectangular Duct
    if category in ["SUPPLY_DUCT", "RETURN_DUCT", "FRESH_AIR_DUCT", "EXHAUST_AIR_DUCT", "SMOKE_DUCT"]:
        if _unit_key(item.get("unit")) == "m2":
            area = 1.0
        elif w_m > 0 and h_m > 0:
            area = 2 * (w_m + h_m) * l_m
        elif w_m > 0:
            area = w_m * l_m # Flat sheet/other
            
    # Round Duct or Flexible Duct
    elif category in ["FLEXIBLE_DUCT", "ROUND_DIFFUSER"] or (d_m > 0 and category == "UNKNOWN"):
        if d_m > 0:
            area = math.pi * d_m * l_m
            
    # Reducer
    elif category == "REDUCER":
        # Check if we can find secondary dimensions from description
        # Example: "REDUCER 500x300 to 300x200" or "D400 to D300"
        w2, h2, d2 = None, None, None
        
        # Try rect reducer pattern: W1xH1 - W2xH2
        rect_reducer = re_find_reducer_rect(desc)
        if rect_reducer:
            w2, h2 = rect_reducer[2]/1000.0, rect_reducer[3]/1000.0
        
        # Try round reducer pattern: D1 - D2
        round_reducer = re_find_reducer_round(desc)
        if round_reducer:
            d2 = round_reducer[1]/1000.0

        if w_m > 0 and h_m > 0:
            # Rectangular Reducer: Average Perimeter Formula
            w2_m = w2 if w2 is not None else w_m * 0.75 # default exit size is 75%
            h2_m = h2 if h2 is not None else h_m * 0.75
            p1 = 2 * (w_m + h_m)
            p2 = 2 * (w2_m + h2_m)
            area = ((p1 + p2) / 2.0) * l_m
        elif d_m > 0:
            # Round Reducer: Average Perimeter
            d2_m = d2 if d2 is not None else d_m * 0.75
            area = math.pi * ((d_m + d2_m) / 2.0) * l_m
            
    # Transition
    elif category == "TRANSITION":
        # Transition connects rectangular to round, or offsets.
        # Transition Formula: Average of Rect Perimeter and Round Perimeter
        # Try to parse diameter for exit
        d2 = None
        d_match = re.search(r'(?:[dD]|[Øø]|dia|phi)\s*(\d+(?:[\.,]\d+)?)', desc, re.IGNORECASE)
        if d_match:
            d2 = float(d_match.group(1).replace(",", ".")) / 1000.0
            
        if w_m > 0 and h_m > 0:
            p1 = 2 * (w_m + h_m)
            p2 = math.pi * d2 if d2 else p1 * 0.8
            area = ((p1 + p2) / 2.0) * l_m
        else:
            area = 2 * (w_m + h_m) * l_m if (w_m > 0 and h_m > 0) else 0.0

    # Elbow / Bend
    elif category == "ELBOW":
        # Elbow: Coefficient Formula based on duct perimeter and centerline radius.
        # Centerline radius is typically 1.5 * Width
        # Let centerline length be ~ 1.57 * Width
        # Area = Perimeter * centerline_length
        if w_m > 0 and h_m > 0:
            p = 2 * (w_m + h_m)
            elbow_len = 1.57 * w_m
            area = p * elbow_len
        elif d_m > 0:
            area = math.pi * d_m * (1.57 * d_m)
            
    # Tee
    elif category == "TEE":
        # Tee Coefficient Formula: approximate as 1.5 times the main duct area
        if w_m > 0 and h_m > 0:
            area = 2 * (w_m + h_m) * l_m * 1.5
        elif d_m > 0:
            area = math.pi * d_m * l_m * 1.5
            
    # Cross
    elif category == "CROSS":
        # Cross Coefficient Formula: approximate as 2.0 times the main duct area
        if w_m > 0 and h_m > 0:
            area = 2 * (w_m + h_m) * l_m * 2.0
        elif d_m > 0:
            area = math.pi * d_m * l_m * 2.0
            
    # Offset
    elif category == "OFFSET":
        # Offset Coefficient Formula: standard area + 15% slant correction
        if w_m > 0 and h_m > 0:
            area = 2 * (w_m + h_m) * l_m * 1.15
        elif d_m > 0:
            area = math.pi * d_m * l_m * 1.15

    # Plenum Box
    elif category == "PLENUM_BOX":
        # Plenum Box: Surface Formula (6 sides)
        # Often open on 1 side, let's calculate 5 sides
        if w_m > 0 and h_m > 0 and l_m > 0:
            area = (w_m * h_m) + 2 * (w_m * l_m) + 2 * (h_m * l_m)
        else:
            area = 1.0 # Default fallback m2 per box

    # Dampers (Fire, Volume Control, Motorized, Back Draft)
    elif category in DAMPER_CATEGORIES:
        # Kaiyo damper formula: side area + face area.
        # Rectangular: 2 * (W + H) * L + W * H.
        # Circular: circumference * L + circular face area.
        if w_m > 0 and h_m > 0:
            area = (2 * (w_m + h_m) * l_m) + (w_m * h_m)
        elif d_m > 0:
            area = (math.pi * d_m * l_m) + (math.pi * (d_m ** 2) / 4.0)
        else:
            area = 0.1 # default 0.1 m2

    # Grilles / Diffusers (Linear, Square, Eggcrate, Louver, Jet Nozzle)
    elif category in ["LINEAR_DIFFUSER", "SQUARE_DIFFUSER", "ROUND_DIFFUSER", "EGGCRATE_GRILLE", "JET_NOZZLE", "LOUVER"]:
        # Area = W * H
        if w_m > 0 and h_m > 0:
            area = w_m * h_m
        elif d_m > 0:
            area = math.pi * (d_m ** 2) / 4.0
        else:
            area = 0.05 # default 0.05 m2

    # Access / Inspection Door
    elif category in ["ACCESS_DOOR", "INSPECTION_DOOR"]:
        if w_m > 0 and h_m > 0:
            area = w_m * h_m
        else:
            area = 0.12 # e.g. 300x400 default

    # Silencer / Attenuator
    elif category == "SILENCER":
        if w_m > 0 and h_m > 0:
            area = 2 * (w_m + h_m) * l_m
        else:
            area = 1.0

    # Flexible Connector
    elif category == "FLEXIBLE_CONNECTOR":
        # Usually short connector, e.g. L = 150mm
        conn_l = l_m if l_m > 0 else 0.15
        if w_m > 0 and h_m > 0:
            area = 2 * (w_m + h_m) * conn_l
        elif d_m > 0:
            area = math.pi * d_m * conn_l
        else:
            area = 0.2

    else:
        # Fallback rectangular
        if w_m > 0 and h_m > 0:
            area = 2 * (w_m + h_m) * l_m
        elif d_m > 0:
            area = math.pi * d_m * l_m
        else:
            area = 1.0 # fallback

    return round(area, 4)


def _has_explicit_length(item: Dict[str, Any]) -> bool:
    text = normalize_text(" ".join(str(item.get(key) or "") for key in ["description", "remark", "quote_note", "name"]))
    return bool(re.search(r"(^|[^a-z])l\s*=?\s*\d+", text) or "chieu dai" in text or "dai" in text)


def _item_with_pricing_defaults(item: Dict[str, Any], product_rule: Dict[str, Any]) -> Dict[str, Any]:
    priced_item = dict(item)
    category = priced_item.get("category")
    default_length = product_rule.get("length_mm") or DEFAULT_DAMPER_LENGTH_MM.get(category)
    if category in DAMPER_CATEGORIES and default_length:
        current_length = priced_item.get("length")
        if not current_length or (float(current_length or 0) == 1200.0 and not _has_explicit_length(priced_item)):
            priced_item["length"] = float(default_length)
    return priced_item


def _mfd_explicit_motor_count(item: Dict[str, Any]) -> Optional[int]:
    text = normalize_text(" ".join(str(item.get(key) or "") for key in ["description", "remark", "quote_material_spec", "quote_note", "name"]))
    match = re.search(r"(\d{1,2})\s*(?:dong co|motor|bo motor)", text)
    if match:
        return max(1, int(match.group(1)))
    return None


def _mfd_motor_count(item: Dict[str, Any]) -> int:
    explicit = _mfd_explicit_motor_count(item)
    if explicit:
        return explicit
    width = float(item.get("width") or item.get("diameter") or 0)
    return 1


def _mfd_motor_rate(item: Dict[str, Any]) -> float:
    text = normalize_text(" ".join(str(item.get(key) or "") for key in ["description", "remark", "quote_material_spec", "quote_note", "name"]))
    width = float(item.get("width") or item.get("diameter") or 0)
    if "kyotech" in text or "k-fsls" in text or "k-bfn" in text:
        return 3_500_000.0 if width > 1200 or "k-bfn" in text else 2_800_000.0
    if "belimo" in text or "fsnf" in text:
        return 6_000_000.0
    return MFD_DEFAULT_MOTOR_RATE


def _mfd_motor_cost(item: Dict[str, Any]) -> float:
    explicit = _mfd_explicit_motor_count(item)
    if explicit:
        return explicit * _mfd_motor_rate(item)

    text = normalize_text(" ".join(str(item.get(key) or "") for key in ["description", "remark", "quote_material_spec", "quote_note", "name"]))
    width = float(item.get("width") or item.get("diameter") or 0)
    height = float(item.get("height") or 0)

    if "ei30" in text or "fslf" in text:
        return 5_000_000.0
    if "ei120" in text or "fsnf" in text:
        return 6_000_000.0
    if "ei60" in text:
        if width >= 3000:
            return 24_000_000.0
        if height >= 1200 and width >= 2400:
            return 12_000_000.0
        if width >= 2700:
            return 12_000_000.0
        if width >= 2600 and height <= 400:
            return 12_000_000.0
        if width >= 2250 or (width >= 2000 and height >= 500):
            return 10_000_000.0
        if width >= 1800:
            return 6_000_000.0
        return 5_000_000.0
    return MFD_DEFAULT_MOTOR_RATE


def _fd_accessory_cost(item: Dict[str, Any]) -> float:
    text = normalize_text(" ".join(str(item.get(key) or "") for key in ["description", "remark", "quote_material_spec", "quote_note", "name"]))
    return 400_000.0 if "ei30" in text else FD_DEFAULT_ACCESSORY_RATE


def _is_prd(item: Dict[str, Any]) -> bool:
    text = normalize_text(" ".join(str(item.get(key) or "") for key in ["description", "remark", "quote_note", "name", "quote_code"]))
    return item.get("category") == "VOLUME_CONTROL_DAMPER" and ("prd" in text or "van giam ap" in text or "van xa ap" in text)


def _damper_side_area(item: Dict[str, Any]) -> float:
    w_m = float(item.get("width") or 0) / 1000.0
    h_m = float(item.get("height") or 0) / 1000.0
    d_m = float(item.get("diameter") or 0) / 1000.0
    l_m = float(item.get("length") or DEFAULT_DAMPER_LENGTH_MM.get(item.get("category"), 200.0)) / 1000.0
    if w_m > 0 and h_m > 0:
        return round(2 * (w_m + h_m) * l_m, 4)
    if d_m > 0:
        return round(math.pi * d_m * l_m, 4)
    return 0.0


def _is_fireproof_backdraft(item: Dict[str, Any]) -> bool:
    text = normalize_text(" ".join(str(item.get(key) or "") for key in ["description", "remark", "quote_material_spec", "quote_note", "name"]))
    return item.get("category") == "BACK_DRAFT_DAMPER" and (
        "boc chong chay" in text or "ei60" in text or "ei90" in text or "ei120" in text
    )


def _is_oval_grille_with_obd(item: Dict[str, Any]) -> bool:
    text = normalize_text(" ".join(str(item.get(key) or "") for key in ["description", "remark", "quote_note", "name"]))
    return "obd" in text and ("nan bau duc" in text or "bau duc" in text)


def _oval_grille_obd_components(item: Dict[str, Any]) -> Tuple[float, float, float]:
    width_mm = float(item.get("width") or 0)
    height_mm = float(item.get("height") or 0)
    frame_m = 2 * (width_mm + height_mm) / 1000.0 if width_mm and height_mm else 0.0
    blade_count = max((height_mm / 20.0) - 1.0, 0.0) if height_mm else 0.0
    blade_m = blade_count * width_mm / 1000.0 if width_mm else 0.0
    unit_price = ((frame_m * OVAL_GRILLE_OBD_FRAME_RATE) + (blade_m * OVAL_GRILLE_OBD_BLADE_RATE)) * OVAL_GRILLE_OBD_FACTOR
    return round(frame_m, 4), round(blade_m, 4), round(unit_price, 2)

# Regex helpers for reducer dimensions extraction
import re
def re_find_reducer_rect(text: str) -> Optional[tuple[float, float, float, float]]:
    match = re.search(r'(\d+(?:[\.,]\d+)?)\s*[xX*×]\s*(\d+(?:[\.,]\d+)?)\s*(?:/|-|to)\s*(\d+(?:[\.,]\d+)?)\s*[xX*×]\s*(\d+(?:[\.,]\d+)?)', text, re.IGNORECASE)
    if match:
        return tuple(float(match.group(i).replace(",", ".")) for i in range(1, 5))
    return None

def re_find_reducer_round(text: str) -> Optional[tuple[float, float]]:
    match = re.search(r'(?:[dD]|[Øø]|dia|phi)\s*(\d+(?:[\.,]\d+)?)\s*(?:/|-|to)\s*(?:[dD]|[Øø]|dia|phi)\s*(\d+(?:[\.,]\d+)?)', text, re.IGNORECASE)
    if match:
        return (float(match.group(1).replace(",", ".")), float(match.group(2).replace(",", ".")))
    return None


def get_coefficient_rules() -> Dict[str, Dict[str, Any]]:
    """
    Get all pricing coefficients from the database.
    """
    if supabase_store.enabled():
        try:
            return supabase_store.get_coefficient_rules()
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase get_coefficient_rules unavailable; using local dev DB because USE_SUPABASE_DB=0: {exc}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value, type FROM coefficient_rules")
    rows = cursor.fetchall()
    conn.close()
    
    coeffs = {}
    for r in rows:
        coeffs[r["key"]] = {"value": r["value"], "type": r["type"]}
    return coeffs

def get_pricing_rules() -> Dict[tuple[str, float], float]:
    """
    Get material pricing lookup table from the database: (material, thickness) -> unit_price
    """
    if supabase_store.enabled():
        try:
            return supabase_store.get_pricing_rules()
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase get_pricing_rules unavailable; using local dev DB because USE_SUPABASE_DB=0: {exc}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT material, thickness, unit_price FROM pricing_rules")
    rows = cursor.fetchall()
    conn.close()

    pricing = {}
    for r in rows:
        pricing[(r["material"], r["thickness"])] = r["unit_price"]
    return pricing


def get_product_pricing_rules() -> Dict[str, Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            return supabase_store.get_product_pricing_rules()
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase get_product_pricing_rules unavailable; using local dev DB because USE_SUPABASE_DB=0: {exc}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT category, quote_code, unit_mode, material_spec, brand, base_unit_price,
               area_multiplier, length_mm, note, source
        FROM product_pricing_rules
    """)
    rows = cursor.fetchall()
    conn.close()
    return {row["category"]: dict(row) for row in rows}


def _product_only_item(
    item: Dict[str, Any],
    priced_item: Dict[str, Any],
    product_rule: Dict[str, Any],
    warnings: List[str],
    area_per_item: float,
    total_area: float,
    quote_code: str,
    unit_mode: str,
    kaiyo_formula: Optional[Dict[str, Any]],
    price_source_status: str,
) -> Dict[str, Any]:
    updated_item = dict(item)
    if priced_item.get("length") != item.get("length"):
        updated_item["length"] = priced_item.get("length")
    updated_item.update({
        "calculated_area": round(total_area, 3),
        "material_cost": 0.0,
        "labor_cost": 0.0,
        "accessory_cost": 0.0,
        "installation_cost": 0.0,
        "painting_cost": 0.0,
        "insulation_cost": 0.0,
        "waste_cost": 0.0,
        "subtotal": 0.0,
        "profit": 0.0,
        "vat": 0.0,
        "grand_total": 0.0,
        "quote_code": quote_code,
        "quote_material_spec": product_rule.get("material_spec") or _default_material_spec(
            item.get("material", "GI"),
            float(item.get("thickness") or 0),
            item.get("pressure_class"),
        ),
        "quote_brand": product_rule.get("brand", "Kaiyo Viet Nam"),
        "quote_note": item.get("remark") or "",
        "quote_unit_price": None,
        "quote_output_unit_price": None,
        "pricing_mode": unit_mode,
        "area_multiplier": None,
        "formula_width": (kaiyo_formula or {}).get("formula_width") or priced_item.get("width") or priced_item.get("diameter"),
        "formula_height": (kaiyo_formula or {}).get("formula_height") or priced_item.get("height") or priced_item.get("diameter"),
        "formula_width2": (kaiyo_formula or {}).get("formula_width2") or item.get("formula_width2") or item.get("width2"),
        "formula_height2": (kaiyo_formula or {}).get("formula_height2") or item.get("formula_height2") or item.get("height2"),
        "formula_width3": (kaiyo_formula or {}).get("formula_width3") or item.get("formula_width3") or item.get("width3"),
        "formula_height3": (kaiyo_formula or {}).get("formula_height3") or item.get("formula_height3") or item.get("height3"),
        "formula_length": (kaiyo_formula or {}).get("formula_length") or priced_item.get("length"),
        "formula_radius": (kaiyo_formula or {}).get("formula_radius") or item.get("formula_radius") or item.get("radius"),
        "formula_angle": (kaiyo_formula or {}).get("formula_angle") or item.get("formula_angle") or item.get("angle"),
        "formula_area": (kaiyo_formula or {}).get("area_per_item") or area_per_item,
        "formula_unit_price": None,
        "accessory_area": None,
        "accessory_unit_price": None,
        "price_source_status": "product_only" if price_source_status == "exact" else price_source_status,
        "pricing_confidence": "product_only",
        "can_auto_quote": price_source_status == "exact" and not warnings,
        "warnings": warnings,
    })
    return updated_item


def calculate_item_cost(
    item: Dict[str, Any],
    coeffs: Dict[str, Any],
    pricing: Dict[tuple[str, float], float],
    product_rules: Dict[str, Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Calculate costs for a single product item using the pricing engine logic.
    """
    # Initialize warning list
    warnings = item.get("warnings", [])
    price_source_status = item.get("price_source_status") or "exact"
    if isinstance(warnings, str):
        warnings = [warnings] if warnings else []
    else:
        warnings = list(warnings)

    product_rules = product_rules or {}
    product_rule = product_rules.get(item.get("category"), {})
    priced_item = _item_with_pricing_defaults(item, product_rule)
    required_fields = item.get("product_required_fields") or []
    if isinstance(required_fields, str):
        required_fields = [part.strip() for part in required_fields.split(",") if part.strip()]
    missing_required_fields = [
        field for field in required_fields
        if field != "material" and item.get(field) in (None, "", 0)
    ]
    if "material" in required_fields and not item.get("material"):
        missing_required_fields.append("material")
    if missing_required_fields:
        warnings.append("Thiếu dữ liệu bắt buộc cho mã sản phẩm chuẩn: " + ", ".join(missing_required_fields) + ".")
        price_source_status = "missing_required_fields"

    # 1. Dimensions and Area
    area_per_item = calculate_duct_area(priced_item)
    qty = item.get("quantity", 0.0)
    total_area = area_per_item * qty

    # Validation warnings
    if qty <= 0:
        warnings.append("Số lượng phải lớn hơn 0.")
    if item.get("category") in ["SUPPLY_DUCT", "RETURN_DUCT", "FRESH_AIR_DUCT", "EXHAUST_AIR_DUCT", "SMOKE_DUCT", "ELBOW", "TEE", "REDUCER", "TRANSITION", "CROSS", "OFFSET"]:
        if _unit_key(item.get("unit")) != "m2" and (not item.get("width") or not item.get("height")):
            if not item.get("diameter"):
                warnings.append("Thiếu kích thước rộng/cao hoặc đường kính.")
    if not product_only_mode() and (not item.get("thickness") or item.get("thickness") <= 0):
        warnings.append("Thiếu hoặc sai độ dày vật liệu.")

    if item.get("category") == "UNKNOWN" and not product_rule:
        qty = item.get("quantity", 0.0)
        updated_item = dict(item)
        warnings.append("Chưa có quy tắc nhận diện sản phẩm cho dòng này. Cần bổ sung danh mục/từ khóa trước khi phát hành.")
        updated_item.update({
            "calculated_area": 0.0,
            "material_cost": 0.0,
            "labor_cost": 0.0,
            "accessory_cost": 0.0,
            "installation_cost": 0.0,
            "painting_cost": 0.0,
            "insulation_cost": 0.0,
            "waste_cost": 0.0,
            "subtotal": 0.0,
            "profit": 0.0,
            "vat": 0.0,
            "grand_total": 0.0,
            "quote_code": "",
            "quote_material_spec": "",
            "quote_brand": "",
            "quote_note": "Cần bổ sung quy tắc nhận diện sản phẩm trước khi phát hành.",
            "quote_unit_price": None,
            "pricing_mode": "needs_product_rule",
            "area_multiplier": None,
            "price_source_status": "unknown_product",
            "pricing_confidence": "blocked",
            "can_auto_quote": False,
            "warnings": warnings,
        })
        return updated_item

    unit_mode = product_rule.get("unit_mode", "area")
    quote_code = product_rule.get("quote_code", "")
    area_multiplier = _kaiyo_quote_area_multiplier(priced_item, quote_code, product_rule.get("area_multiplier") or 1.0)
    kaiyo_formula = _kaiyo_formula_context(priced_item, quote_code, product_rule.get("area_multiplier") or 1.0)
    if kaiyo_formula:
        quote_code = kaiyo_formula["quote_code"]
        area_per_item = float(kaiyo_formula["area_per_item"])
        total_area = area_per_item * qty
        area_multiplier = float(kaiyo_formula["area_multiplier"])

    if product_only_mode():
        return _product_only_item(
            item,
            priced_item,
            product_rule,
            warnings,
            area_per_item,
            total_area,
            quote_code,
            unit_mode,
            kaiyo_formula,
            price_source_status,
        )

    if item.get("product_pricing_method") == "manual_or_price_list":
        updated_item = dict(item)
        warnings.append("Sản phẩm này chưa có công thức tự động được duyệt. Cần sales/QS nhập đơn giá hoặc lấy từ báo giá đã học trước khi phát hành.")
        updated_item.update({
            "calculated_area": 0.0,
            "material_cost": 0.0,
            "labor_cost": 0.0,
            "accessory_cost": 0.0,
            "installation_cost": 0.0,
            "painting_cost": 0.0,
            "insulation_cost": 0.0,
            "waste_cost": 0.0,
            "subtotal": 0.0,
            "profit": 0.0,
            "vat": 0.0,
            "grand_total": 0.0,
            "quote_code": "",
            "quote_material_spec": "",
            "quote_brand": "",
            "quote_note": "Cần nhập đơn giá đã xác nhận trước khi phát hành.",
            "quote_unit_price": 0.0,
            "pricing_mode": "needs_price",
            "area_multiplier": 0.0,
            "price_source_status": "needs_manual_price",
            "pricing_confidence": "blocked",
            "can_auto_quote": False,
            "warnings": warnings,
        })
        return updated_item

    # 2. Lookup material unit price
    material = item.get("material", "GI")
    thickness = item.get("thickness") or 0.6
    
    # Try looking up exact match
    unit_price = pricing.get((material, thickness))
    
    if unit_price is None:
        # Fallback to closest thickness for that material
        material_prices = {k[1]: v for k, v in pricing.items() if k[0] == material}
        if material_prices:
            closest_thick = min(material_prices.keys(), key=lambda t: abs(t - thickness))
            unit_price = material_prices[closest_thick]
            warnings.append(f"Chưa có đơn giá độ dày {thickness}mm. Đang dùng đơn giá gần nhất: {closest_thick}mm.")
            price_source_status = "closest_thickness"
        else:
            unit_price = 0.0
            warnings.append(f"Chưa có quy tắc đơn giá cho vật liệu {material}.")
            price_source_status = "missing_material_price"

    base_unit_price = float(product_rule.get("base_unit_price") or 0.0)
    if item.get("product_pricing_method") == "composite_rule" and not product_rule.get("formula_json"):
        warnings.append("Sản phẩm ghép đã nhận diện được nhưng chưa có rule công thức chi tiết được duyệt; cần QS xác nhận hoặc dùng báo giá đã học.")
        price_source_status = "needs_composite_rule"

    # Calculate Material Cost
    mfd_sheet_rate = None
    mfd_motor_cost = 0.0
    special_formula_width2 = item.get("formula_width2")
    special_formula_height2 = item.get("formula_height2")
    special_formula_length = item.get("formula_length")
    special_formula_radius = item.get("formula_radius")
    special_formula_angle = item.get("formula_angle")
    special_formula_area = item.get("formula_area")
    special_accessory_area = item.get("accessory_area")
    special_accessory_unit_price = item.get("accessory_unit_price")
    special_note = product_rule.get("note", "")
    if kaiyo_formula:
        special_formula_width2 = kaiyo_formula.get("formula_width2")
        special_formula_height2 = kaiyo_formula.get("formula_height2")
        special_formula_length = kaiyo_formula.get("formula_length")
        special_formula_radius = kaiyo_formula.get("formula_radius")
        special_formula_angle = kaiyo_formula.get("formula_angle")
        special_formula_area = area_per_item
        special_note = kaiyo_formula.get("quote_note") or special_note
    if item.get("category") == "MOTORIZED_DAMPER":
        mfd_sheet_rate = base_unit_price if base_unit_price and abs(base_unit_price - 3_500_000.0) > 1 else MFD_DEFAULT_SHEET_RATE
        mfd_motor_cost = _mfd_motor_cost(priced_item)
        unit_price = mfd_sheet_rate
        material_cost = qty * ((area_per_item * mfd_sheet_rate) + mfd_motor_cost)
        special_formula_width2 = priced_item.get("length")
        special_formula_height2 = _damper_side_area(priced_item)
        special_formula_area = area_per_item
        special_accessory_unit_price = mfd_motor_cost
        special_note = "Van MFD, L=250"
        unit_mode = "mfd_kaiyo_l250"
    elif item.get("category") == "FIRE_DAMPER":
        fd_accessory_cost = _fd_accessory_cost(priced_item)
        unit_price = FD_DEFAULT_SHEET_RATE
        material_cost = qty * ((area_per_item * unit_price) + fd_accessory_cost)
        special_formula_width2 = priced_item.get("length")
        special_formula_height2 = _damper_side_area(priced_item)
        special_formula_area = area_per_item
        special_accessory_unit_price = fd_accessory_cost
        special_note = "Van FD, L=250"
        unit_mode = "fd_kaiyo_l250"
    elif _is_prd(priced_item):
        unit_price = 800_000.0
        prd_accessory_cost = 200_000.0
        material_cost = qty * ((area_per_item * unit_price) + prd_accessory_cost)
        special_formula_width2 = priced_item.get("length")
        special_formula_height2 = _damper_side_area(priced_item)
        special_formula_area = area_per_item
        special_accessory_unit_price = prd_accessory_cost
        special_note = "Van giam ap PRD, L=200"
        unit_mode = "prd_l200"
    elif _is_fireproof_backdraft(priced_item):
        side_area = _damper_side_area(priced_item)
        unit_price = BACK_DRAFT_FIREPROOF_SHEET_RATE
        material_cost = qty * ((area_per_item * unit_price) + (side_area * BACK_DRAFT_FIREPROOF_ACCESSORY_RATE))
        special_formula_width2 = priced_item.get("length")
        special_formula_height2 = side_area
        special_formula_area = area_per_item
        special_accessory_area = side_area
        special_accessory_unit_price = BACK_DRAFT_FIREPROOF_ACCESSORY_RATE
        special_note = "Van 1 chieu boc chong chay, L=200"
        unit_mode = "backdraft_fireproof_l200"
    elif _is_oval_grille_with_obd(priced_item):
        frame_m, blade_m, grille_unit_price = _oval_grille_obd_components(priced_item)
        unit_price = OVAL_GRILLE_OBD_FRAME_RATE
        material_cost = qty * grille_unit_price
        area_per_item = frame_m
        total_area = frame_m * qty
        special_formula_width2 = frame_m
        special_formula_height2 = (float(priced_item.get("height") or 0) / 20.0) - 1.0
        special_formula_area = None
        special_accessory_area = blade_m
        special_accessory_unit_price = OVAL_GRILLE_OBD_BLADE_RATE
        special_note = "Cua gio nan bau duc 1 lop + OBD"
        unit_mode = "oval_grille_obd"
    elif unit_mode == "piece":
        unit_price = base_unit_price or unit_price
        material_cost = qty * unit_price
    elif unit_mode == "piece_area":
        unit_price = base_unit_price or unit_price
        material_cost = qty * (unit_price + (area_per_item * pricing.get((material, thickness), 0.0) * area_multiplier))
    else:
        material_cost = total_area * unit_price * area_multiplier

    # 3. Apply Coefficients (Labor, Accessory, Installation, Painting, Insulation, Waste)
    def get_coeff_val(key, default=0.0):
        return coeffs.get(key, {}).get("value", default)

    def get_coeff_type(key, default="percentage"):
        return coeffs.get(key, {}).get("type", default)

    # Labor
    labor_rate = get_coeff_val("labor_rate_pct")
    labor_type = get_coeff_type("labor_rate_pct")
    if labor_type == "percentage":
        labor_cost = material_cost * labor_rate
    else: # fixed_per_m2
        labor_cost = total_area * labor_rate

    # Accessory
    acc_rate = get_coeff_val("accessory_rate_pct")
    acc_type = get_coeff_type("accessory_rate_pct")
    if acc_type == "percentage":
        accessory_cost = material_cost * acc_rate
    else:
        accessory_cost = total_area * acc_rate

    # Installation
    inst_rate = get_coeff_val("installation_rate_pct")
    inst_type = get_coeff_type("installation_rate_pct")
    if inst_type == "percentage":
        installation_cost = material_cost * inst_rate
    else:
        installation_cost = total_area * inst_rate

    # Painting (Only if requested in description or remark)
    painting_cost = 0.0
    is_painted = any(kw in (item.get("description", "") + " " + item.get("remark", "")).lower() for kw in ["paint", "sơn", "painting"])
    if is_painted:
        paint_rate = get_coeff_val("painting_rate_m2")
        painting_cost = total_area * paint_rate

    # Insulation (Only if requested in description or remark)
    insulation_cost = 0.0
    is_insulated = any(kw in (item.get("description", "") + " " + item.get("remark", "")).lower() for kw in ["insul", "bảo ôn", "cách nhiệt"])
    if is_insulated:
        insul_rate = get_coeff_val("insulation_rate_m2")
        insulation_cost = total_area * insul_rate

    # Waste
    waste_rate = get_coeff_val("waste_rate_pct")
    waste_cost = material_cost * waste_rate

    # Subtotal
    subtotal = material_cost + labor_cost + accessory_cost + installation_cost + painting_cost + insulation_cost + waste_cost

    # Profit
    profit_rate = get_coeff_val("profit_pct")
    profit = subtotal * profit_rate

    # VAT
    vat_rate = get_coeff_val("vat_pct")
    vat = (subtotal + profit) * vat_rate

    # Grand Total (per item)
    grand_total = subtotal + profit + vat

    # Update item dict
    updated_item = dict(item)
    if priced_item.get("length") != item.get("length"):
        updated_item["length"] = priced_item.get("length")
    quote_note = special_note
    updated_item.update({
        "calculated_area": round(total_area, 3),
        "material_cost": round(material_cost, 2),
        "labor_cost": round(labor_cost, 2),
        "accessory_cost": round(accessory_cost, 2),
        "installation_cost": round(installation_cost, 2),
        "painting_cost": round(painting_cost, 2),
        "insulation_cost": round(insulation_cost, 2),
        "waste_cost": round(waste_cost, 2),
        "subtotal": round(subtotal, 2),
        "profit": round(profit, 2),
        "vat": round(vat, 2),
        "grand_total": round(grand_total, 2),
        "quote_code": quote_code,
        "quote_material_spec": product_rule.get("material_spec") or _default_material_spec(material, thickness, item.get("pressure_class")),
        "quote_brand": product_rule.get("brand", "Kaiyo Viet Nam"),
        "quote_note": quote_note,
        "quote_unit_price": round(subtotal / qty, 2) if qty else round(subtotal, 2),
        "pricing_mode": unit_mode,
        "area_multiplier": area_multiplier,
        "formula_width": kaiyo_formula.get("formula_width") if kaiyo_formula else priced_item.get("width"),
        "formula_height": kaiyo_formula.get("formula_height") if kaiyo_formula else priced_item.get("height"),
        "formula_width2": special_formula_width2,
        "formula_height2": special_formula_height2,
        "formula_width3": kaiyo_formula.get("formula_width3") if kaiyo_formula else item.get("formula_width3"),
        "formula_height3": kaiyo_formula.get("formula_height3") if kaiyo_formula else item.get("formula_height3"),
        "formula_length": priced_item.get("length") if item.get("category") in DAMPER_CATEGORIES else special_formula_length,
        "formula_radius": special_formula_radius,
        "formula_angle": special_formula_angle,
        "formula_area": special_formula_area if special_formula_area is not None else (area_per_item if item.get("category") in DAMPER_CATEGORIES else item.get("formula_area")),
        "formula_unit_price": unit_price if kaiyo_formula or unit_mode in {"mfd_kaiyo_l250", "fd_kaiyo_l250", "prd_l200", "backdraft_fireproof_l200", "oval_grille_obd"} else (mfd_sheet_rate or item.get("formula_unit_price")),
        "accessory_area": special_accessory_area,
        "accessory_unit_price": special_accessory_unit_price if special_accessory_unit_price is not None else (mfd_motor_cost or item.get("accessory_unit_price")),
        "price_source_status": price_source_status,
        "pricing_confidence": "high" if price_source_status in {"exact", "quote_memory"} else "needs_review",
        "can_auto_quote": price_source_status in {"exact", "quote_memory"},
        "warnings": warnings
    })

    return updated_item


def _kaiyo_quote_area_multiplier(item: Dict[str, Any], quote_code: Any, default: Any = 1.0) -> float:
    try:
        default_multiplier = float(default or 1.0)
    except (TypeError, ValueError):
        default_multiplier = 1.0

    normalized_code = normalize_text(quote_code or item.get("quote_code") or "")
    category = item.get("category")
    duct_categories = {"SUPPLY_DUCT", "RETURN_DUCT", "FRESH_AIR_DUCT", "EXHAUST_AIR_DUCT", "SMOKE_DUCT"}
    text = normalize_text(" ".join(str(item.get(key) or "") for key in ["description", "remark", "quote_note", "name"]))

    is_t_code = normalized_code == "t"
    if not is_t_code and category not in duct_categories:
        return default_multiplier

    if "ong bu" in text or "ong bo sung" in text or "bu ong" in text:
        return 1.2
    if category in duct_categories or "ong nguyen" in text or "ong thong gio" in text or "ong gio" in text:
        return 1.0
    return 1.3


def _default_material_spec(material: str, thickness: float, pressure_class: str = "") -> str:
    label = {
        "GI": "Ton ma kem",
        "SS": "Inox",
        "MS": "Thep den",
        "PU": "Panel PU",
    }.get(material, material)
    suffix = f" + {pressure_class}" if pressure_class else ""
    return f"{label} day {thickness:g} mm{suffix}"


def _apply_quote_memory_price(item: Dict[str, Any], memory: Dict[str, Any]) -> Dict[str, Any]:
    qty = float(item.get("quantity") or 0)
    unit_price = float(memory.get("quote_unit_price") or 0)
    if qty <= 0 or unit_price <= 0:
        return item

    package_length_m = float(memory.get("package_length_m") or 0)
    package_unit_price = float(memory.get("package_unit_price") or 0)
    learned_line_total = float(memory.get("reference_line_total") or 0)
    package_qty = None
    if package_length_m > 0 and package_unit_price > 0:
        package_qty = math.ceil(qty / package_length_m)
        line_total = package_qty * package_unit_price
        effective_unit_price = line_total / qty
    elif learned_line_total > 0 and float(memory.get("quote_output_quantity") or 0) > 0:
        learned_qty = float(memory.get("quote_output_quantity") or 0)
        learned_unit_price = learned_line_total / learned_qty
        line_total = qty * learned_unit_price
        effective_unit_price = learned_unit_price
        unit_price = learned_unit_price
    else:
        line_total = qty * unit_price
        effective_unit_price = unit_price

    updated = dict(item)
    updated.update({
        "quote_unit_price": round(effective_unit_price, 2),
        "quote_output_quantity": qty,
        "quote_output_unit": memory.get("quote_output_unit") or item.get("unit"),
        "quote_output_unit_price": round(float(memory.get("quote_output_unit_price") or unit_price), 2),
        "quote_material_spec": memory.get("quote_material_spec") or item.get("quote_material_spec"),
        "quote_brand": memory.get("quote_brand") or item.get("quote_brand"),
        "quote_note": memory.get("quote_note") or item.get("quote_note"),
        "quote_code": memory.get("quote_code") or item.get("quote_code"),
        "formula_width": memory.get("formula_width") or item.get("width"),
        "formula_height": memory.get("formula_height") or item.get("height"),
        "formula_width2": memory.get("formula_width2") or item.get("width2"),
        "formula_height2": memory.get("formula_height2") or item.get("height2"),
        "formula_width3": memory.get("formula_width3") or item.get("width3"),
        "formula_height3": memory.get("formula_height3") or item.get("height3"),
        "formula_length": memory.get("formula_length") or item.get("length"),
        "formula_radius": memory.get("formula_radius") or item.get("radius"),
        "formula_angle": memory.get("formula_angle") or item.get("angle"),
        "formula_area": memory.get("formula_area"),
        "formula_unit_price": memory.get("formula_unit_price"),
        "area_multiplier": memory.get("area_multiplier") or item.get("area_multiplier"),
        "accessory_area": memory.get("accessory_area"),
        "accessory_unit_price": memory.get("accessory_unit_price"),
        "reference_source": "AI quote memory",
        "price_source_status": "quote_memory",
        "pricing_confidence": "high",
        "can_auto_quote": True,
        "memory_source_project": memory.get("source_project"),
        "memory_source_customer": memory.get("source_customer"),
        "material_cost": round(line_total, 2),
        "labor_cost": 0.0,
        "accessory_cost": 0.0,
        "installation_cost": 0.0,
        "painting_cost": 0.0,
        "insulation_cost": 0.0,
        "waste_cost": 0.0,
        "subtotal": round(line_total, 2),
        "profit": 0.0,
        "vat": 0.0,
        "grand_total": round(line_total, 2),
    })
    if package_qty is not None:
        updated["package_quantity"] = package_qty
        updated["package_length_m"] = package_length_m
        updated["package_unit_price"] = package_unit_price
        updated["quote_output_quantity"] = package_qty
        updated["quote_output_unit"] = memory.get("unit") or item.get("unit")
        updated["quote_output_unit_price"] = package_unit_price
    if memory.get("formula_area"):
        try:
            updated["calculated_area"] = round(float(memory.get("formula_area") or 0) * qty, 4)
        except (TypeError, ValueError):
            pass
    return updated


def _suppress_unmatched_trained_quote_price(item: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(item)
    warnings = list(updated.get("warnings") or [])
    warnings.append(
        "Không tìm thấy đơn giá trong báo giá đã học; đang giữ giá tính theo bảng giá/quy tắc hiện tại và cần kiểm tra trước khi phát hành."
    )
    updated["warnings"] = warnings
    return updated


def _quote_memory_part_tokens(text: str) -> List[str]:
    raw_tokens = re.findall(r"[a-z0-9]+", text.lower())
    tokens: List[str] = []
    for token in raw_tokens:
        if token == "louver":
            token = "lover"
        if token == "lcct":
            tokens.extend(["luoi", "chan", "con", "trung"])
            continue
        tokens.append(token)
    ignored = {
        "kt", "mm", "bo", "cai", "m2", "m", "va", "kem", "theo", "ban",
        "ve", "lap", "dat", "gia", "cong", "tao", "cap", "gio",
    }
    return [token for token in tokens if len(token) > 1 and token not in ignored]


def _dimension_match_score(item: Dict[str, Any], memory: Dict[str, Any]) -> int:
    score = 0
    for key in ("width", "height", "diameter"):
        item_value = item.get(key)
        memory_value = memory.get(key)
        if item_value and memory_value:
            try:
                if abs(float(item_value) - float(memory_value)) <= 1:
                    score += 3
            except (TypeError, ValueError):
                pass
    return score


def _composite_quote_memory_price(item: Dict[str, Any], memory_rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    description = quote_memory_description_key(item)
    if "+" not in description or not memory_rows:
        return None

    parts = [part.strip() for part in description.split("+") if part.strip()]
    if len(parts) < 2:
        return None

    used_signatures = set()
    selected_rows: List[Dict[str, Any]] = []

    for part in parts:
        part_tokens = _quote_memory_part_tokens(part)
        if not part_tokens:
            return None

        candidates = []
        for row in memory_rows:
            signature = row.get("signature")
            if signature in used_signatures:
                continue
            row_description = str(row.get("normalized_description") or "")
            row_tokens = set(_quote_memory_part_tokens(row_description))
            if not all(token in row_tokens for token in part_tokens):
                continue
            price = float(row.get("quote_unit_price") or 0)
            if price <= 0:
                continue
            score = len(part_tokens) * 10 + _dimension_match_score(item, row)
            candidates.append((score, row))

        if not candidates:
            return None

        candidates.sort(key=lambda pair: pair[0], reverse=True)
        selected = candidates[0][1]
        used_signatures.add(selected.get("signature"))
        selected_rows.append(selected)

    unit_price = sum(float(row.get("quote_unit_price") or 0) for row in selected_rows)
    if unit_price <= 0:
        return None

    source_files = sorted({str(row.get("source_file") or "") for row in selected_rows if row.get("source_file")})
    source_projects = sorted({str(row.get("source_project") or "") for row in selected_rows if row.get("source_project")})
    specs = [str(row.get("quote_material_spec") or "").strip() for row in selected_rows if row.get("quote_material_spec")]
    brands = [str(row.get("quote_brand") or "").strip() for row in selected_rows if row.get("quote_brand")]
    parts_label = " + ".join(str(row.get("normalized_description") or "") for row in selected_rows)

    return {
        "quote_unit_price": unit_price,
        "quote_material_spec": " + ".join(specs) if specs else None,
        "quote_brand": brands[0] if brands else None,
        "quote_note": f"Gộp đơn giá từ báo giá đã học: {parts_label}",
        "source_project": source_projects[0] if source_projects else None,
        "source_customer": selected_rows[0].get("source_customer"),
        "source_file": source_files[0] if source_files else None,
        "trained_count": sum(int(row.get("trained_count") or 0) for row in selected_rows),
        "composite": True,
        "composite_parts": len(selected_rows),
    }


def _pop_sequential_quote_memory(item: Dict[str, Any], quote_memory: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    queues = quote_memory.get("__queues__", {})
    queue = queues.get(quote_memory_item_description_lookup_key(item))
    if queue:
        return queue.pop(0)

    description_queue = queues.get(quote_memory_item_description_only_lookup_key(item)) or []
    for idx, memory in enumerate(description_queue):
        converted = _convert_memory_unit_price_if_needed(item, memory)
        if converted:
            description_queue.pop(idx)
            return converted
    return None


def _convert_memory_unit_price_if_needed(item: Dict[str, Any], memory: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    item_unit = _unit_key(item.get("unit"))
    memory_unit = _unit_key(memory.get("unit"))
    if item_unit == memory_unit:
        return memory

    note = normalize_text(memory.get("quote_note") or "")
    unit_price = float(memory.get("quote_unit_price") or 0)
    if unit_price <= 0:
        return None

    per_piece_to_meter = re.search(r"1\s*(?:ong|cuon)\s*=\s*(\d+(?:[\.,]\d+)?)\s*m", note)
    if item_unit == "m" and memory_unit in {"ong", "cuon"} and per_piece_to_meter:
        length_m = float(per_piece_to_meter.group(1).replace(",", "."))
        if length_m > 0:
            display_unit = memory.get("unit") or memory_unit
            converted = dict(memory)
            converted["quote_unit_price"] = unit_price / length_m
            converted["package_length_m"] = length_m
            converted["package_unit_price"] = unit_price
            converted["quote_note"] = f"{memory.get('quote_note') or ''} | Tự quy đổi {display_unit} sang m, làm tròn lên nguyên {display_unit}"
            return converted

    return None


def _unit_key(unit: Any) -> str:
    normalized = normalize_text(unit)
    if normalized in {"met", "meter", "m"}:
        return "m"
    if normalized in {"cai", "pcs", "pc"}:
        return "cai"
    if normalized in {"bo", "set"}:
        return "bo"
    if "ong" in normalized:
        return "ong"
    if "cuon" in normalized:
        return "cuon"
    return normalized

def run_project_pricing(
    items: List[Dict[str, Any]],
    overrides: Dict[str, float] = None,
    price_overrides: Dict[Tuple[str, float], float] = None,
    quote_memory_source_file: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, float], List[str]]:
    """
    Runs the pricing calculations for a whole list of parsed items.
    Returns: (calculated_items, summary_totals, global_warnings)
    """
    coeffs = {} if product_only_mode() else get_coefficient_rules()
    pricing = {} if product_only_mode() else get_pricing_rules()
    product_rules = get_product_pricing_rules()
    if price_overrides:
        pricing.update(price_overrides)

    # Apply overrides from frontend
    if overrides:
        for k, v in overrides.items():
            if k in coeffs and v is not None:
                coeffs[k]["value"] = v

    calculated_items = []
    global_warnings = []
    if product_only_mode():
        quote_memory = {}
        quote_memory_rows = []
        rag_memory_rows = []
    else:
        quote_memory = quote_memory_lookup(items, source_file=quote_memory_source_file)
        quote_memory_rows = quote_memory.get("__rows__", [])
        rag_memory_rows = quote_memory_candidate_rows(source_file=quote_memory_source_file) if items else []

    # Aggregators
    total_area = 0.0
    total_material = 0.0
    total_labor = 0.0
    total_accessory = 0.0
    total_installation = 0.0
    total_painting = 0.0
    total_insulation = 0.0
    total_waste = 0.0
    total_subtotal = 0.0
    total_profit = 0.0
    total_vat = 0.0
    selling_price_memory_count = 0

    # For duplicate mark check
    marks = set()
    duplicate_marks = set()

    for item in items:
        # Check for duplicate mark
        m = item.get("mark")
        if m:
            if m in marks:
                duplicate_marks.add(m)
            else:
                marks.add(m)

        calc_item = calculate_item_cost(item, coeffs, pricing, product_rules)
        memory = None
        if not product_only_mode():
            memory = _pop_sequential_quote_memory(item, quote_memory)
            if not memory:
                memory = (
                    quote_memory.get(quote_memory_signature(item))
                    or quote_memory.get(quote_memory_item_description_lookup_key(item))
                )
            if memory and memory.get("ambiguous"):
                memory = None
            if not memory:
                memory = _composite_quote_memory_price(item, quote_memory_rows)
            if not memory:
                rag_threshold = float(os.getenv("RAG_AUTO_APPLY_THRESHOLD", "0.92") or 0.92)
                rag_candidate = best_quote_memory_candidate(item, rag_memory_rows, min_apply_score=rag_threshold)
                if rag_candidate and rag_candidate.get("rag_auto_apply"):
                    memory = rag_candidate
                    memory["rag_source"] = True
                elif rag_candidate and float(rag_candidate.get("rag_similarity_score") or 0) >= 0.72:
                    warnings = list(calc_item.get("warnings") or [])
                    warnings.append(
                        "RAG tìm thấy báo giá cũ tương tự "
                        f"({float(rag_candidate.get('rag_similarity_score') or 0):.0%}) nhưng chưa đủ chắc để tự áp giá. "
                        "Sales/QS cần kiểm tra hoặc cho AI học báo giá chuẩn."
                    )
                    calc_item["warnings"] = warnings
        if memory:
            calc_item = _apply_quote_memory_price(calc_item, memory)
            if memory.get("composite"):
                calc_item["reference_source"] = "AI quote memory composite"
                calc_item["composite_parts"] = memory.get("composite_parts", 0)
            if memory.get("rag_source"):
                calc_item["reference_source"] = "RAG bộ nhớ AI"
                calc_item["rag_similarity_score"] = memory.get("rag_similarity_score")
                calc_item["pricing_confidence"] = "needs_confirmation"
                calc_item["can_auto_quote"] = False
            calc_item["warnings"] = [
                warning for warning in calc_item.get("warnings", [])
                if not (
                    warning.startswith("No pricing rules found")
                    or warning.startswith("Price for ")
                    or warning == "Missing or invalid thickness."
                    or warning.startswith("Chưa có quy tắc đơn giá")
                    or warning.startswith("Chưa có đơn giá độ dày")
                    or warning == "Thiếu hoặc sai độ dày vật liệu."
                )
            ]
            selling_price_memory_count += 1
        elif quote_memory_source_file:
            calc_item = _suppress_unmatched_trained_quote_price(calc_item)
        calculated_items.append(calc_item)

        # Accumulate sums
        total_area += calc_item["calculated_area"]
        total_material += calc_item["material_cost"]
        total_labor += calc_item["labor_cost"]
        total_accessory += calc_item["accessory_cost"]
        total_installation += calc_item["installation_cost"]
        total_painting += calc_item["painting_cost"]
        total_insulation += calc_item["insulation_cost"]
        total_waste += calc_item["waste_cost"]
        total_subtotal += calc_item["subtotal"]
        total_profit += calc_item["profit"]
        total_vat += calc_item["vat"]

    # Append duplicate mark warnings to the items that have them
    if duplicate_marks:
        global_warnings.append(f"Duplicate marks detected: {', '.join(duplicate_marks)}")
        for calc_item in calculated_items:
            if calc_item.get("mark") in duplicate_marks:
                calc_item["warnings"].append("Duplicate product mark.")

    # Calculate Project Level Fixed Charges
    item_count = len(calculated_items)
    uses_trained_selling_prices = item_count > 0 and selling_price_memory_count == item_count
    if product_only_mode():
        uses_trained_selling_prices = False
    if 0 < selling_price_memory_count < item_count:
        global_warnings.append(
            f"Bộ nhớ AI chỉ khớp {selling_price_memory_count}/{item_count} dòng. "
            "Không được phát hành chính thức cho đến khi QS kiểm tra các dòng chưa khớp và phần phí tổng."
        )
    fixed_trans = 0.0 if (uses_trained_selling_prices or product_only_mode()) else coeffs.get("transportation_total", {}).get("value", 0.0)
    fixed_mach = 0.0 if (uses_trained_selling_prices or product_only_mode()) else coeffs.get("machinery_total", {}).get("value", 0.0)

    # Subtotal with fixed charges and project-level commercial allowances.
    base_project_subtotal = total_subtotal + fixed_trans + fixed_mach
    management_fee = 0.0 if (uses_trained_selling_prices or product_only_mode()) else base_project_subtotal * coeffs.get("management_fee_pct", {}).get("value", 0.0)
    risk_fee = 0.0 if (uses_trained_selling_prices or product_only_mode()) else base_project_subtotal * coeffs.get("risk_fee_pct", {}).get("value", 0.0)
    project_subtotal = base_project_subtotal + management_fee + risk_fee

    project_profit = 0.0 if (uses_trained_selling_prices or product_only_mode()) else project_subtotal * coeffs.get("profit_pct", {}).get("value", 0.0)
    project_vat = 0.0 if product_only_mode() else (project_subtotal + project_profit) * coeffs.get("vat_pct", {}).get("value", 0.0)
    project_grand_total = project_subtotal + project_profit + project_vat

    summary = {
        "total_area": round(total_area, 2),
        "total_material": round(total_material, 2),
        "total_labor": round(total_labor, 2),
        "total_accessory": round(total_accessory, 2),
        "total_installation": round(total_installation, 2),
        "total_painting": round(total_painting, 2),
        "total_insulation": round(total_insulation, 2),
        "total_waste": round(total_waste, 2),
        "items_subtotal": round(total_subtotal, 2),
        "transportation": round(fixed_trans, 2),
        "machinery": round(fixed_mach, 2),
        "management_fee": round(management_fee, 2),
        "risk_fee": round(risk_fee, 2),
        "subtotal": round(project_subtotal, 2),
        "profit": round(project_profit, 2),
        "vat": round(project_vat, 2),
        "grand_total": round(project_grand_total, 2),
        "quote_memory_matched": selling_price_memory_count,
        "quote_memory_full_match": uses_trained_selling_prices,
        "quote_memory_partial_match": 0 < selling_price_memory_count < item_count,
    }

    # Verify if any item has warnings
    for idx, calc_item in enumerate(calculated_items):
        if calc_item["warnings"]:
            global_warnings.append(f"Item #{calc_item.get('item_no') or idx+1} ({calc_item.get('mark')}): {'; '.join(calc_item['warnings'])}")

    return calculated_items, summary, global_warnings




