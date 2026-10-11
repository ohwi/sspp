"""Decode Stella Sora's native Potential Preselection Base64 share codes.

This format carries exact target levels, independently of sspp priority marks.
CharPotential array order is part of the protocol; never sort those arrays.
See tests/fixtures/ingame_presets/README.md for pinned primary sources.
"""
import base64
import binascii
import re
import struct
from collections.abc import Mapping

FORMAT = "stella_sora_ingame_preset"
POSITIONS = ("master", "assist1", "assist2")
MAX_CODE_CHARS = 4096
SCHEMA_FIELDS = (
    "MasterSpecificPotentialIds", "MasterNormalPotentialIds",
    "AssistSpecificPotentialIds", "AssistNormalPotentialIds",
    "CommonPotentialIds",
)


def _load_catalog():
    try:
        from .character_catalog import load_catalog
    except ImportError:
        from src.character_catalog import load_catalog
    try:
        return load_catalog()
    except (OSError, ValueError) as exc:
        raise ValueError(f"캐릭터 카탈로그를 읽을 수 없습니다: {exc}") from exc


def _base64_bytes(code):
    if not isinstance(code, str) or not code or len(code) > MAX_CODE_CHARS:
        raise ValueError("게임 프리셋 코드는 비어 있지 않은 Base64 문자열이어야 합니다")
    encoded = re.sub(r"[ \t\r\n\f\v]", "", code)
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", encoded):
        raise ValueError("잘못된 게임 프리셋 Base64 문자입니다")
    if len(encoded) % 4 == 1:
        raise ValueError("잘못된 게임 프리셋 Base64 길이입니다")
    # Missing padding is accepted; explicit padding must already be correct.
    if "=" in encoded and len(encoded) % 4:
        raise ValueError("잘못된 게임 프리셋 Base64 패딩입니다")
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        payload = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("게임 프리셋 Base64를 해독할 수 없습니다") from exc
    # Reject discarded nonzero Base64 pad bits rather than silently normalizing.
    if base64.b64encode(payload).decode("ascii").rstrip("=") != encoded.rstrip("="):
        raise ValueError("비정규 게임 프리셋 Base64 표현입니다")
    return payload


def _slot_schema(catalog, char_id, position):
    characters = catalog.get("characters")
    if not isinstance(characters, Mapping):
        raise ValueError("캐릭터 카탈로그 형식이 올바르지 않습니다")
    character = characters.get(str(char_id))
    if not isinstance(character, Mapping):
        raise ValueError(f"알 수 없는 프리셋 캐릭터 ID: {char_id}")
    schema = character.get("preset_schema")
    if not isinstance(schema, Mapping):
        raise ValueError(f"캐릭터 {char_id}의 프리셋 배열 자료가 없습니다")
    for field in SCHEMA_FIELDS:
        values = schema.get(field)
        if not isinstance(values, list) or any(
            type(value) is not int or not 0 < value <= 0xffffffff for value in values
        ):
            raise ValueError(f"캐릭터 {char_id}의 {field} 배열이 올바르지 않습니다")
    prefix = "Master" if position == "master" else "Assist"
    groups = (
        (schema[prefix + "SpecificPotentialIds"], 1),
        (schema[prefix + "NormalPotentialIds"], 3),
        (schema["CommonPotentialIds"], 3),
    )
    ids = [potential_id for values, _ in groups for potential_id in values]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"캐릭터 {char_id}의 프리셋 잠재력 ID가 비어 있거나 중복됩니다")
    known = character.get("potentials")
    if isinstance(known, Mapping) and any(str(value) not in known for value in ids):
        raise ValueError(f"캐릭터 {char_id}의 프리셋에 알 수 없는 잠재력 ID가 있습니다")
    return groups


def decode_preset(code, catalog=None):
    """Return source metadata and ordered slots with every exact target level.

    Successful target_levels dictionaries include zeros. Native special flags
    represent target 0/1; normal/common fields represent target 0..configured max.
    Any invalid input raises ValueError and returns no partial decoded payload.
    """
    payload = _base64_bytes(code)
    if len(payload) < 12:
        raise ValueError("게임 프리셋 캐릭터 헤더가 잘렸습니다")
    char_ids = struct.unpack(">III", payload[:12])
    if 0 in char_ids:
        raise ValueError("게임 프리셋에는 빈 캐릭터 ID 0을 사용할 수 없습니다")
    if len(set(char_ids)) != 3:
        raise ValueError("게임 프리셋 캐릭터가 중복됩니다")
    catalog = _load_catalog() if catalog is None else catalog
    if not isinstance(catalog, Mapping):
        raise ValueError("캐릭터 카탈로그 형식이 올바르지 않습니다")
    max_level = catalog.get("ingame_preset_max_level")
    if type(max_level) is not int or not 1 <= max_level <= 7:
        raise ValueError("게임 프리셋 최대 목표 레벨 설정이 없습니다")
    groups_by_slot = [
        _slot_schema(catalog, char_id, position)
        for position, char_id in zip(POSITIONS, char_ids)
    ]
    expected_bits = 96 + sum(
        len(ids) * width for groups in groups_by_slot for ids, width in groups
    )
    if len(payload) != (expected_bits + 7) // 8:
        raise ValueError("게임 프리셋 길이가 잠재력 배열 자료와 다릅니다")
    # One integer shifts MSB-first through the complete body, including the tail.
    body = int.from_bytes(payload[12:], "big")
    remaining_bits = (len(payload) - 12) * 8
    slots = []
    for position, char_id, groups in zip(POSITIONS, char_ids, groups_by_slot):
        targets = {}
        for ids, width in groups:
            for potential_id in ids:
                remaining_bits -= width
                target = (body >> remaining_bits) & ((1 << width) - 1)
                if target > max_level:
                    raise ValueError(
                        f"게임 프리셋 목표 레벨 범위 오류: {position}/{potential_id}={target}"
                    )
                targets[potential_id] = target
        slots.append({
            "position": position,
            "char_id": char_id,
            "target_levels": targets,
        })
    if remaining_bits and body & ((1 << remaining_bits) - 1):
        raise ValueError("게임 프리셋 마지막 패딩 비트가 0이 아닙니다")
    source = {"format": FORMAT, "code": code, "payload_hex": payload.hex()}
    if "source" in catalog:
        source["catalog_source"] = catalog["source"]
    return {"source": source, "slots": slots}
