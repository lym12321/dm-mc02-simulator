import {build} from 'esbuild';
import {readFile, writeFile, mkdir, copyFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {gzipSync} from 'node:zlib';

const source = fileURLToPath(new URL('../../src/', import.meta.url));
const output = path.resolve(process.argv[2] || 'build/board-3d-delivery');
await mkdir(output, {recursive: true});
const {outputFiles} = await build({entryPoints: [fileURLToPath(new URL('preview-entry.js', import.meta.url))],
  bundle: true, write: false, minify: true, format: 'esm', target: 'es2022', legalComments: 'inline'});
const script = text => text.replace(/<\/script/gi, '<\\/script');
const [mdui, css, binary, license, meshoptLicense, metadata] = await Promise.all([
  readFile(`${source}vendor/mdui.js`, 'utf8'), readFile(`${source}vendor/mdui.css`, 'utf8'),
  readFile(`${source}models/dm-mc02.glb`),
  readFile(`${source}vendor/licenses/three/LICENSE`, 'utf8'),
  readFile(`${source}vendor/licenses/meshoptimizer/LICENSE`, 'utf8'), readFile(`${source}models/dm-mc02.json`, 'utf8'),
]);
const stats = JSON.parse(metadata);
const html = `<!doctype html>
<html lang="zh-CN" class="mdui-theme-light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>喵板 · DM-MC02 三维模型</title><style>${css}</style>
<style>
*{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif;color:#26382f;background:#f7f9f5}
header{display:flex;align-items:center;justify-content:space-between;padding:22px 32px;border-bottom:1px solid #dce3d9;gap:20px}
h1{font-size:22px;margin:4px 0;font-weight:600}header small{font-size:10px;letter-spacing:2px;color:#687d6a}
main{height:calc(100dvh - 105px);min-height:500px;display:grid;grid-template-columns:minmax(0,1fr) 280px;padding:16px 24px;gap:24px}
.stage{position:relative;min-width:0;display:flex;flex-direction:column;border-radius:16px;background:#e8eee6;overflow:hidden}
nav{display:flex;gap:4px;flex-wrap:wrap;align-items:center;padding:12px;z-index:1;background:#edf1ea}
nav span{margin-right:auto;font-size:12px;color:#657963;padding:0 10px}
#model{position:relative;flex:1;min-height:260px;background:radial-gradient(ellipse at 50% 42%,#fafbf8,#e1e7e0)}
#model canvas{display:block;width:100%;height:100%;touch-action:none}
#model canvas:focus-visible{outline:2px solid #599b8b;outline-offset:-3px}
#loading{position:absolute;top:45%;left:12px;right:12px;text-align:center;font-size:13px}
.hint{padding:12px 20px;font-size:11px;color:#697d68}aside{padding:16px 0;overflow:auto}
aside h2{font-size:19px;margin:12px 0}aside p{font-size:13px;line-height:1.9;color:#667364}
#part-ref{font-family:monospace;font-size:12px;color:#397965;letter-spacing:1px}
#part-value{font-size:12px;overflow-wrap:anywhere;color:#397965}#part-source{font-size:11px}
#part-select::part(menu){max-height:320px;overflow-y:auto}
.stats{display:flex;gap:22px;padding:18px 0;margin:20px 0;border-block:1px solid #dce3d9}
.stats strong{display:block;font-size:22px;font-weight:500}.stats span{font-size:11px;color:#697d68}
details{font-size:11px;color:#697d68;margin-top:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:10px}
@media(max-width:800px){header{padding:16px}main{height:auto;min-height:calc(100dvh - 100px);display:flex;flex-direction:column;padding:12px;gap:12px}.stage{height:65dvh;min-height:400px}aside{padding:12px}nav span{width:100%}h1{font-size:18px}}
</style><script>${script(mdui)}</script></head><body>
<header><div><small>BOARD STUDY / 01</small><h1>喵板 · DM-MC02</h1></div><mdui-button id="download" variant="outlined" download="dm-mc02.glb">下载 GLB 模型</mdui-button></header>
<main><section class="stage"><nav><span>三维装配 · 56 × 40 mm</span><mdui-button id="perspective" variant="text">整体</mdui-button><mdui-button id="top" variant="text">俯视</mdui-button><mdui-button id="bottom" variant="text">背面</mdui-button><mdui-button id="zoom-out" variant="text" aria-label="缩小">−</mdui-button><mdui-button id="zoom-in" variant="text" aria-label="放大">+</mdui-button></nav><div id="model"><div id="loading">正在加载三维装配…</div></div><div class="hint">拖动旋转 · 滚轮缩放 · 右键 / 双指平移 · 点击查看部件</div></section>
<aside><div id="part-ref">CtrBoard-H7 / V1.0</div><h2 id="part-name">DM-MC02 控制板</h2><mdui-select id="part-select" variant="outlined" label="选择器件 / 接口"></mdui-select><p id="part-value"></p><p id="part-description">选择元件，查看位号、型号、参数和电路用途。拖动板卡可查看正反面与连接器内部。</p><p id="part-source"></p>
<div class="stats"><div><strong>${stats.parts}</strong><span>装配部件</span></div><div><strong>${(stats.triangles / 10000).toFixed(1)} 万</strong><span>三角面</span></div></div>
<p>几何取自原始 STEP，元件信息按 CtrBoard-H7 V1.0 原理图核对。</p>
<details><summary>模型与软件来源</summary><pre id="provenance"></pre><pre>${license}</pre><pre>${meshoptLicense}</pre><p>MDUI 2.1.4 · MIT；完整声明随模型包附带。</p></details></aside></main>
<script id="model-data" type="application/gzip">${gzipSync(binary, {level: 9}).toString('base64')}</script>
<script>document.getElementById('provenance').textContent=${JSON.stringify(metadata)};</script>
<script type="module">${script(outputFiles[0].text)}</script></body></html>`;
await writeFile(`${output}/dm-mc02-preview.html`, html);
for (const file of ['dm-mc02.glb', 'dm-mc02.json']) await copyFile(`${source}models/${file}`, `${output}/${file}`);
await copyFile(fileURLToPath(new URL('parts.json', import.meta.url)), `${output}/dm-mc02-parts.json`);
await copyFile(`${source}vendor/licenses/mdui/LICENSE`, `${output}/LICENSE-mdui.txt`);
await copyFile(`${source}vendor/licenses/mdui/THIRD-PARTY-NOTICES.txt`, `${output}/MDUI-THIRD-PARTY-NOTICES.txt`);
await writeFile(`${output}/LICENSE-three.txt`, license);
await writeFile(`${output}/LICENSE-meshoptimizer.txt`, meshoptLicense);
await writeFile(`${output}/README.txt`, `DM-MC02 三维模型\n\n用当前 Chrome / Edge 打开 dm-mc02-preview.html，即可离线旋转、缩放、查看正反面，点击元件查看位号、型号、参数和用途。\n\ndm-mc02.glb：glTF 2.0 二进制，米制、Y 向上，使用 Meshopt 压缩。普通 GLB 导出命令：node compress.mjs dm-mc02.glb uncompressed.glb --decode。\ndm-mc02-parts.json：逐位号元件目录、原理图页码与来源。\ndm-mc02.json：STEP 和原理图指纹、网格参数、尺寸及接口对应关系。\n\n${stats.parts} 个装配部件，${stats.triangles.toLocaleString('en-US')} 个三角面，保留坐标和法线精度。\n模型 ${stats.glbBytes.toLocaleString('en-US')} bytes。独立 HTML 内嵌模型、脚本和样式。\n几何来自用户提供的 STEP，元件信息按 CtrBoard-H7_V1.0-240124.pdf 核对。\n材质使用 PBR 表现；CAD 与几何权利归原权利人，软件许可证随包提供。\n\nSimulator 首页支持 USER / RESET 按键、板体姿态输入、实时灯光与声音、元件搜索定位、UART/CAN 停靠面板和二维切换。\n源码及重建说明：apps/lab/docs/BOARD.md 和 apps/lab/tools/board-model/。\n`);
console.log(output);
