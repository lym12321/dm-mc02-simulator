import { build } from 'esbuild';
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const directory = fileURLToPath(new URL('.', import.meta.url));
const vendor = fileURLToPath(new URL('../../src/vendor/', import.meta.url));
await mkdir(`${vendor}licenses/three`, { recursive: true });
await build({
  entryPoints: [`${directory}three-entry.js`], outfile: `${vendor}three.js`,
  bundle: true, minify: true, format: 'esm', target: 'es2022', legalComments: 'inline',
});
await copyFile(`${directory}node_modules/three/LICENSE`, `${vendor}licenses/three/LICENSE`);
await mkdir(`${vendor}licenses/meshoptimizer`, { recursive: true });
await copyFile(`${directory}node_modules/meshoptimizer/LICENSE.md`, `${vendor}licenses/meshoptimizer/LICENSE`);
const lock = JSON.parse(await readFile(`${directory}package-lock.json`, 'utf8'));
await writeFile(`${vendor}licenses/three/PROVENANCE.json`, JSON.stringify({
  upstream: 'https://github.com/mrdoob/three.js', version: '0.180.0', license: 'MIT',
  commit: '0af9729d0c143a86a1d725d6e2c3ad83301f3f34',
  distribution: lock.packages['node_modules/three'],
  build: 'npm ci && npm run bundle (apps/lab/tools/board-model)',
  local_changes: 'Unmodified upstream modules bundled with meshoptimizer decoder by esbuild 0.25.11; see three-entry.js and ../meshoptimizer/PROVENANCE.json.',
  sha256: createHash('sha256').update(await readFile(`${vendor}three.js`)).digest('hex'),
}, null, 2) + '\n');
await writeFile(`${vendor}licenses/meshoptimizer/PROVENANCE.json`, JSON.stringify({
  upstream: 'https://github.com/zeux/meshoptimizer', version: '1.3.0', license: 'MIT',
  distribution: lock.packages['node_modules/meshoptimizer'],
  build: 'npm ci && npm run bundle (apps/lab/tools/board-model)',
  local_changes: 'Unmodified meshoptimizer/decoder (embedded WASM) bundled into vendor/three.js. No runtime fetch or workers.',
  sha256: createHash('sha256').update(await readFile(`${vendor}three.js`)).digest('hex'),
}, null, 2) + '\n');
