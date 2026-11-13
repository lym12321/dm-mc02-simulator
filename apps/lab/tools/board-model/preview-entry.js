import {BoardModel} from '../../src/board-model.js';

const $ = id => document.getElementById(id);
const packed = Uint8Array.from(atob($('model-data').textContent.trim()), char => char.charCodeAt(0));
$('model-data').remove();
const model = new BoardModel($('model'), {
  select: part => {
    $('part-ref').textContent = part.ref;
    $('part-name').textContent = part.label;
    $('part-description').textContent = part.description;
    $('part-value').textContent = part.value || '';
    $('part-source').textContent = part.schematicPage ? `原理图 · 第 ${part.schematicPage} 页` : '';
    $('part-select').value = part.ref;
  },
  error: error => { $('loading').hidden = false; $('loading').textContent = error.message; },
});
try {
  const data = new Uint8Array(await new Response(new Blob([packed]).stream()
    .pipeThrough(new DecompressionStream('gzip'))).arrayBuffer());
  await model.load(data.buffer);
  $('loading').hidden = true;
  $('model').dataset.parts = model.parts.size;
  for (const view of ['perspective', 'top', 'bottom']) $(view).addEventListener('click', () => model.home(view));
  $('zoom-in').addEventListener('click', () => model.zoom(.8));
  $('zoom-out').addEventListener('click', () => model.zoom(1.25));
  const select = $('part-select');
  for (const part of [...model.parts.values()].sort((a, b) => a.userData.ref.localeCompare(b.userData.ref, 'en', {numeric: true}))) {
    const option = document.createElement('mdui-menu-item'); option.value = part.userData.ref;
    option.textContent = `${part.userData.ref} · ${part.userData.label || part.userData.ref}${part.userData.value ? ` · ${part.userData.value}` : ''}`;
    select.append(option);
  }
  select.addEventListener('change', () => {
    model.select(select.value); model.onSelect(model.parts.get(select.value).userData);
  });
  const download = URL.createObjectURL(new Blob([data], {type: 'model/gltf-binary'}));
  $('download').href = download;
  window.addEventListener('pagehide', () => { model.dispose(); URL.revokeObjectURL(download); }, {once: true});
} catch (error) {
  $('loading').textContent = `无法显示三维模型：${error.message}。请用支持 WebGL 2 的浏览器打开。`;
}
