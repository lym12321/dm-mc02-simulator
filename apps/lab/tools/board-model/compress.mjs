// Storage optimization of the existing mesh; no tessellation or quantization.
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { NodeIO, PropertyType } from '@gltf-transform/core';
import { EXTMeshoptCompression } from '@gltf-transform/extensions';
import { weld, reorder, dedup } from '@gltf-transform/functions';
import { MeshoptEncoder, MeshoptDecoder } from 'meshoptimizer';

const io = new NodeIO().registerExtensions([EXTMeshoptCompression]).registerDependencies({
  'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder,
});
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

export async function compressModel(input) {
  await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);
  const document = await io.readBinary(new Uint8Array(input));
  // weld() in 4.5.1 only merges bitwise-identical attribute tuples, preserving
  // hard edges. reorder() changes storage order, never vertex values or faces.
  await document.transform(weld(), reorder({ encoder: MeshoptEncoder, target: 'size' }),
    // Share byte-identical attribute/index arrays across parts; each part keeps
    // its own mesh, name, material and metadata for picking and live LED state.
    dedup({ propertyTypes: [PropertyType.ACCESSOR] }));
  document.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({
    // This writer mode skips lossy filters. It does NOT call quantize().
    // Do not replace this pipeline with the lossy meshopt() preset.
    method: EXTMeshoptCompression.EncoderMethod.QUANTIZE,
  });
  const binary = await io.writeBinary(document);
  return { binary, metadata: { glbBytes: binary.byteLength, glbSHA256: sha256(binary),
    compression: { extension: 'EXT_meshopt_compression', writer: '@gltf-transform 4.5.1',
      codec: 'meshoptimizer 1.3.0', preprocessing: 'Bitwise weld, size-oriented storage reorder and shared byte-identical attribute/index arrays; no quantization, filters or decimation.',
      inputBytes: input.byteLength, inputSHA256: sha256(input),
      precision: 'Preserves existing GLB float32 position/normal values and oriented triangles; STEP tessellation is unchanged.' } } };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [input, output, mode] = process.argv.slice(2);
  if (!input?.endsWith('.glb') || !output?.endsWith('.glb') || ![undefined, '--decode'].includes(mode)) {
    throw new Error('Usage: node compress.mjs input.glb output.glb [--decode]');
  }
  const bytes = await readFile(input);
  if (mode === '--decode') {
    // Standard uncompressed GLB for editors without Meshopt support.
    await MeshoptDecoder.ready;
    const document = await io.readBinary(bytes);
    document.getRoot().listExtensionsUsed().find(e => e.extensionName === EXTMeshoptCompression.EXTENSION_NAME)?.dispose();
    await io.write(output, document);
  } else {
    const provenance = JSON.parse(await readFile(input.replace(/\.glb$/, '.json'), 'utf8'));
    const { binary, metadata } = await compressModel(bytes);
    await writeFile(output, binary);
    await writeFile(output.replace(/\.glb$/, '.json'), JSON.stringify({ ...provenance, ...metadata }, null, 2) + '\n');
    console.log(JSON.stringify(metadata, null, 2));
  }
}
