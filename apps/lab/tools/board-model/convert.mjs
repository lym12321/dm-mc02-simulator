// Offline CAD conversion only. Runtime uses the resulting GLB, never STEP/WASM.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import occtImport from 'occt-import-js';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { compressModel } from './compress.mjs';
import { catalogue, components as COMPONENTS, partInfo } from './annotate.mjs';

const TESSELLATION = { linearUnit: 'millimeter', linearDeflectionType: 'absolute_value',
  linearDeflection: 0.025, angularDeflection: 0.2 };

const FINISH = {
  pcb: ['#18221e', .12, .57], body: ['#272b2c', .05, .65],
  silver: ['#b9bfc3', .85, .27], gold: ['#c9a75b', .8, .3],
  ivory: ['#e1ddca', 0, .52], yellow: ['#dab318', 0, .4],
  blue: ['#315b8d', 0, .55], red: ['#8f3d3c', 0, .55], led: ['#b7b797', 0, .38],
};

// Material treatments are visual approximations; CAD geometry stays intact.
// These masks separate visually distinct housings/contacts in monochrome CAD solids.
function finish(ref, meshIndex, point, color) {
  const [x, y, z] = point;
  if (ref === 'Board') return 'pcb';
  if (ref === 'U14') return Math.abs(x - 37.135) > 7 || Math.abs(y) > 7 ? 'silver' : 'body';
  if (ref === 'J1') return meshIndex < 12 || meshIndex === 23 ? 'silver' : meshIndex < 21 ? 'gold' : 'body';
  if (ref === 'LED3') return 'led';
  if (ref === 'P9') return x > 9.5 || z < -0.3 ? 'gold' : 'yellow';
  if (ref === 'P7' || ref === 'P8') return z < -.3 ? 'silver' : 'body';
  if (ref === 'P11') return z > 2.6 || z < 0 ? 'gold' : ['blue', 'body', 'red'][meshIndex];
  if (ref === 'P6') return z > 2.6 || z < 0 ? 'silver' : 'body';
  if (/^SW[12]$/.test(ref)) return meshIndex === 1 && z < 2.5 ? 'silver' : 'body';
  if (['P1', 'P2', 'P3', 'P4', 'P5', 'P10', 'P12', 'P13', 'P15', 'U13'].includes(ref)) return 'ivory';
  if (ref === 'U18') return meshIndex === 0 ? 'body' : 'gold';
  if (ref === 'P16') return 'body';
  if (ref === 'BUZZER1' || /^L[126]$/.test(ref)) return 'body';
  if (ref === 'Y1') return 'silver';
  if (/^U\d+$/.test(ref)) return 'body';
  return color ? `cad:${color.map(v => v.toFixed(6)).join(',')}` : 'silver';
}

