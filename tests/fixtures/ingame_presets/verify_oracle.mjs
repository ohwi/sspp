// Usage: node tests/fixtures/ingame_presets/verify_oracle.mjs <pinned app-ingame-codec.ts>
// Requires Node >=22.13. Does not write files or require npm packages.
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
const fixture = JSON.parse(readFileSync(new URL('cases.json', import.meta.url), 'utf8'));
if (!process.argv[2]) throw new Error('Supply the pinned upstream TypeScript codec path');
const source = readFileSync(process.argv[2], 'utf8');
const js = stripTypeScriptTypes(source, { mode: 'strip' })
  .replace(/\bexport\s+(?=(function|class|const))/g, '');
const api = new Function(js + '\nreturn {decodeIngameCode,encodeIngameCode};')();
for (const example of fixture.cases) {
  const bytes = Buffer.from(example.code, 'base64');
  const schemas = new Map();
  for (const offset of [0,4,8]) {
    const id = bytes.readUInt32BE(offset);
    const cp = fixture.catalog.characters[id].preset_schema;
    schemas.set(id, {
      master:{specificLen:cp.MasterSpecificPotentialIds.length,normalLen:cp.MasterNormalPotentialIds.length,commonLen:cp.CommonPotentialIds.length},
      assist:{specificLen:cp.AssistSpecificPotentialIds.length,normalLen:cp.AssistNormalPotentialIds.length,commonLen:cp.CommonPotentialIds.length},
    });
  }
  const result = api.decodeIngameCode(example.code, schemas, fixture.catalog.ingame_preset_max_level);
  if (!result.ok) throw new Error(JSON.stringify(result));
  if (api.encodeIngameCode(result.payload, schemas, 6) !== example.code) throw new Error('Round-trip mismatch');
  const slots = result.payload.slots.map((slot,index)=>{
    const cp = fixture.catalog.characters[slot.charId].preset_schema;
    const prefix = index === 0 ? 'Master' : 'Assist';
    const target_levels = {};
    cp[prefix+'SpecificPotentialIds'].forEach((id,i)=>target_levels[id]=slot.specificOn[i]?1:0);
    cp[prefix+'NormalPotentialIds'].forEach((id,i)=>target_levels[id]=slot.normalLevels[i]);
    cp.CommonPotentialIds.forEach((id,i)=>target_levels[id]=slot.commonLevels[i]);
    return {position:['master','assist1','assist2'][index],char_id:slot.charId,target_levels};
  });
  if (JSON.stringify(slots) !== JSON.stringify(example.expected_slots)) throw new Error('Expected slots mismatch');
  console.log(example.name, 'upstream decode + exact byte round-trip verified');
}
