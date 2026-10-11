"""Extract reproducible matching crops from an existing, manually prepared game screenshot."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screenshot", type=Path)
    args = parser.parse_args()
    screenshot = args.screenshot.read_bytes()
    if hashlib.sha256(screenshot).hexdigest() != "dba8f9a0491d2173c9193ac0466781a1c3df4a93f3cef72b3620e11524a893d0":
        raise ValueError("기록된 원본 화면과 SHA256이 다릅니다. 다른 화면에는 이 좌표를 적용하지 않습니다.")
    image = cv2.imdecode(np.frombuffer(screenshot, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (900, 1600):
        raise ValueError("검증된 1600×900 윌로(여름) 선택 화면만 사용합니다.")
    image = cv2.resize(image, (1280, 720), interpolation=cv2.INTER_AREA)
    # Coordinates recorded from the Korean client screenshot on 2026-10-12.
    crops = {
        "icons/suntide_willow.png": (487, 135, 65, 65),
        "potentials/suntide_willow/516023.png": (222, 222, 150, 150),
        "potentials/suntide_willow/516022.png": (565, 190, 150, 150),
        "potentials/suntide_willow/516021.png": (899, 225, 150, 150),
    }
    output = ROOT / "app/resources/templates"
    for relative, (x, y, width, height) in crops.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"기존 이미지를 교체하지 않습니다: {target}")
        ok, encoded = cv2.imencode(".png", image[y:y + height, x:x + width])
        if not ok:
            raise ValueError(relative)
        target.write_bytes(encoded.tobytes())
    provenance = {
        "capture_date": "2026-10-12",
        "client": "StellaSora KR, Korean",
        "snapshot_sha256": hashlib.sha256(screenshot).hexdigest(),
        "source_dimensions": [1600, 900],
        "normalized_dimensions": [1280, 720],
        "identity_catalog_revision": "34f57606015277cf9da76a2c7cc14fa1b68ccbbf",
        "crops_xywh": crops,
        "validation_scope": "One observed selection screen; other potentials and skins are unverified.",
    }
    (ROOT / "app/resources/template_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(provenance, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
