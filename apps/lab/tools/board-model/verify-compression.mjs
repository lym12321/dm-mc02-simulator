// Compare decoded geometry through the actual Three loader, independently of
// the compression writer. Triangle order and cyclic corner rotations may differ.
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'meshoptimizer/decoder';

const [beforePath, afterPath, reportPath] = process.argv.slice(2);
if (!beforePath || !afterPath || !reportPath) {
  throw new Error('Usage: node verify-compression.mjs before.glb after.glb report.json');
}
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const inputs = await Promise.all([beforePath, afterPath].map(p => readFile(p)));
const json = bytes => JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)));
assert.deepEqual(json(inputs[1]).materials, json(inputs[0]).materials, 'material definitions');
const scenes = await Promise.all(inputs.map(async bytes => {
  const result = await new GLTFLoader().setMeshoptDecoder(MeshoptDecoder)
    .parseAsync(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), '');
  result.scene.updateMatrixWorld(true);
  return result.scene;
}));
const nodes = scenes.map(scene => { const list = []; scene.traverse(n => list.push(n)); return list; });
assert.equal(nodes[0].length, nodes[1].length, 'node count');

function geometrySignature(geometry) {
  const semantics = Object.keys(geometry.attributes).sort();
  const attributes = semantics.map(name => geometry.getAttribute(name));
  const vertices = Array.from({ length: attributes[0].count }, (_, index) => {
    return Buffer.concat(attributes.map(a => {
      const array = a.isInterleavedBufferAttribute ? a.data.array : a.array;
      const stride = a.isInterleavedBufferAttribute ? a.data.stride : a.itemSize;
      const offset = a.isInterleavedBufferAttribute ? a.offset : 0;
      return Buffer.from(array.buffer, array.byteOffset + (index * stride + offset) * array.BYTES_PER_ELEMENT,
        a.itemSize * array.BYTES_PER_ELEMENT);
    }));
  });
  const indices = geometry.index;
  assert.ok(indices && indices.count % 3 === 0, 'indexed triangle mesh');
  const faces = [];
  for (let i = 0; i < indices.count; i += 3) {
    const corners = [0, 1, 2].map(k => vertices[indices.getX(i + k)]);
    const rotations = [0, 1, 2].map(k => Buffer.concat([corners[k], corners[(k + 1) % 3], corners[(k + 2) % 3]]));
    rotations.sort(Buffer.compare);
    faces.push(hash(rotations[0]));
  }
  // Sorted per-face hashes retain duplicate multiplicity and winding, and bind
  // every corner's position to its exact normal/other attribute bytes.
  faces.sort();
  return { layout: semantics.map((name, i) => ({ name, size: attributes[i].itemSize,
    normalized: attributes[i].normalized, type: (attributes[i].data?.array || attributes[i].array).constructor.name })),
    triangles: faces.length, sha256: hash(faces.join('')) };
}

let triangles = 0, meshes = 0, parts = 0;
for (let i = 0; i < nodes[0].length; i++) {
  const a = nodes[0][i], b = nodes[1][i], label = `${i}: ${a.name}`;
  assert.deepEqual({ name: b.name, type: b.type, extras: b.userData, children: b.children.map(c => c.name),
    matrix: b.matrixWorld.toArray() }, { name: a.name, type: a.type, extras: a.userData,
    children: a.children.map(c => c.name), matrix: a.matrixWorld.toArray() }, `node ${label}`);
  if (a.userData.ref) parts++;
  if (!a.isMesh) continue;
  assert.equal(b.material.name, a.material.name, `material binding ${label}`);
  const expected = geometrySignature(a.geometry);
  assert.deepEqual(geometrySignature(b.geometry), expected, `oriented triangle attributes ${label}`);
  meshes++; triangles += expected.triangles;
}
const report = { status: 'passed', beforeBytes: inputs[0].length, afterBytes: inputs[1].length,
  beforeSHA256: hash(inputs[0]), afterSHA256: hash(inputs[1]), parts, meshes, triangles,
  fidelity: 'Every oriented triangle retains bit-identical per-corner position/normal attributes, multiplicity and material binding; decoded world transforms, hierarchy, names, extras and material definitions are identical. Only vertex/triangle order and cyclic corner rotation may differ.',
  scope: 'Lossless relative to the input GLB mesh, not the analytic STEP surfaces.' };
await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