export function buildModel(imported) {
  const materials = new Map();
  let originalTriangles = 0, triangles = 0, duplicateMeshes = 0, parts = 0;
  const material = key => {
    if (!materials.has(key)) {
      const m = new THREE.MeshStandardMaterial();
      m.name = key;
      if (FINISH[key]) {
        m.color.set(FINISH[key][0]); m.metalness = FINISH[key][1]; m.roughness = FINISH[key][2];
      } else {
        // OpenCascade exports linear RGB; do not apply another sRGB conversion.
        m.color.fromArray(key.slice(4).split(',').map(Number));
        m.metalness = .15; m.roughness = .5;
      }
      materials.set(key, m);
    }
    return materials.get(key);
  };
  const build = source => {
    const group = new THREE.Group(); group.name = source.name || 'DM-MC02';
    if (source.meshes.length) {
      parts++;
      group.userData = partInfo(source.name);
      const batches = new Map(), seen = new Set();
      for (const [ordinal, index] of source.meshes.entries()) {
        const mesh = imported.meshes[index];
        originalTriangles += mesh.index.array.length / 3;
        const fingerprint = createHash('sha256').update(JSON.stringify(mesh)).digest('hex');
        if (seen.has(fingerprint)) { duplicateMeshes++; continue; }
        seen.add(fingerprint);
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.attributes.position.array, 3));
        geometry.setIndex(mesh.index.array);
        if (mesh.attributes.normal) geometry.setAttribute('normal', new THREE.Float32BufferAttribute(mesh.attributes.normal.array, 3));
        else geometry.computeVertexNormals();
        const positions = geometry.attributes.position;
        const byMaterial = new Map();
        let faceIndex = 0;
        for (let i = 0; i < mesh.index.array.length; i += 3) {
          while (faceIndex + 1 < mesh.brep_faces.length && mesh.brep_faces[faceIndex].last < i / 3) faceIndex++;
          const color = mesh.brep_faces[faceIndex]?.color || mesh.color;
          const indices = mesh.index.array.slice(i, i + 3);
          const center = [0, 0, 0];
          for (const j of indices) {
            center[0] += positions.getX(j) / 3;
            center[1] += positions.getY(j) / 3;
            center[2] += positions.getZ(j) / 3;
          }
          const key = finish(source.name, ordinal, center, color);
          if (!byMaterial.has(key)) byMaterial.set(key, []);
          byMaterial.get(key).push(...indices);
        }
        for (const [key, indices] of byMaterial) {
          // Compact the indexed subset, retaining the imported hard-edge normals.
          const remap = new Map(), vertices = [], normals = [], compact = [];
          for (const index of indices) {
            if (!remap.has(index)) {
              remap.set(index, remap.size);
              vertices.push(positions.getX(index), positions.getY(index), positions.getZ(index));
              const n = geometry.attributes.normal;
              normals.push(n.getX(index), n.getY(index), n.getZ(index));
            }
            compact.push(remap.get(index));
          }
          const piece = new THREE.BufferGeometry();
          piece.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
          piece.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
          piece.setIndex(compact);
          if (!batches.has(key)) batches.set(key, []);
          batches.get(key).push(piece);
          triangles += compact.length / 3;
        }
      }
      for (const [key, pieces] of batches) {
        const mesh = new THREE.Mesh(mergeGeometries(pieces), material(key));
        mesh.name = `${source.name}:${key}`; group.add(mesh);
      }
    }
    for (const child of source.children) group.add(build(child));
    return group;
  };
  const root = build(imported.root);
  // STEP X/Y board plane, +Z front → glTF metres, Y up, USB toward -Z.
  root.scale.setScalar(.001); root.rotation.x = -Math.PI / 2; root.position.x = -.028;
  root.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(root);
  return { root, stats: { parts, originalTriangles, triangles, duplicateMeshes, materials: materials.size,
    boundsMetres: { min: bounds.min.toArray(), max: bounds.max.toArray() } } };
}

// GLTFExporter needs FileReader's Blob adapter in Node; no image/texture encoding.
globalThis.FileReader ??= class {
  readAsArrayBuffer(blob) { blob.arrayBuffer().then(result => { this.result = result; this.onloadend?.(); }); }
  readAsDataURL(blob) { blob.arrayBuffer().then(result => {
    this.result = `data:${blob.type};base64,${Buffer.from(result).toString('base64')}`; this.onloadend?.();
  }); }
};

export async function writeModel(imported, input, output) {
  if (!imported.success) throw new Error('STEP import failed');
  const source = await fs.readFile(input), { root, stats } = buildModel(imported);
  root.userData = { source: path.basename(input), units: 'metre', ...stats, schematic: catalogue.source };
  const exported = await new GLTFExporter().parseAsync(root, { binary: true });
  const { binary, metadata } = await compressModel(new Uint8Array(exported));
  await fs.mkdir(path.dirname(output), { recursive: true });
  await fs.writeFile(output, Buffer.from(binary));
  const report = { source: path.basename(input), sourceBytes: source.length,
    sourceSHA256: createHash('sha256').update(source).digest('hex'),
    importer: 'occt-import-js 0.0.23', exporter: 'three 0.180.0 GLTFExporter',
    tessellation: TESSELLATION, ...stats, ...metadata,
    appearance: 'Original STEP geometry. PBR finishes and housing/contact colour masks are visual approximations. Exact duplicate meshes within a part removed; no decimation. No invented silkscreen or circuit traces.',
    components: COMPONENTS, schematic: catalogue.source,
  };
  await fs.writeFile(output.replace(/\.glb$/, '.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [input, output] = process.argv.slice(2);
  if (!input || !output?.endsWith('.glb')) throw new Error('Usage: node convert.mjs source.step output.glb');
  const occt = await occtImport();
  await writeModel(occt.ReadStepFile(await fs.readFile(input), TESSELLATION), input, output);
}
