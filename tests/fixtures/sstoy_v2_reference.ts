// Source: https://github.com/JforPlay/sstoy/blob/88e9aa3535e20a36e98e2a9928fcaa2065c94f42/src/modules/app-saveload.ts
// Exact excerpts; function bodies are unchanged. Node's stripTypeScriptTypes removes annotations.
const SHARE_VERSION = 2;

const SHARE_POSITIONS = ['m', 'a1', 'a2'] as const;

const DISC_SLOTS = ['main1', 'main2', 'main3', 'sub1', 'sub2', 'sub3'] as const;

const BASE91_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&()*+,./:;<=>?@[]^_`{|}~"';

const BASE32768_CHARS = [...'𠀀𠂊𠄓𠆢𠈕𠊧𠌫𠎭𠐻𠒄𠔓𠖻𠘨𠚺𠜎𠞩𠠻𠢹𠤼𠦿𠩂𠫅𠭇𠯉𠱋𠳍𠵏𠷑𠹓𠻕𠽗𠿙𡁛𡃝𡅟𡇡𡉣𡋥𡍧𡏩𡑫𡓭𡕯𡗱𡙳𡛵𡝷𡟹𡡻𡣽𡥿𡨁𡪃𡬅𡮇𡰉𡲋𡴍𡶏𡸑𡺓𡼕𡾗𢀙𢂛𢄝𢆟𢈡𢊣𢋥𢎧𢐩𢒫𢔭𢖯𢘱𢚳𢜵𢞷𢠹𢢻𢤽𢦿𢨁𢪃𢬅𢮇𢰉𢲋𢴍𢶏𢸑𢺓𢼕𢾗𣀙𣂛𣄝𣆟𣈡𣊣𣌥𣎧𣐩𣒫𣔭𣖯𣘱𣚳𣜵𣞷𣠹𣢻𣤽𣦿𣨁𣪃𣬅𣮇𣰉𣲋𣴍𣶏𣸑𣺓𣼕𣾗𤀙𤂛𤄝𤆟𤈡𤊣𤌥𤎧𤐩𤒫𤔭𤖯𤘱𤚳𤜵𤞷𤠹𤢻𤤽𤦿𤨁𤪃𤬅𤮇𤰉𤲋𤴍𤶏𤸑𤺓𤼕𤾗𥀙𥂛𥄝𥆟𥈡𥊣𥌥𥎧𥐩𥒫𥔭𥖯𥘱𥚳𥜵𥞷𥠹𥢻𥤽𥦿𥨁𥪃𥬅𥮇𥰉𥲋𥴍𥶏𥸑𥺓𥼕𥾗𦀙𦂛𦄝𦆟𦈡𦊣𦌥𦎧𦐩𦒫𦔭𦖯𦘱𦚳𦜵𦞷𦠹𦢻𦤽𦦿𦨁𦪃𦬅𦮇𦰉𦲋𦴍𦶏𦸑𦺓𦼕𦾗𧀙𧂛𧄝𧆟𧈡𧊣𧌥𧎧𧐩𧒫𧔭𧖯𧘱𧚳𧜵𧞷𧠹𧢻𧤽𧦿𧨁𧪃𧬅𧮇𧰉𧲋𧴍𧶏𧸑𧺓𧼕𧾗𨀙𨂛𨄝𨆟𨈡𨊣𨌥𨎧𨐩𨒫𨔭𨖯𨘱𨚳𨜵𨞷𨠹𨢻𨤽𨦿𨨁𨪃𨬅𨮇𨰉𨲋𨴍𨶏𨸑𨺓𨼕𨾗𩀙𩂛𩄝𩆟𩈡𩊣𩌥𩎧𩐩𩒫𩔭𩖯𩘱𩚳𩜵𩞷𩠹𩢻𩤽𩦿𩨁𩪃𩬅𩮇𩰉𩲋𩴍𩶏𩸑𩺓𩼕𩾗𪀙𪂛𪄝𪆟𪈡𪊣𪌥𪎧𪐩𪒫𪔭𪖯𪘱𪚳𪜵𪞷𪠹𪢻𪤽𪦿𪨁𪪃𪬅𪮇𪰉𪲋𪴍𪶏𪸑𪺓𪼕𪾗𫀙𫂛𫄝𫆟𫈡𫊣𫌥𫎧𫐩𫒫𫔭𫖯𫘱𫚳𫜵𫞷𫠹𫢻𫤽𫦿𫨁𫪃𫬅𫮇𫰉𫲋𫴍𫶏𫸑𫺓𫼕𫾗𬀙𬂛𬄝𬆟𬈡𬊣𬌥𬎧𬐩𬒫𬔭𬖯𬘱𬚳𬜵𬞷𬠹𬢻𬤽𬦿𬨁𬪃𬬅𬮇𬰉𬲋𬴍𬶏𬸑𬺓𬼕𬾗𭀙𭂛𭄝𭆟𭈡𭊣𭌥𭎧𭐩𭒫𭔭𭕯𭘱𭚳𭜵𭞷𭠹𭢻𭤽𭦿𭨁𭪃𭬅𭮇𭰉𭲋𭴍𭶏𭸑𭺓𭼕𭾗𮀙𮂛𮄝𮆟𮈡𮊣𮌥𮎧𮐩𮒫𮤭𮖯𮘱𮚳𮜵𮞷𮠹𮢻𮤽𮦿𮨁𮪃𮬅𮮇𮰉𮲋𮴍𮶏𮸑𮺓𮼕𮾗𯀙𯂛𯄝𯆟𯈡𯊣𯌥𯎧𯐩𯒫𯔭𯖯𯘱𯚳𯜵𯞷叫捨𥁄䗗𩖶𯪃𯬅𯮇𯰉𯲋𯴍𯶏𯸑𯺓𯼕𯾗'];

const MARK_CODES: Record<string, number> = {
  '필수': 1,
  'essential': 1,
  '후순위': 2,
  'low': 2,
  '다다익선': 3,
  'recommended': 3,
  '명함만': 4,
  'minimum': 4,
};

function canUseCompression(): boolean {
  return (
    typeof compressionLib.deflateSync === 'function' &&
    typeof compressionLib.inflateSync === 'function' &&
    typeof compressionLib.strToU8 === 'function' &&
    typeof compressionLib.strFromU8 === 'function'
  );
}

function toNum(value: unknown): number {
  const num = Number(value);
  return Number.isFinite(num) ? num : 0;
}

function stringToBytes(str: string): Uint8Array {
  if (!str) return new Uint8Array(0);
  if (canUseCompression()) {
    return compressionLib.strToU8(str);
  }
  return new TextEncoder().encode(str);
}

function base91Encode(bytes: Uint8Array): string {
  if (!bytes || bytes.length === 0) return '';
  let b = 0;
  let n = 0;
  let output = '';
  for (let i = 0; i < bytes.length; i++) {
    const byte = bytes[i];
    if (byte === undefined) continue;
    b |= byte << n;
    n += 8;
    if (n > 13) {
      let v = b & 8191;
      if (v > 88) {
        b >>= 13;
        n -= 13;
      } else {
        v = b & 16383;
        b >>= 14;
        n -= 14;
      }
      output += (BASE91_CHARS[v % 91] || '') + (BASE91_CHARS[Math.floor(v / 91)] || '');
    }
  }
  if (n) {
    const char1 = BASE91_CHARS[b % 91];
    const char2 = BASE91_CHARS[Math.floor(b / 91)];
    output += char1 || '';
    if ((n > 7 || b > 90) && char2) {
      output += char2;
    }
  }
  return output;
}

function base32768Encode(bytes: Uint8Array): string {
  if (!bytes || bytes.length === 0) return '';
  let out = '';
  for (let i = 0; i < bytes.length; i += 2) {
    const hi = bytes[i];
    const lo = i + 1 < bytes.length ? (bytes[i + 1] ?? 0) : 0;
    if (hi === undefined) continue;
    const code = (hi << 8) | lo;
    out += BASE32768_CHARS[code];
  }
  return out;
}

function writeVarint(target: number[], value: number): void {
  let val = value >>> 0;
  while (val >= 0x80) {
    target.push((val & 0x7f) | 0x80);
    val >>>= 7;
  }
  target.push(val);
}

function buildIdMaps(): IdMaps {
  function makeMap(ids: number[]): { toIdx: Map<number, number>; fromIdx: Record<number, number> } {
    const sorted = Array.from(new Set(ids)).sort((a, b) => a - b);
    const toIdx = new Map<number, number>();
    const fromIdx: Record<number, number> = {};
    sorted.forEach((id, i) => {
      const idx = i + 1;
      toIdx.set(id, idx);
      fromIdx[idx] = id;
    });
    return { toIdx, fromIdx };
  }

  const charIds = GameData.characters
    ? Object.keys(GameData.characters).map(toNum)
    : [];
  const potIds = GameData.potentials
    ? Object.keys(GameData.potentials).map(toNum)
    : [];
  const discIds = window.discsState?.allDiscs
    ? window.discsState.allDiscs.map((d) => toNum(d.Id))
    : [];

  return {
    charMap: makeMap(charIds),
    potMap: makeMap(potIds),
    discMap: makeMap(discIds),
  };
}

function encodeMark(mark: string): number {
  return MARK_CODES[mark] || 0;
}

function packSharePayload(cleanedData: BuildData): Uint8Array {
  const { charMap, potMap } = buildIdMaps();
  const writer: number[] = [];

  writeVarint(writer, SHARE_VERSION);

  const nameBytes = stringToBytes(cleanedData.n || '');
  writeVarint(writer, nameBytes.length);
  for (let i = 0; i < nameBytes.length; i++) {
    const byte = nameBytes[i];
    if (byte !== undefined) writer.push(byte);
  }

  const chars = cleanedData.c || {};
  let slotMask = 0;
  if (chars.m) slotMask |= 1;
  if (chars.a1) slotMask |= 2;
  if (chars.a2) slotMask |= 4;
  writer.push(slotMask & 0xff);

  function packCharacter(charData: CharacterBuildData): void {
    const mappedId = charMap.toIdx.get(Number(charData.i)) || 0;
    writeVarint(writer, mappedId);

    const potentials = Array.isArray(charData.p) ? [...charData.p] : [];
    // Note: Don't sort - preserve user's custom potential order
    writeVarint(writer, potentials.length);
    potentials.forEach((p) => {
      const mapped = potMap.toIdx.get(Number(p)) || 0;
      writeVarint(writer, mapped);
    });

    const mappedLevels: Record<number, number> = {};
    if (charData.pl) {
      Object.entries(charData.pl).forEach(([k, v]) => {
        const mapped = potMap.toIdx.get(Number(k)) || 0;
        if (mapped) mappedLevels[mapped] = v;
      });
    }
    const levelEntries = Object.entries(mappedLevels)
      .map(([k, v]) => [toNum(k), v] as [number, number])
      .sort((a, b) => a[0] - b[0]);
    writeVarint(writer, levelEntries.length);
    let prevKey = 0;
    for (const [key, value] of levelEntries) {
      writeVarint(writer, (key - prevKey) >>> 0);
      writeVarint(writer, Math.max(0, (value || 0) - 1));
      prevKey = key;
    }

    const markEntries: [number, number][] = [];
    if (charData.pm) {
      Object.entries(charData.pm).forEach(([k, v]) => {
        const mapped = potMap.toIdx.get(Number(k)) || 0;
        const code = encodeMark(v);
        if (mapped && code) {
          markEntries.push([mapped, code]);
        }
      });
    }
    markEntries.sort((a, b) => a[0] - b[0]);
    writeVarint(writer, markEntries.length);
    let prevMark = 0;
    markEntries.forEach(([idx, code]) => {
      writeVarint(writer, idx - prevMark);
      writer.push(code & 0xff);
      prevMark = idx;
    });
  }

  SHARE_POSITIONS.forEach((pos) => {
    const data = chars[pos];
    if (!data) return;
    packCharacter(data);
  });

  const discs = cleanedData.d;
  if (!discs) {
    writer.push(0);
  } else {
    writer.push(1);
    let discSlotMask = 0;
    const slotPayload: Array<{ discId: number; lb: number; sub: number } | null> = [];
    DISC_SLOTS.forEach((slotKey, idx) => {
      const discId = toNum(discs.s?.[slotKey]) || 0;
      if (discId > 0) {
        discSlotMask |= 1 << idx;
        const lb = Math.max(0, (toNum(discs.l?.[slotKey]) || 1) - 1);
        const sub = Math.max(0, toNum(discs.g?.[slotKey]) || 0);
        slotPayload.push({ discId, lb, sub });
      } else {
        slotPayload.push(null);
      }
    });
    writer.push(discSlotMask);
    slotPayload.forEach((payload, idx) => {
      if (!payload || (discSlotMask & (1 << idx)) === 0) return;
      writeVarint(writer, payload.discId);
      writer.push(((payload.lb & 0x07) << 3) | (payload.sub & 0x07));
    });
  }

  return new Uint8Array(writer);
}
