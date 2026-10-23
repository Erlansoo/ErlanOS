// Unit-level JS smoke: no browser automation or simulated visual approval.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const context = { Option: function (label, value) { this.label = label; this.value = value; } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui/simulacion.js'), 'utf8') + '\nglobalThis.Vista = VistaSimulacion;', context);
const Vista = context.Vista;
for (const [x, i] of [[-1, 0], [0, 0], [5, 0], [6, 1], [10, 1], [15, 1], [99, 2]]) {
  assert.equal(Vista.masCercano([0, 10, 20], x), i);
}
assert.equal(Vista.masCercano([3], 8), 0);
// Check equivalence to nearest-point oracle across an irregular/log-like scale.
const xs = Array.from({ length: 1500 }, (_, i) => Math.log1p(i));
for (let i = 0; i < 100; i++) {
  const x = i / 99 * 8;
  let best = 0;
  for (let j = 1; j < xs.length; j++) if (Math.abs(xs[j] - x) < Math.abs(xs[best] - x)) best = j;
  assert.equal(Vista.masCercano(xs, x), best);
}

(async () => {
  const vista = Object.create(Vista.prototype);
  const elementos = {};
  let reconstrucciones = 0, descargas = 0, dibujos = 0;
  vista.el = nombre => elementos[nombre] ||= { value: '', replaceChildren() { reconstrucciones++; }, add() {} };
  vista.proyecto = 'p'; vista.id = 'a'; vista.consultando = false;
  vista.estado = mensaje => { throw new Error(mensaje); };
  vista.dibujar = () => { dibujos++; };
  const meta = { id: 'a', nombre: 'RC', creado: 1, estado: 'completado', vigencia: 'vigente' };
  const esLista = url => url.split('?')[0] === '/api/simulacion' && !url.includes('id=');
  vista.json = async url => {
    if (esLista(url)) return { trabajos: [{ ...meta }], motor_disponible: true };
    descargas++; return { ...meta, resultado: { series: [] } };
  };
  await vista.consultar(); await vista.consultar();
  assert.equal(descargas, 1); assert.equal(dibujos, 1); assert.equal(reconstrucciones, 2);
  meta.vigencia = 'obsoleto'; await vista.consultar();
  assert.equal(descargas, 1); assert.equal(dibujos, 2); assert.equal(vista.datos.vigencia, 'obsoleto');
  // Selection race: never paint a response belonging to the previous project.
  vista.firma = ''; vista.datos = null;
  vista.json = async url => {
    if (esLista(url)) return { trabajos: [{ ...meta }], motor_disponible: true };
    vista.proyecto = 'otro'; return { ...meta, resultado: {} };
  };
  await vista.consultar(); assert.equal(dibujos, 2);
  console.log('SIMULACION_UI PASS: cursor, consultas sin duplicados, invalidación y cambio de proyecto');
})().catch(error => { console.error(error); process.exitCode = 1; });
