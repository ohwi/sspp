#!/usr/bin/env python3
"""Generate unvalidated 65 px face candidates from pinned public game assets.

Default output is work/generated-templates, outside app/resources. Sources are
verified against immutable Git trees before decoding. Existing production faces
are never replaced. Requires requests, numpy and opencv-python.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = REPO_ROOT.parent
SOURCES = {
    "sstoy": {"repository": "JforPlay/sstoy", "commit": "34f57606015277cf9da76a2c7cc14fa1b68ccbbf"},
    "ssassets": {"repository": "AutumnVN/ssassets", "commit": "a970adc8ae875ffbfbad98a138ba569402959b4b"},
}
WORKER_DIFF_MAX = 0.15


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def git_blob_sha(body: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(body)).encode("ascii") + b"\0" + body).hexdigest()


def production_snapshot(root: Path) -> dict:
    files = {str(p.relative_to(root)).replace("\\", "/"): sha256(p.read_bytes())
             for p in sorted(root.rglob("*")) if p.is_file() and p.name != "templates.cache"}
    digest = sha256(json.dumps(files, sort_keys=True).encode("utf-8"))
    return {"sha256": digest, "file_count": len(files), "files": files}


def decode(body: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError("Cannot decode verified image")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return image


def circular_mask(width: int = 65, height: int = 65) -> np.ndarray:
    mask = np.zeros((height, width), np.uint8)
    cv2.circle(mask, (width // 2, height // 2), min(width, height) // 2, 255, thickness=-1)
    return mask


def circle_candidate(image: np.ndarray, anchor: str, ring_px: int = 0):
    height, width = image.shape[:2]
    side = min(width, height)
    left = (width - side) // 2
    top = height - side if anchor == "bottom" else 0
    crop = image[top:top + side, left:left + side]
    candidate = cv2.resize(crop, (65, 65), interpolation=cv2.INTER_AREA)
    mask = circular_mask()
    candidate[:, :, 3] = np.minimum(candidate[:, :, 3], mask)
    candidate[mask == 0, :3] = 0
    if ring_px:
        ring = np.zeros((65, 65), np.uint8)
        cv2.circle(ring, (32, 32), 31, 255, thickness=ring_px)
        candidate[ring > 0, :3] = 255
        candidate[ring > 0, 3] = 255
    transform = {
        "crop_xywh": [left, top, side, side], "crop_anchor": anchor,
        "resize_wh": [65, 65], "interpolation": "INTER_AREA",
        "mask_center_xy": [32, 32], "mask_radius_px": 32,
        "outside_mask_rgb": [0, 0, 0], "synthetic_white_ring_px": ring_px,
        "source_alpha_preserved_inside_mask": True,
    }
    return candidate, transform


def runtime_diff(reference: np.ndarray, candidate: np.ndarray) -> float:
    if reference.ndim == 3:
        reference = cv2.cvtColor(reference, cv2.COLOR_BGRA2GRAY if reference.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
    if candidate.ndim == 3:
        candidate = cv2.cvtColor(candidate, cv2.COLOR_BGRA2GRAY if candidate.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
    mask = circular_mask(candidate.shape[1], candidate.shape[0])
    return float(cv2.matchTemplate(reference, candidate, cv2.TM_SQDIFF_NORMED, mask=mask)[0, 0])


class VerifiedSources:
    def __init__(self, audit: Path, output: Path, offline: bool):
        self.audit, self.output, self.offline = audit, output, offline
        self.trees = {}
        self.tree_receipts = {}
        for name in SOURCES:
            tree_file = audit / "db-coverage" / (name + "_tree.json")
            body = tree_file.read_bytes()
            tree = json.loads(body)
            if tree.get("truncated"):
                raise ValueError("Truncated source tree")
            if tree.get("sha") != SOURCES[name]["commit"]:
                raise ValueError(f"Pinned source tree SHA mismatch: {name}")
            self.trees[name] = {entry["path"]: entry for entry in tree["tree"] if entry["type"] == "blob"}
            self.tree_receipts[name] = {**SOURCES[name], "cache_file": str(tree_file.resolve()),
                                        "tree_sha": tree["sha"], "cache_sha256": sha256(body),
                                        "pinned_commit_verified": True}
        self.local_pngs = list(audit.rglob("*.png"))

    def get(self, name: str, path: str):
        entry = self.trees[name][path]
        source = SOURCES[name]
        url = f"https://raw.githubusercontent.com/{source['repository']}/{source['commit']}/{path}"
        cache = self.output / "source-cache" / name / path
        origin = "cache"
        if cache.exists():
            body = cache.read_bytes()
        else:
            body = None
            for local in self.local_pngs:
                # Audit files sometimes have an extra char_ prefix.
                if local.name == Path(path).name or local.name == "char_" + Path(path).name:
                    candidate = local.read_bytes()
                    if git_blob_sha(candidate) == entry["sha"]:
                        body = candidate
                        origin = "existing_audit_file"
                        break
            if body is None:
                if self.offline:
                    raise FileNotFoundError(f"Verified source missing from cache: {path}")
                response = requests.get(url, timeout=40)
                response.raise_for_status()
                body = response.content
                origin = "public_raw_url"
        actual_blob = git_blob_sha(body)
        if actual_blob != entry["sha"]:
            raise ValueError(f"Source Git blob mismatch: {path}")
        image = decode(body)
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not cache.exists():
            cache.write_bytes(body)
        receipt = {**source, "path": path, "url": url, "bytes": len(body),
                   "width": image.shape[1], "height": image.shape[0], "channels": image.shape[2],
                   "git_blob_sha": entry["sha"], "git_blob_sha_verified": True,
                   "sha256": sha256(body), "cache_file": str(cache.resolve()), "read_origin": origin}
        return image, receipt


def probes(sources: VerifiedSources, production: Path):
    results = []
    for key, cid in (("amber", 103), ("suntide_willow", 160)):
        reference_path = production / "icons" / (key + ".png")
        if not reference_path.exists():
            continue
        reference_bytes = reference_path.read_bytes()
        reference = decode(reference_bytes)
        suffixes = ("L", "S", "SS", "XL", "SK") if cid == 103 else ("L", "SS")
        for suffix in suffixes:
            kind = "sstoy" if suffix == "L" else "ssassets"
            path = (f"public/assets/char/head_{cid}01_{suffix}.png" if kind == "sstoy" else
                    f"export/assets/assetbundles/icon/head/head_{cid}01_{suffix}.webp")
            if path not in sources.trees[kind]:
                continue
            image, receipt = sources.get(kind, path)
            candidate, transform = circle_candidate(image, "bottom" if suffix == "L" else "top")
            diff = runtime_diff(reference, candidate)
            results.append({"reference_character_id": cid, "reference_key": key,
                            "reference_file": str(reference_path.resolve()),
                            "reference_sha256": sha256(reference_bytes), "source": receipt,
                            "transform": transform, "method": "TM_SQDIFF_NORMED",
                            "comparison_mask": {"width": 65, "height": 65, "center_xy": [32, 32], "radius_px": 32},
                            "diff": diff, "threshold": WORKER_DIFF_MAX,
                            "passes_single_reference_threshold": diff <= WORKER_DIFF_MAX,
                            "independent_live_validation": False})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, default=WORK_ROOT / "asset-audit")
    parser.add_argument("--output-dir", type=Path, default=WORK_ROOT / "generated-templates")
    parser.add_argument("--offline", action="store_true", help="Use only verified cached/audit sources")
    args = parser.parse_args()
    audit, output = args.audit_dir.resolve(), args.output_dir.resolve()
    production = (REPO_ROOT / "app" / "resources" / "templates").resolve()
    if output == production or production in output.parents:
        raise ValueError("Candidate output must remain outside production templates")
    if output == REPO_ROOT or REPO_ROOT in output.parents:
        raise ValueError("Candidate output must remain outside the repository")
    before = production_snapshot(production)
    catalog = read_json(REPO_ROOT / "app" / "resources" / "characters.json")
    config_file = audit / "Character.json"
    config_bytes = config_file.read_bytes()
    expected_config_sha = catalog["source"]["sha256"]["Character.json"]
    if sha256(config_bytes) != expected_config_sha:
        raise ValueError("Pinned Character.json SHA256 mismatch")
    configs = json.loads(config_bytes.decode("utf-8-sig"))
    config_receipt = {"local_file": str(config_file), "sha256": sha256(config_bytes),
                      "expected_sha256": expected_config_sha, "sha256_verified": True}
    present = {p.stem.casefold() for p in (production / "icons").glob("*.png")}
    missing = [(int(cid), char) for cid, char in catalog["characters"].items()
               if char["key"].casefold() not in present]
    output.mkdir(parents=True, exist_ok=True)
    sources = VerifiedSources(audit, output, args.offline)
    comparisons = probes(sources, production)
    calibration_accepted = False  # No common transform has been independently validated.
    jobs = []
    for cid, char in missing:
        default_skin = configs[str(cid)]["DefaultSkinId"]
        advance_skin = configs[str(cid)]["AdvanceSkinId"]
        jobs += [(cid, char, default_skin, "default", "primary_default_L", "sstoy",
                  f"public/assets/char/head_{default_skin}_L.png", "bottom", 0),
                 (cid, char, advance_skin, "advance", "advance_L", "ssassets",
                  f"export/assets/assetbundles/icon/head/head_{advance_skin}_L.webp", "bottom", 0),
                 (cid, char, default_skin, "default", "default_SS", "ssassets",
                  f"export/assets/assetbundles/icon/head/head_{default_skin}_SS.webp", "top", 2)]

    def generate(job):
        cid, char, skin, skin_kind, variant, repository, path, anchor, ring = job
        image, receipt = sources.get(repository, path)
        candidate, transform = circle_candidate(image, anchor, ring)
        relative = (Path("icons") / (char["key"] + ".png") if variant == "primary_default_L" else
                    Path("icons") / "variants" / variant / (char["key"] + ".png"))
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".png", candidate)
        if not ok:
            raise ValueError("Failed to encode candidate")
        body = encoded.tobytes()
        target.write_bytes(body)
        reread = decode(target.read_bytes())
        if reread.shape != (65, 65, 4):
            raise ValueError("Unexpected generated dimensions")
        return {"character_id": cid, "key": char["key"], "name_ko": char["name_ko"],
                "name_en": char["name_en"], "skin_id": skin, "skin_kind": skin_kind,
                "variant": variant, "primary": variant == "primary_default_L",
                "source_kind": "preframed_portrait" if variant.endswith("_L") else "small_portrait",
                "pose_status": "not_verified_against_current_game_for_this_character",
                "status": "candidate_unvalidated", "source": receipt,
                "transform": transform, "relative_output_file": relative.as_posix(),
                "output_file": str(target.resolve()), "width": 65, "height": 65,
                "channels": 4, "format": "png", "bytes": len(body), "sha256": sha256(body),
                "actual_game_match_verified": False}

    with ThreadPoolExecutor(max_workers=4) as pool:
        candidates = list(pool.map(generate, jobs))
    after = production_snapshot(production)
    if before != after:
        raise RuntimeError("Production templates changed during candidate generation")
    primary_count = sum(row["primary"] for row in candidates)
    if primary_count != len(missing):
        raise ValueError("A primary candidate is required for every missing character")
    manifest = {"schema_version": 1, "scope": "Unvalidated face candidates only; no production registration",
                "script": str(Path(__file__).resolve()), "script_sha256": sha256(Path(__file__).read_bytes()),
                "catalog_source": catalog["source"], "source_trees": sources.tree_receipts,
                "character_config": config_receipt,
                "primary_count": primary_count, "variant_count": len(candidates) - primary_count,
                "total_count": len(candidates), "calibration_accepted": calibration_accepted,
                "live_game_validation_performed": False,
                "primary_selection_reason": "Fallback: verified default head_L; common crop has not passed current face references",
                "known_geometry_limit": "Bottom square preserves 140 px circular head in 140x152 L sprites. Portrait pose and in-game outline can differ.",
                "worker_accept_diff_max": WORKER_DIFF_MAX, "reference_comparisons": comparisons,
                "production_before": {k: v for k, v in before.items() if k != "files"},
                "production_after": {k: v for k, v in after.items() if k != "files"},
                "production_unchanged": True, "candidates": candidates}
    manifest_path = output / "faces-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "primary_count": primary_count,
                      "variant_count": len(candidates) - primary_count, "production_unchanged": True}, ensure_ascii=True))


if __name__ == "__main__":
    main()
