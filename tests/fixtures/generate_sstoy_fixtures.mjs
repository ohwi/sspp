// Rebuild with Node >= 22.13: node tests/fixtures/generate_sstoy_fixtures.mjs
// Python tests consume the committed JSON and need no Node/npm dependencies.
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { stripTypeScriptTypes } from 'node:module';
import { deflateRawSync, inflateRawSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';

const compressionLib = {
  // Adapter only: fflate's raw DEFLATE is interoperable with node:zlib.
  deflateSync: bytes => deflateRawSync(bytes, { level: 9, memLevel: 8 }),
  inflateSync: bytes => inflateRawSync(bytes),
  strToU8: value => new Uint8Array(Buffer.from(value, 'utf8')),
  strFromU8: bytes => Buffer.from(bytes).toString('utf8'),
};
function loadReference(filename, gameData = {}) {
  const text = readFileSync(new URL(filename, import.meta.url), 'utf8');
  const code = stripTypeScriptTypes(text, { mode: 'strip' });
  const suffix = filename.includes('v3') ? ', compressSharePayload, LEGACY_V2_CHAR_IDS, LEGACY_V2_POT_IDS' : '';
  const api = new Function('compressionLib', 'GameData', 'window', code +
    '\nreturn {packSharePayload, base91Encode, base32768Encode, writeVarint, BASE32768_CHARS' + suffix + '};'
  )(compressionLib, gameData, {});
  return { api, sha256: createHash('sha256').update(text).digest('hex') };
}
const reference3 = loadReference('sstoy_v3_reference.ts');
const frozenIds = {
  characters: Object.fromEntries(reference3.api.LEGACY_V2_CHAR_IDS.map(id => [id, {}])),
  potentials: Object.fromEntries(reference3.api.LEGACY_V2_POT_IDS.map(id => [id, {}])),
};
const reference2 = loadReference('sstoy_v2_reference.ts', frozenIds);
const build = {
  n: 'KR 빌드 / 검증 + % # &',
  c: {
    m: { i: '103', p: [510303, 510301, 510302, 510304, 510305],
      pl: { 510301: 3, 510303: 2 },
      pm: { 510301: 'essential', 510302: 'low', 510303: 'recommended', 510304: 'minimum' } },
    a1: { i: '159', p: [515943, 515901], pl: { 515943: 4 }, pm: { 515943: 'minimum' } },
    a2: { i: '106', p: [], pl: {}, pm: {} },
  },
  d: { s: { main1: 1000001, sub3: 1000111 }, l: { main1: 3 }, g: { sub3: 7 } },
};
const expectedCharacters = {
  master: { mapped_char_idx: 103, mapped_potentials: [510303, 510301, 510302, 510304, 510305],
    potential_levels: { 510301: 3, 510303: 2 },
    marks: { 510301: 5, 510302: 1, 510303: 4, 510304: 2 } },
  assist1: { mapped_char_idx: 159, mapped_potentials: [515943, 515901],
    potential_levels: { 515943: 4 }, marks: { 515943: 2 } },
  assist2: { mapped_char_idx: 106, mapped_potentials: [], potential_levels: {}, marks: {} },
};
const deflated = bytes => reference3.api.base91Encode(compressionLib.deflateSync(Uint8Array.from(bytes)));
const bytes = (...values) => Uint8Array.from(values);
const vi = value => { const result = []; reference3.api.writeVarint(result, value); return result; };
const cases = [];
for (const [version, reference] of [[3, reference3], [2, reference2]]) {
  const payload = reference.api.packSharePayload(build);
  const token = 'v' + version + 'd-' + deflated(payload);
  cases.push({ name: 'v' + version + '_multislot_unicode_discs', token,
    url: 'https://jforplay.github.io/sstoy/app.html#build=' + encodeURIComponent(token),
    payload_hex: Buffer.from(payload).toString('hex'),
    expected: { build_name: build.n, raw_characters: expectedCharacters, version } });
}
const empty = reference3.api.packSharePayload({ n: '', c: {} });
cases.push({ name: 'v3_empty_deflated', token: 'v3d-' + deflated(empty),
  expected: { build_name: '', raw_characters: {}, version: 3 } });
const assistOnly = reference3.api.packSharePayload({ n: 'Assist only', c: {
  a2: { i: '159', p: [515943], pl: {}, pm: {} } } });
cases.push({ name: 'v3_assist2_only', token: 'v3d-' + deflated(assistOnly),
  expected: { build_name: 'Assist only', version: 3,
    raw_characters: { assist2: { mapped_char_idx: 159, mapped_potentials: [515943],
      potential_levels: {}, marks: {} } } } });
const rawRegression = [2, 3].map(version => {
  const api = version === 2 ? reference2.api : reference3.api;
  const payload = api.packSharePayload({ n: '', c: {} });
  return { version, token: 'v' + version + 'r-' + api.base32768Encode(payload),
    payload_hex: Buffer.from(payload).toString('hex'), alphabet_entries: api.BASE32768_CHARS.length };
});
const malformedPayloads = [
  ['overlong_version', bytes(0x83, 0, 0, 0, 0)],
  ['overflow_version', bytes(255, 255, 255, 255, 16)],
  ['truncated_varint', bytes(3, 0, 1, 128)],
  ['truncated_name', bytes(3, 5, 65)],
  ['invalid_utf8', bytes(3, 1, 255, 0, 0)],
  ['invalid_slot_mask', bytes(3, 0, 8, 0)],
  ['zero_character', bytes(3, 0, 1, 0, 0, 0, 0, 0)],
  ['truncated_potentials', bytes(3, 0, 1, 103, 2)],
  ['invalid_mark_code', bytes(3, 0, 1, 103, 1, ...vi(510301), 0, 1, ...vi(510301), 5, 0)],
  ['duplicate_mark_key', bytes(3, 0, 1, 103, 0, 0, 2, ...vi(510301), 1, 0, 2, 0)],
  ['invalid_disc_flag', bytes(3, 0, 0, 2)],
  ['invalid_disc_mask', bytes(3, 0, 0, 1, 64)],
  ['truncated_disc', bytes(3, 0, 0, 1, 1, ...vi(1000001))],
  ['trailing_payload', bytes(3, 0, 0, 0, 17)],
].map(([name, payload]) => ({ name, token: 'v3d-' + deflated(payload) }));
malformedPayloads.push(
  { name: 'unknown_v2_character', token: 'v2d-' + deflated(bytes(2, 0, 1, 40, 0, 0, 0, 0)) },
  { name: 'unknown_v2_potential', token: 'v2d-' + deflated(bytes(2, 0, 1, 1, 1, ...vi(1045), 0, 0, 0)) },
  { name: 'version_mismatch', token: cases[0].token.replace('v3d-', 'v2d-') },
  { name: 'unsupported_version', token: cases[0].token.replace('v3d-', 'v4d-') },
  { name: 'truncated_deflate', token: 'v3d-' + reference3.api.base91Encode(compressionLib.deflateSync(empty).subarray(0, 2)) },
  { name: 'extra_compressed_bytes', token: 'v3d-' + reference3.api.base91Encode(bytes(...compressionLib.deflateSync(empty), 0)) },
  { name: 'oversized_deflate', token: 'v3d-' + deflated(new Uint8Array(262145)) },
);
// Remove the mandatory no-discs flag from actual upstream-encoded builds.
for (const [version, reference] of [[2, reference2], [3, reference3]]) {
  const payload = reference.api.packSharePayload({ n: 'Truncated flag', c: build.c });
  if (payload[payload.length - 1] !== 0) throw new Error('Expected no-discs flag');
  malformedPayloads.push({ name: 'v' + version + '_missing_discs_flag',
    token: 'v' + version + 'd-' + deflated(payload.subarray(0, payload.length - 1)) });
}
// This includes zero byte pairs, explicitly exercising basE91's 14-bit branch.
const vector = Uint8Array.from([...new Uint8Array(32), ...Array.from({ length: 256 }, (_, i) => i)]);
const output = {
  provenance: {
    v3_ref: '34f57606015277cf9da76a2c7cc14fa1b68ccbbf',
    v2_ref: '88e9aa3535e20a36e98e2a9928fcaa2065c94f42',
    v3_excerpt_sha256: reference3.sha256,
    v2_excerpt_sha256: reference2.sha256,
    generator: 'Exact upstream packSharePayload/base91Encode/base32768Encode; Node type stripping; node:zlib raw-DEFLATE adapter',
    frozen_v2_character_ids: reference3.api.LEGACY_V2_CHAR_IDS,
    frozen_v2_potential_ids: reference3.api.LEGACY_V2_POT_IDS,
  },
  cases, raw_regression: rawRegression, malformed: malformedPayloads,
  base91_vector: { encoded: reference3.api.base91Encode(vector), hex: Buffer.from(vector).toString('hex') },
  raw_vector: { encoded: reference3.api.base32768Encode(bytes(0, 1, 1, 254)), hex: '000101fe' },
};
const destination = new URL('sstoy_urls.json', import.meta.url);
writeFileSync(destination, JSON.stringify(output, null, 2) + '\n', 'utf8');
console.log('Wrote', fileURLToPath(destination), { successCases: cases.length, malformedCases: malformedPayloads.length, rawRegression });
