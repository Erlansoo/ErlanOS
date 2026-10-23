// Pruebas de interacción y respuestas tardías; no sustituyen una revisión visual.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const eventos = {};
const svg = { addEventListener: (nombre, fn) => eventos[nombre] = fn };
const sandbox = { window: { addEventListener() {} }, URLSearchParams };
vm.createContext(sandbox);
for (const nombre of ['esquematico', 'bloques']) {
  const clase = nombre === 'bloques' ? 'VistaBloques' : 'VistaEsquematico';
  vm.runInContext(fs.readFileSync(path.join(__dirname, `../ui/${nombre}.js`), 'utf8') + `\nglobalThis.${clase} = ${clase};`, sandbox);
}
const vista = new sandbox.VistaEsquematico(svg);
let abiertos = [];
vista.onBloque = id => abiertos.push(id);
const ev = (target, key) => ({ target: { closest: () => target }, key, preventDefault() {}, stopPropagation() {} });
eventos.dblclick(ev({ dataset: { bloque: 'filtro' } }));
eventos.dblclick(ev(null)); // componente o cable, no título de bloque
eventos.keydown(ev({ dataset: { bloque: 'potencia' } }, 'Enter'));
assert.deepEqual(abiertos, ['filtro', 'potencia']);
svg.getBoundingClientRect = () => ({ width: 800, height: 400 });
svg.setAttribute = () => {};
vista.modelo = { bloques: [{ uuid: 'filtro', a: [10, 20], b: [110, 60] }] };
vista.enfocarBloque('filtro');
assert.equal(vista.usuarioMovio, true);
assert.equal(vista.vista.w / vista.vista.h, 2);
assert.ok(vista.vista.x < 10 && vista.vista.x + vista.vista.w > 110);

(async () => {
  const fichas = Object.create(sandbox.VistaBloques.prototype);
  let actual = { proyecto_id: 'a', revision: '1' }, dibujados = 0, cerrados = 0;
  const titulo = {};
  sandbox.document = { getElementById: () => titulo };
  fichas.contexto = () => actual; fichas.peticion = 0;
  fichas.cuerpo = { replaceChildren() {} }; fichas.nodo = () => ({});
  fichas.dialogo = { open: false, showModal() { this.open = true; }, close() { this.open = false; cerrados++; fichas.peticion++; } };
  fichas.dibujar = () => dibujados++;
  let resolver;
  sandbox.fetch = () => new Promise(resolve => { resolver = resolve; });
  const abrir = fichas.abrir('b1');
  actual = { proyecto_id: 'b', revision: '2' };
  fichas.actualizar(actual);
  resolver({ ok: true, json: async () => ({ ok: true, revision: '1', ficha: {} }) });
  await abrir;
  assert.equal(dibujados, 0); assert.equal(cerrados, 1);
  sandbox.fetch = async () => ({ ok: true, json: async () => ({ ok: true, revision: '2', ficha: {} }) });
  await fichas.abrir('b2'); assert.equal(dibujados, 1);
  actual = { ...actual, revision: '3' }; fichas.actualizar(actual);
  assert.equal(cerrados, 2);
  console.log('BLOQUES_UI PASS: doble clic, teclado, componente excluido, cambio de proyecto/revisión');
})().catch(e => { console.error(e); process.exitCode = 1; });
