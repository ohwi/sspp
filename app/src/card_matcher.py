"""Conservative card matching with aligned component checks and ambiguity refusal.

Enriched records contain gray/bgr arrays and public-data metadata. Legacy arrays
retain the existing grayscale matcher, with a runner-up margin and exact-pixel
tie protection. Scores are correlation measurements, not probabilities.
"""
from __future__ import annotations

import hashlib
import re

import cv2
import numpy as np

MIN_FULL_SCORE = 0.75
MIN_MARGIN = 0.03
MIN_CORNER_SCORE = 0.45
MAX_HUE_DISTANCE = 18.0  # OpenCV's 0..179 hue units; checked only with color input.


def _gray(image):
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or not image.size:
        return None
    if image.ndim == 2:
        return image
    if image.ndim == 3 and image.shape[2] in (3, 4):
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
    return None


def _bgr(image):
    if image.ndim == 3 and image.shape[2] in (3, 4):
        return image[:, :, :3]
    return None


def _pid(filename):
    return str(filename).removesuffix(".png")


def _normalized_allowed(values):
    if values is None:
        return None
    return {_pid(value) for value in values}


def _array_fingerprint(image):
    return hashlib.sha256(str(image.shape).encode("ascii") + image.tobytes()).hexdigest()


def _ncc(first, second):
    if first.shape != second.shape or not first.size:
        return None
    a, b = first.astype(np.float32).ravel(), second.astype(np.float32).ravel()
    a -= a.mean()
    b -= b.mean()
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-6:
        return None
    value = float(np.dot(a, b) / denominator)
    return float(np.clip(value, -1, 1)) if np.isfinite(value) else None


def _maximum(image, template, full_height=None):
    if (template.ndim != 2 or template.dtype != image.dtype or not template.size
            or np.ptp(template) == 0 or template.shape[0] > image.shape[0]
            or template.shape[1] > image.shape[1]):
        return None
    scores = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    if full_height is not None:
        scores = scores[:image.shape[0] - full_height + 1]
    if not scores.size:
        return None
    valid = np.isfinite(scores) & (scores >= -1.000001) & (scores <= 1.000001)
    if not np.any(valid):
        return None
    scores = np.where(valid, scores, -np.inf)
    y, x = np.unravel_index(np.argmax(scores), scores.shape)
    return float(scores[y, x]), (int(x), int(y))


def _region(image, coordinates):
    x1, y1, x2, y2 = [int(value) for value in coordinates]
    if x1 < 0 or y1 < 0 or x2 > image.shape[1] or y2 > image.shape[0] or x1 >= x2 or y1 >= y2:
        return None
    return image[y1:y2, x1:x2]


def validate_enriched_metadata(meta, shape):
    """Shared loader/matcher contract; return False for malformed metadata."""
    if (not isinstance(shape, (tuple, list)) or len(shape) < 2
            or any(type(value) is not int or value <= 0 for value in shape[:2])
            or not isinstance(meta, dict) or not isinstance(meta.get("glyph"), str)
            or not meta["glyph"].strip()):
        return False
    for field, choices in (("corner", (0, 1, 2, 3)), ("stype", (41, 42)), ("rarity", (1, 2))):
        if type(meta.get(field)) is not int or meta[field] not in choices:
            return False
    if meta["stype"] == 42 and meta["corner"] != 0:
        return False
    for field, default in (("glyph_region", [0, 0, shape[1], 105]),
                           ("corner_region", [50, 105, 105, 150])):
        coordinates = meta.get(field, default)
        if (not isinstance(coordinates, (list, tuple)) or len(coordinates) != 4
                or any(type(value) is not int for value in coordinates)):
            return False
        x1, y1, x2, y2 = coordinates
        if not (0 <= x1 < x2 <= shape[1] and 0 <= y1 < y2 <= shape[0]):
            return False
        if field == "glyph_region" and list(coordinates[:2]) != [0, 0]:
            return False
    return True


