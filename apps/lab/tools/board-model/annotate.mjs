// Schematic catalogue is the single source for CAD reference descriptions.
// Updating metadata preserves the existing compressed geometry bytes.
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

export const catalogue = JSON.parse(await readFile(new URL('parts.json', import.meta.url), 'utf8'));
export const components = Object.fromEntries(Object.entries(catalogue.parts)
  .filter(([, part]) => part.component).map(([ref, part]) => [ref, part.component]));
export const partInfo = ref => ({ ref, component: null, ...catalogue.parts[ref] });

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [input, output] = process.argv.slice(2);
  if (!input?.endsWith('.glb') || !output?.endsWith('.glb')) {
    throw new Error('Usage: node annotate.mjs input.glb output.glb');
  }
  const bytes = await readFile(input), provenance = JSON.parse(await readFile(input.replace(/\.glb$/, '.json'), 'utf8'));
  assert.equal(bytes.toString('ascii', 0, 4), 'glTF');
  assert.equal(bytes.readUInt32LE(4), 2);
  assert.equal(bytes.toString('ascii', 16, 20), 'JSON');
  const jsonEnd = 20 + bytes.readUInt32LE(12), json = JSON.parse(bytes.subarray(20, jsonEnd));
  for (const node of json.nodes) {
    if (node.extras?.ref) node.extras = { ...node.extras, ...partInfo(node.extras.ref) };
  }
  json.nodes.find(node => node.name === 'DM-MC02').extras.schematic = catalogue.source;
  const text = Buffer.from(JSON.stringify(json)), padded = Buffer.alloc((text.length + 3) & ~3, 0x20);
  text.copy(padded);
  const header = Buffer.from(bytes.subarray(0, 20));
  header.writeUInt32LE(20 + padded.length + bytes.length - jsonEnd, 8);
  header.writeUInt32LE(padded.length, 12);
  const binary = Buffer.concat([header, padded, bytes.subarray(jsonEnd)]);
  const metadata = { ...provenance, components, schematic: catalogue.source,
    glbBytes: binary.length, glbSHA256: createHash('sha256').update(binary).digest('hex') };
  await writeFile(output, binary);
  await writeFile(output.replace(/\.glb$/, '.json'), JSON.stringify(metadata, null, 2) + '\n');
  console.log(JSON.stringify({ glbBytes: metadata.glbBytes, glbSHA256: metadata.glbSHA256,
    schematic: metadata.schematic, parts: Object.keys(catalogue.parts).length }, null, 2));
}
