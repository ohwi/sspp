"""Decode SSToy share URLs into the existing sspp preset-import contract.

Protocol reference (including frozen v2 maps):
https://github.com/JforPlay/sstoy/blob/34f57606015277cf9da76a2c7cc14fa1b68ccbbf/src/modules/app-saveload.ts

The historical mapped_* field names are retained for formatter compatibility.
Their values are normalized to actual game IDs for both v2 and v3.
"""
import logging
import re
import zlib
from typing import Any, Dict, Tuple
from urllib.parse import unquote, urlsplit

logger = logging.getLogger("SSToyDecoder")

BASE91_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&()*+,./:;<=>?@[]^_`{|}~"'
# Exact upstream raw alphabet. It currently contains only 511 entries, while the
# upstream encoder indexes it by a 16-bit word. Consequently its current v2/v3
# raw encoder emits "undefined"; reject those lossy URLs instead of guessing.
BASE32768_CHARS = '𠀀𠂊𠄓𠆢𠈕𠊧𠌫𠎭𠐻𠒄𠔓𠖻𠘨𠚺𠜎𠞩𠠻𠢹𠤼𠦿𠩂𠫅𠭇𠯉𠱋𠳍𠵏𠷑𠹓𠻕𠽗𠿙𡁛𡃝𡅟𡇡𡉣𡋥𡍧𡏩𡑫𡓭𡕯𡗱𡙳𡛵𡝷𡟹𡡻𡣽𡥿𡨁𡪃𡬅𡮇𡰉𡲋𡴍𡶏𡸑𡺓𡼕𡾗𢀙𢂛𢄝𢆟𢈡𢊣𢋥𢎧𢐩𢒫𢔭𢖯𢘱𢚳𢜵𢞷𢠹𢢻𢤽𢦿𢨁𢪃𢬅𢮇𢰉𢲋𢴍𢶏𢸑𢺓𢼕𢾗𣀙𣂛𣄝𣆟𣈡𣊣𣌥𣎧𣐩𣒫𣔭𣖯𣘱𣚳𣜵𣞷𣠹𣢻𣤽𣦿𣨁𣪃𣬅𣮇𣰉𣲋𣴍𣶏𣸑𣺓𣼕𣾗𤀙𤂛𤄝𤆟𤈡𤊣𤌥𤎧𤐩𤒫𤔭𤖯𤘱𤚳𤜵𤞷𤠹𤢻𤤽𤦿𤨁𤪃𤬅𤮇𤰉𤲋𤴍𤶏𤸑𤺓𤼕𤾗𥀙𥂛𥄝𥆟𥈡𥊣𥌥𥎧𥐩𥒫𥔭𥖯𥘱𥚳𥜵𥞷𥠹𥢻𥤽𥦿𥨁𥪃𥬅𥮇𥰉𥲋𥴍𥶏𥸑𥺓𥼕𥾗𦀙𦂛𦄝𦆟𦈡𦊣𦌥𦎧𦐩𦒫𦔭𦖯𦘱𦚳𦜵𦞷𦠹𦢻𦤽𦦿𦨁𦪃𦬅𦮇𦰉𦲋𦴍𦶏𦸑𦺓𦼕𦾗𧀙𧂛𧄝𧆟𧈡𧊣𧌥𧎧𧐩𧒫𧔭𧖯𧘱𧚳𧜵𧞷𧠹𧢻𧤽𧦿𧨁𧪃𧬅𧮇𧰉𧲋𧴍𧶏𧸑𧺓𧼕𧾗𨀙𨂛𨄝𨆟𨈡𨊣𨌥𨎧𨐩𨒫𨔭𨖯𨘱𨚳𨜵𨞷𨠹𨢻𨤽𨦿𨨁𨪃𨬅𨮇𨰉𨲋𨴍𨶏𨸑𨺓𨼕𨾗𩀙𩂛𩄝𩆟𩈡𩊣𩌥𩎧𩐩𩒫𩔭𩖯𩘱𩚳𩜵𩞷𩠹𩢻𩤽𩦿𩨁𩪃𩬅𩮇𩰉𩲋𩴍𩶏𩸑𩺓𩼕𩾗𪀙𪂛𪄝𪆟𪈡𪊣𪌥𪎧𪐩𪒫𪔭𪖯𪘱𪚳𪜵𪞷𪠹𪢻𪤽𪦿𪨁𪪃𪬅𪮇𪰉𪲋𪴍𪶏𪸑𪺓𪼕𪾗𫀙𫂛𫄝𫆟𫈡𫊣𫌥𫎧𫐩𫒫𫔭𫖯𫘱𫚳𫜵𫞷𫠹𫢻𫤽𫦿𫨁𫪃𫬅𫮇𫰉𫲋𫴍𫶏𫸑𫺓𫼕𫾗𬀙𬂛𬄝𬆟𬈡𬊣𬌥𬎧𬐩𬒫𬔭𬖯𬘱𬚳𬜵𬞷𬠹𬢻𬤽𬦿𬨁𬪃𬬅𬮇𬰉𬲋𬴍𬶏𬸑𬺓𬼕𬾗𭀙𭂛𭄝𭆟𭈡𭊣𭌥𭎧𭐩𭒫𭔭𭕯𭘱𭚳𭜵𭞷𭠹𭢻𭤽𭦿𭨁𭪃𭬅𭮇𭰉𭲋𭴍𭶏𭸑𭺓𭼕𭾗𮀙𮂛𮄝𮆟𮈡𮊣𮌥𮎧𮐩𮒫𮤭𮖯𮘱𮚳𮜵𮞷𮠹𮢻𮤽𮦿𮨁𮪃𮬅𮮇𮰉𮲋𮴍𮶏𮸑𮺓𮼕𮾗𯀙𯂛𯄝𯆟𯈡𯊣𯌥𯎧𯐩𯒫𯔭𯖯𯘱𯚳𯜵𯞷叫捨𥁄䗗𩖶𯪃𯬅𯮇𯰉𯲋𯴍𯶏𯸑𯺓𯼕𯾗'
POSITIONS = ('master', 'assist1', 'assist2')
MAX_URL_CHARS = 65536
MAX_DECODED_BYTES = 262144

# SSToy: 1 essential, 2 low, 3 recommended, 4 minimum. An unmarked potential
# retains sspp's existing default priority 2; SSToy has no "명함 필수" mark.
MARK_PRIORITY_MAP = {0: 2, 1: 5, 2: 1, 3: 4, 4: 2}

LEGACY_V2_CHAR_IDS = (
    103, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117,
    118, 119, 120, 123, 125, 126, 127, 129, 130, 132, 133, 134, 135,
    136, 141, 142, 143, 144, 145, 147, 149, 150, 155, 156, 158, 159,
)
# Compact exact representation of upstream's 1044 frozen IDs, not a map rebuilt
# from current game data. Each listed group has these same 29 suffixes.
_LEGACY_V2_POT_GROUPS = (
    5103, 5107, 5108, 5110, 5111, 5112, 5113, 5114, 5115, 5116,
    5117, 5118, 5119, 5120, 5123, 5125, 5126, 5127, 5130, 5132,
    5133, 5134, 5135, 5136, 5141, 5142, 5143, 5144, 5145, 5147,
    5149, 5150, 5155, 5156, 5158, 5159,
)
_LEGACY_V2_POT_SUFFIXES = (*range(1, 14), *range(21, 34), 41, 42, 43)
LEGACY_V2_POT_IDS = tuple(
    group * 100 + suffix
    for group in _LEGACY_V2_POT_GROUPS
    for suffix in _LEGACY_V2_POT_SUFFIXES
)


def base91_decode(encoded_str: str) -> bytes:
    """Decode standard basE91, rejecting characters outside its alphabet."""
    b, n, v = 0, 0, -1
    output = bytearray()
    decode_table = {char: i for i, char in enumerate(BASE91_CHARS)}
    for char in encoded_str:
        if char not in decode_table:
            raise ValueError("Base91에 없는 문자가 포함되어 있습니다")
        p = decode_table[char]
        if v < 0:
            v = p
        else:
            v += p * 91
            b |= v << n
            n += 13 if (v & 8191) > 88 else 14
            while n >= 8:
                output.append(b & 255)
                b >>= 8
                n -= 8
            v = -1
    if v >= 0:
        output.append((b | (v << n)) & 255)
    return bytes(output)


def base32768_decode(encoded_str: str) -> bytes:
    """Decode the raw alphabet published by SSToy, without skipping characters."""
    if "undefined" in encoded_str:
        raise ValueError(
            "SSToy raw 인코더가 데이터를 유실한 URL입니다. v3d 압축 공유 URL을 사용하세요"
        )
    decode_table = {char: i for i, char in enumerate(BASE32768_CHARS)}
    output = bytearray()
    for char in encoded_str:
        if char not in decode_table:
            raise ValueError("SSToy raw 문자표에 없는 문자가 포함되어 있습니다")
        value = decode_table[char]
        output.extend((value >> 8, value & 255))
    return bytes(output)


def read_varint(data: bytes, offset: int) -> Tuple[int, int]:
    """Read a canonical uint32 varint and reject truncation or overflow."""
    if offset < 0 or offset >= len(data):
        raise EOFError("Varint 데이터가 잘렸습니다")
    result = 0
    for index in range(5):
        pos = offset + index
        if pos >= len(data):
            raise EOFError("Varint 데이터가 잘렸습니다")
        byte = data[pos]
        if index == 4 and byte > 15:
            raise ValueError("Varint 값이 uint32 범위를 벗어났습니다")
        result |= (byte & 0x7f) << (index * 7)
        if not byte & 0x80:
            if index and byte == 0:
                raise ValueError("비정규 Varint 표현입니다")
            return result, pos + 1
    raise ValueError("Varint 값이 uint32 범위를 벗어났습니다")


def _extract_build(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError("URL은 문자열이어야 합니다")
    if len(url) > MAX_URL_CHARS:
        raise ValueError("공유 URL이 너무 깁니다")
    url = url.strip()
    # Bare tokens are literal: '%' and '+' are valid Base91 characters.
    if re.match(r"^v\d+[dr]-", url):
        return url
    if url.startswith(("build=", "#build=", "?build=")):
        sections = [url.lstrip("#?")]
    else:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("SSToy 공유 URL 형식이 아닙니다")
        sections = [parts.query, parts.fragment]
    values = []
    for section in sections:
        for field in section.split("&"):
            key, separator, value = field.partition("=")
            if separator and key == "build":
                values.append(value)
    if len(values) != 1:
        raise ValueError("build 매개변수가 정확히 하나 필요합니다")
    if re.search(r"%(?![0-9a-fA-F]{2})", values[0]):
        raise ValueError("잘못된 URL 퍼센트 인코딩입니다")
    # SSToy uses encodeURIComponent, not form encoding. Preserve literal '+'.
    return unquote(values[0], encoding="utf-8", errors="strict")


def _inflate(data: bytes) -> bytes:
    inflater = zlib.decompressobj(-15)  # fflate emits raw DEFLATE.
    result = inflater.decompress(data, MAX_DECODED_BYTES + 1)
    if len(result) > MAX_DECODED_BYTES or inflater.unconsumed_tail:
        raise ValueError("압축 해제 데이터가 허용 크기를 초과합니다")
    if not inflater.eof:
        raise ValueError("압축 데이터가 잘렸거나 올바른 raw DEFLATE가 아닙니다")
    if inflater.unused_data:
        raise ValueError("압축 데이터 뒤에 추가 데이터가 있습니다")
    return result


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def number(self) -> int:
        value, self.offset = read_varint(self.data, self.offset)
        return value

    def take(self, length: int) -> bytes:
        if length > len(self.data) - self.offset:
            raise EOFError("빌드 데이터가 잘렸습니다")
        result = self.data[self.offset:self.offset + length]
        self.offset += length
        return result

    def byte(self) -> int:
        return self.take(1)[0]

    def count(self) -> int:
        value = self.number()
        if value > len(self.data) - self.offset:
            raise EOFError("목록 개수가 남은 데이터 길이를 초과합니다")
        return value


def _actual_id(value: int, version: int, kind: str) -> int:
    if value == 0:
        raise ValueError(f"{kind} ID는 0일 수 없습니다")
    if value > 0xffffffff:
        raise ValueError(f"{kind} ID가 uint32 범위를 벗어났습니다")
    if version == 3:
        return value
    ids = LEGACY_V2_CHAR_IDS if kind == "캐릭터" else LEGACY_V2_POT_IDS
    if value > len(ids):
        raise ValueError(f"알 수 없는 v2 {kind} 인덱스: {value}")
    return ids[value - 1]


def _parse_payload(data: bytes, version: int, raw_mode: bool) -> Dict[str, Any]:
    reader = _Reader(data)
    stream_version = reader.number()
    if stream_version != version:
        raise ValueError("URL 버전과 내부 데이터 버전이 다릅니다")
    build_name = reader.take(reader.number()).decode("utf-8", errors="strict")
    slot_mask = reader.byte()
    if slot_mask & ~7:
        raise ValueError("잘못된 캐릭터 슬롯 마스크입니다")
    characters = {}
    for index, position in enumerate(POSITIONS):
        if not slot_mask & (1 << index):
            continue
        char_id = _actual_id(reader.number(), version, "캐릭터")
        potentials = [
            _actual_id(reader.number(), version, "잠재력")
            for _ in range(reader.count())
        ]
        levels = {}
        previous = 0
        for _ in range(reader.count()):
            key = previous + reader.number()
            if key <= previous:
                raise ValueError("잠재력 레벨 키가 증가하지 않습니다")
            actual_key = _actual_id(key, version, "잠재력")
            levels[actual_key] = reader.number() + 1
            previous = key
        marks = {}
        previous = 0
        for _ in range(reader.count()):
            key = previous + reader.number()
            if key <= previous:
                raise ValueError("잠재력 마크 키가 증가하지 않습니다")
            actual_key = _actual_id(key, version, "잠재력")
            code = reader.byte()
            if code not in MARK_PRIORITY_MAP:
                raise ValueError(f"알 수 없는 잠재력 마크 코드: {code}")
            marks[actual_key] = MARK_PRIORITY_MAP[code]
            previous = key
        characters[position] = {
            "mapped_char_idx": char_id,
            "mapped_potentials": potentials,
            "potential_levels": levels,
            "marks": marks,
        }

    # Discs do not affect sspp's recommendation overlay, but consume/validate
    # their bytes so truncated or shifted URLs cannot appear to import cleanly.
    # Both supported encoders always write this flag, even without discs.
    has_discs = reader.byte()
    if has_discs not in (0, 1):
        raise ValueError("잘못된 디스크 존재 플래그입니다")
    if has_discs:
        disc_mask = reader.byte()
        if disc_mask & ~63:
            raise ValueError("잘못된 디스크 슬롯 마스크입니다")
        for index in range(6):
            if disc_mask & (1 << index):
                if not reader.number():
                    raise ValueError("디스크 ID는 0일 수 없습니다")
                if reader.byte() & ~63:
                    raise ValueError("잘못된 디스크 레벨 데이터입니다")
    remaining = data[reader.offset:]
    # Upstream raw encoding pads an odd byte length with one zero byte.
    if remaining and not (raw_mode and remaining == b"\x00"):
        raise ValueError("빌드 뒤에 처리되지 않은 추가 데이터가 있습니다")
    return {
        "build_name": build_name,
        "raw_characters": characters,
        "version": version,
    }


def decode_url_raw(url: str) -> Dict[str, Any]:
    """Decode a share URL; failures preserve the existing {'error': ...} API."""
    try:
        token = _extract_build(url)
        match = re.fullmatch(r"v([23])([dr])-(.+)", token)
        if not match:
            raise ValueError("지원하는 공유 형식은 v2d/v2r/v3d/v3r입니다 (LZ 구형 형식 미지원)")
        version = int(match.group(1))
        raw_mode = match.group(2) == "r"
        payload = match.group(3)
        data = base32768_decode(payload) if raw_mode else _inflate(base91_decode(payload))
        if len(data) > MAX_DECODED_BYTES:
            raise ValueError("빌드 데이터가 허용 크기를 초과합니다")
        result = _parse_payload(data, version, raw_mode)
        logger.info("SSToy v%s 빌드 해독 완료: %s", version, result["build_name"])
        return result
    except (ValueError, EOFError, zlib.error, UnicodeError) as exc:
        logger.warning("SSToy 공유 URL 해독 실패: %s", exc)
        return {"error": f"디코딩 오류: {exc}"}