def _valid_enriched(value):
    """Malformed calibrated records must never silently become legacy records."""
    template, color, meta = value.get("gray"), value.get("bgr"), value.get("meta")
    return bool(_gray(template) is not None and template.ndim == 2
                and isinstance(color, np.ndarray) and color.dtype == np.uint8
                and color.ndim == 3 and color.shape[2] == 3
                and color.shape[:2] == template.shape
                and validate_enriched_metadata(meta, template.shape))


def _hue_distance(first, second):
    medians = []
    for image in (first, second):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        selected = (hsv[:, :, 1] >= 80) & (hsv[:, :, 2] >= 80)
        if int(selected.sum()) < 100:
            return None
        medians.append(float(np.median(hsv[:, :, 0][selected])))
    difference = abs(medians[0] - medians[1])
    return min(difference, 180 - difference)


def match_card(image, char_key, templates, allowed_ids=None):
    """Return ``(filename | None, score, diagnostics)`` for a single captured ROI.

    ``templates`` may be char->filename->record or filename->record. Each record
    is a legacy gray array or {gray, bgr, meta}. allowed_ids filters candidates
    before ranking; target level zero must remain in the caller's allowed pool.
    Components are compared at one glyph-family alignment, never independently
    searched elsewhere in the image. No expected PID is accepted by this API.
    """
    diagnostics = {"accepted": False, "reason": "invalid_input", "candidates": [],
                   "color_checked": False, "allowed_pool_supplied": allowed_ids is not None}
    gray = _gray(image)
    if gray is None or not isinstance(templates, dict):
        return None, 0.0, diagnostics
    bgr = _bgr(image)
    pool = templates.get(char_key, {}) if char_key in templates else templates
    if not isinstance(pool, dict):
        return None, 0.0, diagnostics
    allowed = _normalized_allowed(allowed_ids)
    records = []
    invalid = []
    for filename, value in sorted(pool.items()):
        if not re.fullmatch(r"\d+\.png", str(filename)) or (allowed is not None and _pid(filename) not in allowed):
            continue
        if isinstance(value, dict):
            if not _valid_enriched(value):
                invalid.append(filename)
                continue
            template = value.get("gray")
            meta, color = value.get("meta", {}), value.get("bgr")
        else:
            template, meta, color = value, {}, None
        if _gray(template) is None or template.ndim != 2:
            continue
        if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1] or np.ptp(template) == 0:
            continue
        enhanced = bool(meta.get("glyph") and isinstance(color, np.ndarray)
                        and color.shape[:2] == template.shape)
        records.append({"filename": filename, "gray": template, "bgr": color,
                        "meta": meta, "enhanced": enhanced})
    if invalid:
        diagnostics.update(reason="invalid_enriched_metadata", invalid_records=invalid)
        return None, 0.0, diagnostics
    if not records:
        diagnostics["reason"] = "empty_candidate_pool"
        return None, 0.0, diagnostics
    diagnostics["candidate_count"] = len(records)
    families = {}
    for record in records:
        # Pink and normal sprites use different glyph geometry; a location from
        # one profile cannot be copied to the other without an affine correction.
        family = (record["meta"]["glyph"] + ":" + str(record["meta"]["stype"])
                  + ":" + str(record["meta"]["rarity"])) if record["enhanced"] else "legacy:" + record["filename"]
        families.setdefault(family, []).append(record)
    ranked = []
    for family, members in families.items():
        if not members[0]["enhanced"]:
            record = members[0]
            maximum = _maximum(gray, record["gray"])
            if maximum:
                score, location = maximum
                ranked.append({"filename": record["filename"], "score": score,
                               "full_score": score, "ranking_score": score, "location": list(location),
                               "component_valid": True, "method": "legacy_gray", "family": family})
            continue
        # Same-glyph candidates share one measured alignment. Changing a badge
        # must not move its comparison window to another location in the ROI.
        anchor = None
        for record in members:
            template = record["gray"]
            coordinates = record["meta"].get("glyph_region", [0, 0, template.shape[1], 105])
            # Calibration stores top-aligned regions so local coordinates equal
            # the complete template's coordinates; unsupported records fail closed.
            if coordinates[:2] != [0, 0]:
                continue
            glyph = _region(template, coordinates)
            if glyph is None:
                continue
            maximum = _maximum(gray, glyph, template.shape[0])
            if maximum and (anchor is None or maximum[0] > anchor[0]):
                anchor = maximum
        if anchor is None:
            continue
        glyph_score, (x, y) = anchor
        for record in members:
            template, meta = record["gray"], record["meta"]
            h, w = template.shape
            patch = gray[y:y + h, x:x + w]
            full = _ncc(patch, template)
            if full is None:
                continue
            corner, hue = None, None
            # Enriched templates require observed color evidence. Grayscale
            # legacy records remain available through their separate fallback.
            component_valid = bgr is not None
            if meta.get("corner", 0):
                region = meta.get("corner_region", [50, 105, 105, 150])
                actual, expected = _region(patch, region), _region(template, region)
                corner = _ncc(actual, expected) if actual is not None and expected is not None else None
                component_valid = component_valid and corner is not None and corner >= MIN_CORNER_SCORE
            if bgr is not None:
                color_patch = bgr[y:y + h, x:x + w]
                hue = _hue_distance(color_patch, record["bgr"])
                diagnostics["color_checked"] = True
                component_valid = component_valid and hue is not None and hue <= MAX_HUE_DISTANCE
            score = .65 * full + .35 * corner if corner is not None else full
            ranked.append({"filename": record["filename"], "score": full,
                           "ranking_score": float(score),
                           "full_score": full, "glyph_score": glyph_score,
                           "corner_score": corner, "hue_distance": hue,
                           "location": [x, y], "component_valid": bool(component_valid),
                           "method": "aligned_components", "family": family})
    ranked.sort(key=lambda row: (-row["ranking_score"], row["filename"]))
    diagnostics["candidates"] = ranked[:5]
    if not ranked:
        diagnostics["reason"] = "no_finite_match"
        return None, 0.0, diagnostics
    # Invalid component candidates cannot win, but remain visible in diagnostics.
    valid = [row for row in ranked if row["component_valid"]
             and row["full_score"] >= MIN_FULL_SCORE]
    if not valid:
        if bgr is None and any(record["enhanced"] for record in records):
            diagnostics["reason"] = "missing_color"
        else:
            diagnostics["reason"] = "component_mismatch" if ranked[0]["full_score"] >= MIN_FULL_SCORE else "low_score"
        return None, float(ranked[0]["score"]), diagnostics
    best = valid[0]
    diagnostics["best"] = best
    lookup = {row["filename"]: row for row in records}
    observed_color = bgr is not None
    winning = lookup[best["filename"]]
    pixels = winning["bgr"] if observed_color and winning["enhanced"] else winning["gray"]
    fingerprint = _array_fingerprint(pixels)
    identical = []
    for record in records:
        other = record["bgr"] if observed_color and record["enhanced"] else record["gray"]
        if _array_fingerprint(other) == fingerprint:
            identical.append(record["filename"])
    if len(identical) > 1:
        diagnostics.update(reason="identical_pixels", ambiguous_ids=identical)
        return None, float(best["score"]), diagnostics
    # A near-failing Corner is still conflicting evidence. Removing it before
    # computing the margin would give a sole, possibly wrong survivor margin=1.
    contenders = [row for row in ranked if row["filename"] != best["filename"]
                  and row["full_score"] >= MIN_FULL_SCORE
                  and (row["method"] == "legacy_gray" or
                       (row["hue_distance"] is not None and row["hue_distance"] <= MAX_HUE_DISTANCE))]
    margin = best["ranking_score"] - contenders[0]["ranking_score"] if contenders else 1.0
    diagnostics["margin"] = float(margin)
    if margin < MIN_MARGIN:
        diagnostics["reason"] = "insufficient_margin"
        return None, float(best["score"]), diagnostics
    diagnostics.update(accepted=True, reason="accepted")
    return best["filename"], float(best["score"]), diagnostics
