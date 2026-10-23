'use strict';

const $ = id => document.getElementById(id);
const vistas = {
  esquematico: new VistaEsquematico($('hoja')),
  pcb: new VistaPlaca($('placa')),
};
const LIENZOS = { esquematico: 'hoja', pcb: 'placa' };
const ETAPAS_PLACA = {
  creada: 'contorno listo · huellas en espera',
  acomodada: 'acomodada · falta rutear',
  ruteada: 'ruteada · falta verificar',
  verificada: 'verificada por KiCad',
};
let modelos = {};
let actual = {};
let ultimoEvento = 0;
let pestana = 'esquematico';
let etapaVista = null;
let eligioUsuario = false;
let versionImagenes = 0;
let firmaImagenes = '';
let generando3d = false;
let temporizadorAviso = null;
let referenciaEnlazada = null;
let primerEstado = true;
let latidos = {};   // contadores por servicio (simulación, 3D, chat, documentos): la interfaz refresca sólo cuando cambian
const proyectoUrl = new URLSearchParams(location.search).get('proyecto');   // la pestaña que muestra esta ventana
let proyectoFijo = proyectoUrl;   // una vez elegida, la ventana se queda con su pestaña aunque otra pase a activa
let fuenteEventos = null;
let pestanasFirma = '';
let recientes = [];

// ------------------------------------------------ pestañas de proyectos
function urlDeProyecto(id, extra = {}) {
  const q = new URLSearchParams({ proyecto: id, ...extra });
  return `${location.pathname}?${q}`;
}

// Cambiar de pestaña no recarga la página: se cambia la conexión en vivo y se redibuja. Los últimos
// proyectos vistos se guardan en memoria (estado, etapa elegida y encuadre), así que volver a uno es
// instantáneo y luego el servidor lo pone al día.
const MEMORIA_MAX = 4;
const memoria = new Map();   // proyecto_id -> { estado, pestana, eligioUsuario, camaras }
let ultimoEstado = null;
let vigilante = null;

function recordarProyecto() {
  const id = actual.proyecto_id;
  if (!id || !ultimoEstado) return;
  const camaras = {};
  for (const [nombre, vista] of Object.entries(vistas)) camaras[nombre] = vista.usuarioMovio && vista.vista ? { ...vista.vista } : null;
  memoria.delete(id);
  memoria.set(id, { estado: { ...ultimoEstado, modelos }, pestana, eligioUsuario, camaras });
  while (memoria.size > MEMORIA_MAX) memoria.delete(memoria.keys().next().value);
}

function nombreDeProyecto(id) {
  return (pestanasLista.find(p => p.id === id) || {}).nombre || id.split(/[\\/]/).filter(Boolean).pop();
}

function cambiarAProyecto(id, { empujar = true } = {}) {
  recordarProyecto();
  const guardado = memoria.get(id);
  proyectoFijo = id;
  if (empujar) history.pushState(null, '', urlDeProyecto(id));
  abrirMenuPestanas(false);
  activaCentrada = false;
  pestanasFirma = '';
  etapaVista = null;
  referenciaEnlazada = null;
  for (const vista of Object.values(vistas)) { vista.refEnlazada = null; if (vista.seleccion) vista.seleccionar(null); }
  if (guardado) {
    pestana = guardado.pestana;
    eligioUsuario = guardado.eligioUsuario;
    etapaVista = (guardado.estado.actual || {}).etapa || 'esquematico';   // que aplicar no olvide la etapa elegida
    aplicar(guardado.estado);
    for (const [nombre, camara] of Object.entries(guardado.camaras)) {
      if (camara) { vistas[nombre].vista = camara; vistas[nombre].usuarioMovio = true; vistas[nombre].aplicarVista(); }
    }
  } else {
    eligioUsuario = false;
    cargando(nombreDeProyecto(id));
  }
  conectar();
}

// Mientras llega el primer estado del proyecto se ve «Abriendo…», no el fondo vacío. Si en 12 s no
// llega nada, se reconecta sola; si tampoco, ofrece reintentar en vez de obligar a pulsar F5.
function cargando(nombre) {
  $('cargando-nombre').textContent = nombre ? `Abriendo ${nombre}…` : 'Abriendo…';
  $('cargando-reintentar').hidden = true;
  $('cargando').hidden = false;
  modelos = {};
  actual = { proyecto_id: proyectoFijo };
  for (const lienzo of Object.values(LIENZOS)) $(lienzo).toggleAttribute('hidden', true);
  vigilar();
}

function vigilar() {
  clearTimeout(vigilante);
  let intentos = 0;
  const revisar = () => {
    if ($('cargando').hidden) return;
    intentos += 1;
    if (intentos <= 2) { conectar(); vigilante = setTimeout(revisar, 12000); }
    else $('cargando-reintentar').hidden = false;
  };
  vigilante = setTimeout(revisar, 12000);
}

$('cargando-reintentar').addEventListener('click', () => { $('cargando-reintentar').hidden = true; conectar(); vigilar(); });

function listo() {
  clearTimeout(vigilante);
  $('cargando').hidden = true;
}

async function irAProyecto(id) {
  if (id === actual.proyecto_id) return;
  cambiarAProyecto(id);
  pedir('/api/pestanas', { op: 'activar', proyecto_id: id }).catch(() => { /* la pestaña se cerró: el estado lo dirá */ });
}

window.addEventListener('popstate', () => {
  const id = new URLSearchParams(location.search).get('proyecto');
  if (id && id !== actual.proyecto_id) cambiarAProyecto(id, { empujar: false });
});

async function cerrarProyecto(id) {
  try {
    const r = await pedir('/api/pestanas', { op: 'cerrar', proyecto_id: id });
    memoria.delete(id);
    if (id !== actual.proyecto_id) return;
    ultimoEstado = null;   // que no se guarde en memoria la pestaña que se acaba de cerrar
    if (r.activo) cambiarAProyecto(r.activo);
    else location.href = location.pathname;
  } catch (err) { avisar(err.message, 'error'); }
}

let pestanasLista = [];

function crearPestana(p, activa, enMenu) {
  const tab = document.createElement('button');
  tab.type = 'button';
  tab.className = (enMenu ? 'proyecto-fila' : 'proyecto') + (activa ? ' activa' : '');
  tab.dataset.estado = p.estado || 'reposo';
  tab.title = `${p.nombre}\n${p.carpeta}${p.texto ? ' · ' + p.texto : ''}`;
  if (enMenu) tab.role = 'menuitem';
  const punto = document.createElement('i');
  punto.className = 'punto';
  const nombre = document.createElement('span');
  nombre.className = 'nombre';
  nombre.textContent = p.nombre;
  const cerrar = document.createElement('span');
  cerrar.className = 'cerrar';
  cerrar.textContent = '×';
  cerrar.title = 'Cerrar esta pestaña (el proyecto no se borra)';
  cerrar.addEventListener('click', ev => { ev.stopPropagation(); cerrarProyecto(p.id); });
  tab.append(punto, nombre, cerrar);
  tab.addEventListener('click', () => irAProyecto(p.id));
  // Clic con la rueda cierra, como en el navegador.
  tab.addEventListener('auxclick', ev => { if (ev.button === 1) { ev.preventDefault(); cerrarProyecto(p.id); } });
  return tab;
}

function aplicarPestanas(estado) {
  const lista = estado.pestanas || [];
  recientes = estado.recientes || [];
  const firma = JSON.stringify([estado.proyecto, lista.map(p => [p.id, p.nombre, p.estado])]);
  if (firma === pestanasFirma) return;
  pestanasFirma = firma;
  pestanasLista = lista;
  $('proyectos-lista').replaceChildren(...lista.map(p => crearPestana(p, p.id === estado.proyecto, false)));
  if (!$('proyectos-menu').hidden) pintarMenuPestanas();
  requestAnimationFrame(revisarDesborde);
}

// Las pestañas se encogen como en el navegador; si aun así no caben, la barra se desliza con la rueda
// y el botón ▾ dice cuántas quedan fuera de la vista y las lista todas.
let activaCentrada = false;
function revisarDesborde() {
  const caja = $('proyectos-lista');
  const boton = $('proyectos-todas');
  if (!activaCentrada) {
    const activa = caja.querySelector('.proyecto.activa');
    if (activa) { activaCentrada = true; activa.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'instant' }); }
  }
  caja.classList.toggle('sombra-izq', caja.scrollLeft > 2);
  caja.classList.toggle('sombra-der', caja.scrollLeft + caja.clientWidth < caja.scrollWidth - 2);
  const r = caja.getBoundingClientRect();
  const ocultas = [...caja.children].filter(t => {
    const b = t.getBoundingClientRect();
    return b.left < r.left - 4 || b.right > r.right + 4;
  });
  boton.hidden = pestanasLista.length < 2;
  boton.textContent = ocultas.length ? `▾ ${ocultas.length}` : '▾';
  boton.classList.toggle('con-ocultas', ocultas.length > 0);
  boton.title = ocultas.length
    ? `${ocultas.length} pestaña${ocultas.length > 1 ? 's' : ''} fuera de la vista: clic para verlas todas`
    : 'Ver todas las pestañas abiertas';
  boton.classList.toggle('trabajando', ocultas.some(t => ['actuando', 'pensando', 'verificando'].includes(t.dataset.estado)));
  boton.classList.toggle('error', ocultas.some(t => t.dataset.estado === 'error'));
}

function pintarMenuPestanas() {
  const filtro = $('proyectos-filtro').value.trim().toLowerCase();
  const filas = pestanasLista
    .filter(p => !filtro || `${p.nombre} ${p.carpeta}`.toLowerCase().includes(filtro))
    .map(p => crearPestana(p, p.id === actual.proyecto_id, true));
  if (!filas.length) {
    const vacio = document.createElement('p');
    vacio.className = 'vacio';
    vacio.textContent = 'Ninguna pestaña coincide';
    filas.push(vacio);
  }
  $('proyectos-menu-lista').replaceChildren(...filas);
}

function abrirMenuPestanas(abrir) {
  $('proyectos-menu').hidden = !abrir;
  $('proyectos-todas').setAttribute('aria-expanded', String(abrir));
  if (!abrir) return;
  $('proyectos-filtro').value = '';
  $('proyectos-filtro').hidden = pestanasLista.length < 7;
  pintarMenuPestanas();
  if (!$('proyectos-filtro').hidden) $('proyectos-filtro').focus();
}

$('proyectos-todas').addEventListener('click', ev => { ev.stopPropagation(); abrirMenuPestanas($('proyectos-menu').hidden); });
$('proyectos-filtro').addEventListener('input', pintarMenuPestanas);
$('proyectos-filtro').addEventListener('keydown', ev => {
  ev.stopPropagation();
  if (ev.key === 'Escape') abrirMenuPestanas(false);
  if (ev.key === 'Enter') $('proyectos-menu-lista').querySelector('.proyecto-fila')?.click();
});
document.addEventListener('click', ev => { if (!ev.target.closest('#proyectos-menu')) abrirMenuPestanas(false); });
document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !$('proyectos-menu').hidden) abrirMenuPestanas(false); });
$('proyectos-lista').addEventListener('wheel', ev => {
  const caja = $('proyectos-lista');
  if (caja.scrollWidth <= caja.clientWidth) return;
  ev.preventDefault();
  caja.scrollLeft += ev.deltaY || ev.deltaX;
}, { passive: false });
$('proyectos-lista').addEventListener('scroll', revisarDesborde, { passive: true });
window.addEventListener('resize', revisarDesborde);

function abrirDialogoProyecto() {
  const caja = $('proyectos-recientes');
  caja.replaceChildren();
  const abiertas = new Set((modelos && actual) ? [] : []);
  for (const carpeta of recientes) {
    const b = document.createElement('button');
    b.type = 'button';
    const nombre = carpeta.split(/[\\/]/).filter(Boolean).pop();
    b.textContent = nombre;
    const ruta = document.createElement('small');
    ruta.textContent = carpeta;
    b.append(ruta);
    b.addEventListener('click', () => { $('proyecto-carpeta').value = carpeta; $('form-proyecto').requestSubmit(); });
    caja.append(b);
  }
  $('proyecto-aviso').textContent = '';
  $('dialogo-proyecto').showModal();
  $('proyecto-carpeta').focus();
}

$('proyecto-abrir').addEventListener('click', abrirDialogoProyecto);
$('proyecto-cancelar').addEventListener('click', () => $('dialogo-proyecto').close());
$('form-proyecto').addEventListener('submit', async ev => {
  ev.preventDefault();
  const carpeta = $('proyecto-carpeta').value.trim();
  if (!carpeta) return;
  $('proyecto-aviso').textContent = 'Abriendo…';
  try {
    const r = await pedir('/api/pestanas', { op: 'abrir', carpeta });
    $('dialogo-proyecto').close();
    if (r.proyecto !== actual.proyecto_id) location.href = urlDeProyecto(r.proyecto);
  } catch (err) { $('proyecto-aviso').textContent = err.message; }
});

function enlazarSeleccion(etapa, clave) {
  const s = etapa === 'esquematico' && modelos.esquematico
    ? modelos.esquematico.simbolos.find(x => x.uuid === clave) : null;
  referenciaEnlazada = etapa === 'pcb' ? clave : (s ? s.ref : null);
  if (etapa === 'pcb') {
    const unidades = (modelos.esquematico?.simbolos || []).filter(x => x.ref === referenciaEnlazada);
    vistas.esquematico.seleccion = unidades.length === 1 ? unidades[0].uuid : null;
  } else {
    vistas.pcb.seleccion = (modelos.pcb?.huellas || []).some(x => x.ref === referenciaEnlazada) ? referenciaEnlazada : null;
  }
  for (const [nombre, vista] of Object.entries(vistas)) {
    vista.refEnlazada = referenciaEnlazada;
    for (const g of vista.svg.querySelectorAll('[data-ref]')) {
      g.classList.toggle('enlazado', !!referenciaEnlazada && g.dataset.ref === referenciaEnlazada);
      g.classList.toggle('seleccionado', vista.claveDe(g) === vista.seleccion);
    }
  }
  textoSeleccion();
}

function hora(ms) {
  return new Date(ms).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function avisar(texto, tipo = '') {
  const caja = $('aviso');
  caja.textContent = texto;
  caja.className = `visible ${tipo}`;
  clearTimeout(temporizadorAviso);
  temporizadorAviso = setTimeout(() => { caja.className = ''; }, 3200);
}

function iaOcupada() {
  const estado = (actual.halo || {}).estado;
  return estado === 'actuando' || estado === 'verificando' || estado === 'pensando';
}

async function pedir(ruta, cuerpo, reintento = false) {
  if (ruta === '/api/editar') cuerpo = { ...cuerpo, expected_revision: cuerpo.expected_revision ?? actual.revision, proyecto_id: cuerpo.proyecto_id ?? actual.proyecto_id };
  const r = await fetch(ruta, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Halo-Request': '1' }, body: JSON.stringify(cuerpo || {}) });
  const datos = await r.json().catch(() => ({ ok: false, error: 'respuesta inválida' }));
  if (r.status === 409 && ruta === '/api/editar' && !reintento && cuerpo.op !== 'deshacer') {
    // El proyecto cambió bajo nuestros pies (la IA, KiCad, otra ventana): se toma la revisión nueva y se repite una vez.
    const estado = await fetch('/api/estado' + (proyectoFijo ? '?proyecto=' + encodeURIComponent(proyectoFijo) : ''), { headers: { 'X-Halo-Request': '1' } }).then(x => x.json()).catch(() => null);
    const revision = estado?.actual?.revision;
    if (revision && revision !== cuerpo.expected_revision) return pedir(ruta, { ...cuerpo, expected_revision: revision }, true);
  }
  if (!r.ok || !datos.ok) throw new Error(datos.error || `error ${r.status}`);
  if (ruta === '/api/editar' && datos.revision) {
    actual.revision = datos.revision;   // la siguiente edición parte de esta revisión, sin esperar al servidor
    if ((cuerpo || {}).op !== 'verificar') actual.verificacion = null;
  }
  return datos;
}

async function editar(peticion) {
  if (peticion.etapa === 'pcb') avisar('KiCad aplica tu cambio en la placa…');
  try {
    const r = await pedir('/api/editar', peticion);
    if (peticion.etapa === 'pcb') $('aviso').className = '';
    return r;
  } catch (err) {
    avisar(err.message, 'error');
    throw err;
  }
}

function fila(dl, clave, valor, clase) {
  const dt = document.createElement('dt');
  dt.textContent = clave;
  const dd = document.createElement('dd');
  dd.textContent = valor;
  if (clase) dd.className = clase;
  dl.append(dt, dd);
}

function aplicarHalo() {
  if (pestana === 'espacio3d') {
    $('halo-texto').textContent = '';
    $('halo').dataset.estado = 'reposo';
    document.body.classList.remove('trabajando');
    return;
  }
  const halo = actual.halo || {};
  const estado = generando3d ? 'verificando' : (halo.estado || 'reposo');
  $('halo').dataset.estado = estado;
  $('halo-texto').textContent = generando3d ? 'generando vista 3D' : (halo.texto || '');
  document.body.classList.toggle('trabajando', estado === 'actuando' || estado === 'pensando');
}

// Casillas por capa de cobre (con su nombre propio si la capa lo tiene) y un botón para verla sola.
function aplicarCapas(m) {
  const caja = $('capas');
  const capas = m && m.tipo === 'pcb' && pestana === 'pcb' ? (m.capas || []) : [];
  const firma = `${capas.join('|')}#${JSON.stringify((m && m.nombres_capa) || {})}#${[...vistas.pcb.ocultas].join('|')}`;
  caja.hidden = !capas.length;
  if (caja.dataset.firma === firma) return;
  caja.dataset.firma = firma;
  caja.replaceChildren();
  for (const c of capas) {
    const etiqueta = document.createElement('label');
    const casilla = document.createElement('input');
    casilla.type = 'checkbox';
    casilla.checked = !vistas.pcb.ocultas.has(c);
    casilla.addEventListener('change', () => { vistas.pcb.alternarCapa(c); aplicarCapas(m); });
    const muestra = document.createElement('i');
    muestra.style.background = COLOR_CAPA[VistaPlaca.sufijo(c)] || '#999';
    const solo = document.createElement('button');
    solo.type = 'button';
    solo.className = 'solo';
    solo.textContent = 'sola';
    solo.title = `Ver sólo ${vistas.pcb.nombreCapa(c)} (otra vez para ver todas)`;
    solo.addEventListener('click', ev => { ev.preventDefault(); vistas.pcb.soloCapa(c); aplicarCapas(m); });
    etiqueta.append(casilla, muestra, document.createTextNode(vistas.pcb.nombreCapa(c)), solo);
    caja.append(etiqueta);
  }
}

function textoSeleccion() {
  const vista = vistas[pestana];
  if (!vista) return;
  if (pestana === 'pcb') {
    $('seleccion').textContent = vista.seleccion ? `${vista.seleccion} seleccionada · R rota · F la pasa a la otra cara`
      : 'Arrastra huellas con el botón izquierdo; el derecho panea. Al moverlas, sus ramas de pista vuelven a ser conexiones pendientes';
  } else {
    const s = vista.seleccion && modelos.esquematico ? modelos.esquematico.simbolos.find(x => x.uuid === vista.seleccion) : null;
    const et = vista.etiquetaSeleccionada();
    $('seleccion').textContent = vista.cableado ? 'Trazando un cable: clic en cada esquina · doble clic o Enter termina · Escape cancela'
      : s ? `${s.ref} seleccionado · R rota · X voltea en horizontal · Y en vertical (sus cables lo siguen)`
      : et ? `Etiqueta ${et.texto} seleccionada · R la rota sobre su punto de conexión · arrástrala y su cable la sigue`
      : vista.cableSeleccionado() ? 'Cable seleccionado · arrástralo (sus vecinos lo siguen) · Supr lo borra'
      : 'Izquierdo: seleccionar y arrastrar · derecho: panear · rueda: zoom · W traza un cable · R rota';
  }
}

function sinBarra(red) { return String(red || '').replace(/^\//, ''); }

function aplicarPanel() {
  $('panel').hidden = pestana === 'simulacion' || pestana === 'espacio3d';
  $('bitacora').hidden = pestana === 'simulacion' || pestana === 'espacio3d';
  if (pestana === 'simulacion' || pestana === 'espacio3d') return;
  $('proyecto').textContent = actual.proyecto || '—';
  $('panel-titulo').textContent = { esquematico: 'ESQUEMÁTICO', pcb: 'PLACA', '3d': 'VISTA 3D', fabricacion: 'FABRICACIÓN' }[pestana];
  const datos = $('datos');
  datos.replaceChildren();
  const m = modelos[pestana === '3d' || pestana === 'fabricacion' ? 'pcb' : pestana];
  if (m && m.tipo === 'esquematico') {
    fila(datos, 'Hoja', `${m.papel[0]} · ${m.nombre}`);
    fila(datos, 'Bloques', (m.bloques || []).length);
    fila(datos, 'Componentes', m.resumen.componentes);
    fila(datos, 'Potencia', m.resumen.simbolos_potencia);
    fila(datos, 'Cables', m.resumen.cables);
    fila(datos, 'Etiquetas', m.resumen.etiquetas);
  } else if (m && m.tipo === 'pcb') {
    const placa = actual.placa || {};
    const b = vistas.pcb.limitesContorno(m);
    if (placa.etapa) fila(datos, 'Etapa', placa.etapa === 'verificada' && (!actual.verificacion || actual.verificacion.revision !== actual.revision)
      ? 'cambios sin verificar' : (ETAPAS_PLACA[placa.etapa] || placa.etapa));
    if (b) fila(datos, 'Tamaño', `${(b[2] - b[0]).toFixed(1)} × ${(b[3] - b[1]).toFixed(1)} mm`);
    fila(datos, 'Capas de cobre', (m.capas || []).length || 2);
    fila(datos, 'Huellas', m.resumen.abajo ? `${m.resumen.huellas} · ${m.resumen.abajo} abajo` : m.resumen.huellas);
    const porCapa = {};
    for (const a of (m.areas || []).filter(x => !x.regla)) for (const c of a.capas) (porCapa[c] = porCapa[c] || []).push(a);
    for (const c of m.capas || []) {
      if (!porCapa[c]) continue;
      const zonas = porCapa[c].sort((x, y) => y.prioridad - x.prioridad)
        .map(a => `${sinBarra(a.red)}${a.prioridad ? ` (p${a.prioridad})` : ''}${a.rellena ? '' : ' · sin rellenar'}`);
      const plano = (placa.planos || {})[c] ? ' · plano' : '';
      fila(datos, `Cobre ${vistas.pcb.nombreCapa(c)}`, zonas.join(', ') + plano);
    }
    if (m.resumen.prohibidas) fila(datos, 'Áreas prohibidas', m.resumen.prohibidas);
    fila(datos, 'Redes', m.resumen.redes);
    fila(datos, 'Pistas', m.resumen.pistas);
    fila(datos, 'Vías', m.resumen.vias);
    fila(datos, 'Sin rutear', m.resumen.sin_rutear, m.resumen.sin_rutear ? 'mal' : 'ok');
  }
  aplicarCapas(pestana === 'fabricacion' ? null : m);
  const ver = $('verificacion');
  ver.replaceChildren();
  const v = actual.verificacion;
  const etapaDeVerificacion = v && v.drc ? 'pcb' : 'esquematico';
  if (!v || v.revision !== actual.revision || (!['3d', 'fabricacion'].includes(pestana) && etapaDeVerificacion !== (pestana === 'fabricacion' ? 'pcb' : pestana))) {
    fila(ver, 'Estado', 'versión actual sin verificar');
  } else {
    if (v.erc) fila(ver, 'ERC', `${v.erc.errores} errores · ${v.erc.avisos} avisos`, v.erc.errores ? 'mal' : 'ok');
    if (v.erc_detalle?.length) {
      // Cada violación con su sitio: clic para ir allí y seleccionar el elemento.
      const dd = document.createElement('dd'); dd.className = 'erc-lista';
      for (const x of v.erc_detalle) {
        const b = document.createElement('button'); b.type = 'button';
        b.className = x.severidad === 'error' ? 'mal' : 'aviso';
        b.textContent = `${x.severidad === 'error' ? '✖' : '⚠'} ${x.descripcion}: ${(x.elementos || []).join(' · ')}`;
        const it = (x.items || [])[0];
        if (it?.pos) b.addEventListener('click', () => { pestana = 'esquematico'; eligioUsuario = true; mostrar(); vistas.esquematico.enfocar(it.pos, it.uuid); textoSeleccion(); });
        dd.append(b);
      }
      ver.append(dd);
    }
    if (v.netlist) fila(ver, 'Conexiones', v.netlist.coinciden ? `${v.netlist.redes} redes exactas` : `${v.netlist.diferencias} diferencias`, v.netlist.coinciden ? 'ok' : 'mal');
    if (v.reglas) fila(ver, 'Dibujo', v.reglas.problemas ? `${v.reglas.problemas} problemas` : 'limpio', v.reglas.problemas ? 'mal' : 'ok');
    if (v.drc) fila(ver, 'DRC', `${v.drc.errores} errores · ${v.drc.avisos} avisos`, v.drc.errores ? 'mal' : 'ok');
    if (v.pendientes !== undefined) fila(ver, 'Sin conectar', v.pendientes, v.pendientes ? 'mal' : 'ok');
    if (v.paridad !== undefined) fila(ver, 'Paridad', v.paridad ? `${v.paridad} diferencias` : 'idéntica al esquemático', v.paridad ? 'mal' : 'ok');
    if (v.cuando) fila(ver, 'Hora', hora(v.cuando));
  }
  const edicion = (pestana === 'esquematico' && modelos.esquematico) || (pestana === 'pcb' && modelos.pcb);
  $('herramientas').hidden = !edicion;
  $('verificar').disabled = iaOcupada();
  $('deshacer').disabled = iaOcupada();
  $('serigrafia').hidden = pestana !== 'pcb';
  $('serigrafia').disabled = iaOcupada();
  $('cablear').hidden = pestana !== 'esquematico' || !vistas.esquematico.editable;
  $('cablear').disabled = iaOcupada();
  $('cablear').classList.toggle('activo', !!vistas.esquematico.cableado);
  textoSeleccion();
}

function aplicarActividad(lista) {
  const ol = $('eventos');
  ol.replaceChildren();
  for (const ev of lista.slice(-9).reverse()) {
    const li = document.createElement('li');
    if (ev.t > ultimoEvento) li.classList.add('nuevo');
    if (ev.nivel) li.classList.add(ev.nivel);
    if (ev.autor === 'usuario') li.classList.add('usuario');
    else if (ev.autor) li.classList.add('ia');
    const t = document.createElement('time');
    t.textContent = hora(ev.t);
    const s = document.createElement('span');
    s.textContent = ev.texto;
    if (ev.autor) s.dataset.autor = ev.autor === 'usuario' ? 'tú' : 'IA';
    li.append(t, s);
    ol.append(li);
  }
  if (lista.length) ultimoEvento = lista[lista.length - 1].t;
}

function aplicar3d() {
  const imagenes = actual.render3d || [];
  const firma = imagenes.join('|');
  if (firma !== firmaImagenes) { firmaImagenes = firma; versionImagenes += 1; }
  const caja = $('vista3d');
  const clave = `${versionImagenes}:${generando3d}`;
  if (caja.dataset.version === clave) return;
  caja.dataset.version = clave;
  caja.replaceChildren();
  if (generando3d && !imagenes.length) {
    const espera = document.createElement('p');
    espera.className = 'espera3d';
    espera.textContent = 'KiCad está generando la vista 3D de esta versión de la placa…';
    caja.append(espera);
    return;
  }
  imagenes.forEach((ruta, i) => {
    const figura = document.createElement('figure');
    const img = document.createElement('img');
    img.src = `/api/imagen?i=${i}&v=${versionImagenes}&proyecto=${encodeURIComponent(actual.proyecto_id || '')}`;
    img.alt = ruta.includes('arriba') ? 'Placa vista desde arriba' : 'Placa en perspectiva';
    const pie = document.createElement('figcaption');
    pie.textContent = ruta.includes('arriba') ? 'Desde arriba' : 'Perspectiva';
    figura.append(img, pie);
    caja.append(figura);
  });
}

async function pedir3d() {
  if (generando3d) return;
  generando3d = true;
  actual.render3d = [];
  mostrar();
  try {
    const r = await pedir('/api/render3d', { proyecto_id: actual.proyecto_id });
    actual.render3d = r.imagenes;
  } catch (err) {
    avisar(err.message, 'error');
  } finally {
    generando3d = false;
    mostrar();
  }
}

function mostrar() {
  fabricador.actualizar(actual, !!modelos.pcb && pestana === 'fabricacion');
  const disponibles = { esquematico: !!modelos.esquematico, pcb: !!modelos.pcb, '3d': !!modelos.pcb, fabricacion: !!modelos.pcb, simulacion: true, espacio3d: true, documentos: !!actual.proyecto_id };
  $('documentos').hidden = pestana !== 'documentos';
  document.body.classList.toggle('documentando', pestana === 'documentos');
  documentador.actualizar(actual, pestana === 'documentos');
  document.body.classList.toggle('creando3d', pestana === 'espacio3d');
  $('espacio3d').hidden = pestana !== 'espacio3d';
  window.dispatchEvent(new CustomEvent('halo-espacio3d-visible', { detail: pestana === 'espacio3d' }));
  document.body.classList.toggle('simulando', pestana === 'simulacion');
  $('simulacion').hidden = pestana !== 'simulacion';
  simulador.actualizar(actual, pestana === 'simulacion');
  fichas.actualizar(actual);
  $('bloques-navegacion').hidden = pestana !== 'esquematico' || !modelos.esquematico?.bloques?.length;
  const opcionesBloques = modelos.esquematico?.bloques || [];
  const firmaBloques = JSON.stringify([actual.proyecto_id, opcionesBloques.map(b => [b.uuid, b.titulo])]);
  if ($('bloques-lista').dataset.firma !== firmaBloques) {
    const previa = $('bloques-lista').value;
    $('bloques-lista').replaceChildren(...opcionesBloques.map(b => new Option(b.titulo || 'Bloque', b.uuid)));
    if (opcionesBloques.some(b => b.uuid === previa)) $('bloques-lista').value = previa;
    $('bloques-lista').dataset.firma = firmaBloques;
  }
  for (const b of document.querySelectorAll('.etapa')) {
    b.disabled = !disponibles[b.dataset.etapa];
    b.classList.toggle('activa', b.dataset.etapa === pestana);
  }
  // En SVG la propiedad .hidden no existe: se usa el atributo en todas las vistas.
  $('hoja').toggleAttribute('hidden', pestana !== 'esquematico');
  $('placa').toggleAttribute('hidden', pestana !== 'pcb' && pestana !== 'fabricacion');
  $('vista3d').toggleAttribute('hidden', pestana !== '3d');
  $('ajustar').toggleAttribute('hidden', ['3d', 'simulacion', 'espacio3d', 'documentos'].includes(pestana));
  $('vacio').toggleAttribute('hidden', disponibles[pestana]);
  for (const [nombre, lienzo] of Object.entries(LIENZOS)) {
    vistas[nombre].revision = actual.revision;
    vistas[nombre].proyectoId = actual.proyecto_id;
    vistas[nombre].editable = !iaOcupada();
    $(lienzo).classList.toggle('editable', vistas[nombre].editable);
  }
  // Fabricación muestra la misma placa, sólo para mirar: sin arrastres ni teclas de edición.
  if (pestana === 'fabricacion') { vistas.pcb.editable = false; $('placa').classList.remove('editable'); }
  const claveVista = pestana === 'fabricacion' ? 'pcb' : pestana;
  const vista = vistas[claveVista];
  if (vista && modelos[claveVista]) {
    if (vista.fuente !== modelos[claveVista]) { vista.fuente = modelos[claveVista]; vista.dibujar(modelos[claveVista]); }
    else vista.aplicarVista();
  }
  if (pestana === '3d') aplicar3d();
  aplicarHalo();
  aplicarPanel();
}

function aplicar(estado) {
  if (estado.proyecto && proyectoFijo && estado.proyecto !== proyectoFijo) return;   // un paquete del proyecto anterior
  aplicarPestanas(estado);
  if (estado.proyecto && !proyectoFijo) {
    // La ventana abrió sin pestaña elegida: se queda con la activa de ese momento, la dirección lo recuerda
    // al recargar y la conexión en vivo pasa a seguir sólo a esa pestaña.
    proyectoFijo = estado.proyecto;
    history.replaceState(null, '', urlDeProyecto(estado.proyecto, Object.fromEntries(new URLSearchParams(location.search).entries())));
    if (fuenteEventos) conectar();
  }
  if (!estado.proyecto && proyectoFijo && (estado.pestanas || []).length) {
    // Esta pestaña se cerró desde otro sitio: se pasa a la activa sin recargar.
    memoria.delete(proyectoFijo);
    ultimoEstado = null;
    cambiarAProyecto(estado.activo || estado.pestanas[0].id, { empujar: false });
    history.replaceState(null, '', urlDeProyecto(proyectoFijo));
    return;
  }
  ultimoEstado = estado;
  if (estado.proyecto) listo();
  actual = estado.actual || {};
  if (estado.modelos) modelos = estado.modelos;
  const etapa = actual.etapa || 'esquematico';
  if (etapa !== etapaVista) {
    etapaVista = etapa;
    if (pestana !== 'espacio3d') eligioUsuario = false;
  }
  if (!eligioUsuario) pestana = modelos[etapa] ? etapa : Object.keys(modelos)[0] || 'esquematico';
  if (primerEstado) {
    const inicial = new URLSearchParams(location.search).get('pestana');
    if (['esquematico', 'pcb', '3d', 'fabricacion', 'simulacion', 'espacio3d', 'documentos'].includes(inicial)) { pestana = inicial; eligioUsuario = true; }
    primerEstado = false;
  }
  aplicarActividad(estado.actividad || []);
  try { mostrar(); }
  catch (err) {
    // Un fallo al dibujar no debe dejar la ventana en blanco: se avisa y el siguiente estado lo vuelve a intentar.
    console.error(err);
    avisar('No se pudo dibujar este estado: ' + err.message, 'error');
  }
  window.dispatchEvent(new CustomEvent('halo-estado', { detail: { proyecto: actual.proyecto_id || proyectoFijo || null } }));
  if (estado.latidos) {
    const cambiados = Object.keys(estado.latidos).filter(k => latidos[k] !== estado.latidos[k]);
    latidos = estado.latidos;
    if (cambiados.length) window.dispatchEvent(new CustomEvent('halo-latido', { detail: cambiados }));
  }
}

function conectar() {
  if (fuenteEventos) fuenteEventos.close();
  const fuente = new EventSource('/api/eventos' + (proyectoFijo ? '?proyecto=' + encodeURIComponent(proyectoFijo) : ''));
  fuenteEventos = fuente;
  fuente.addEventListener('estado', ev => { if (fuente === fuenteEventos) aplicar(JSON.parse(ev.data)); });
  fuente.onopen = () => { $('conexion').className = 'enlazado'; $('conexion').textContent = 'en vivo'; };
  fuente.onerror = () => { $('conexion').className = 'desconectado'; $('conexion').textContent = 'reconectando'; };
}

for (const b of document.querySelectorAll('.etapa')) {
  b.addEventListener('click', () => {
    pestana = b.dataset.etapa;
    eligioUsuario = true;
    mostrar();
    if (pestana === '3d') pedir3d();   // el 3D sólo se genera cuando se pide
  });
}
$('ajustar').addEventListener('click', () => {
  const vista = vistas[pestana];
  if (vista) { vista.usuarioMovio = false; vista.ajustar(); }
});

for (const nombre of Object.keys(LIENZOS)) {
  vistas[nombre].onEditar = editar;
  vistas[nombre].onSeleccion = clave => enlazarSeleccion(nombre, clave);
  vistas[nombre].onCableado = () => { $('cablear').classList.toggle('activo', !!vistas[nombre].cableado); textoSeleccion(); };
}
$('verificar').addEventListener('click', async () => {
  const etapa = pestana === 'pcb' ? 'pcb' : 'esquematico';
  avisar(etapa === 'pcb' ? 'KiCad está revisando la placa: DRC y paridad…' : 'KiCad está verificando tus cambios…');
  try {
    const r = await pedir('/api/editar', { op: 'verificar', etapa });
    const ok = etapa === 'pcb'
      ? r.resultado.drc.errores === 0 && !r.resultado.pendientes && !r.resultado.paridad
      : r.resultado.erc.errores === 0 && r.resultado.netlist.coinciden;
    const bien = etapa === 'pcb' ? 'Placa verificada: DRC limpio, todo conectado y paridad idéntica'
      : 'Verificado: tus cambios no rompieron ninguna conexión';
    avisar(ok ? bien : 'Revisa: KiCad encontró diferencias', ok ? 'ok' : 'error');
  } catch (err) { avisar(err.message, 'error'); }
});
$('deshacer').addEventListener('click', () => editar({ op: 'deshacer', etapa: pestana }).catch(() => {}));

// ---- buscador de componentes: referencia → la vista va allí y lo selecciona (esquemático y placa)
function candidatos() {
  if (pestana === 'pcb' || pestana === 'fabricacion') return (modelos.pcb?.huellas || []).map(h => ({ ref: h.ref, valor: h.valor, at: h.at, clave: h.ref, etapa: 'pcb' }));
  return (modelos.esquematico?.simbolos || []).filter(s => !s.power).map(s => ({ ref: s.ref, valor: s.valor, at: s.at, clave: s.uuid, etapa: 'esquematico' }));
}
function irA(c) {
  if (!c || !c.at) return;
  const v = vistas[c.etapa];
  if (!v) return;
  if (pestana !== c.etapa) { pestana = c.etapa; eligioUsuario = true; mostrar(); }
  v.enfocar({ x: c.at[0], y: c.at[1] }, null);
  v.seleccionar(c.clave);
  textoSeleccion();
  avisar(`${c.ref} · ${c.valor || ''}`, 'ok');
}
$('buscar').addEventListener('input', () => {
  const q = $('buscar').value.trim().toLowerCase();
  const lista = $('buscar-lista'); lista.replaceChildren();
  if (!q) return;
  const vistos = new Set();
  for (const c of candidatos()) {
    if (vistos.has(c.ref) || !(c.ref.toLowerCase().includes(q) || (c.valor || '').toLowerCase().includes(q))) continue;
    vistos.add(c.ref);
    const o = document.createElement('option'); o.value = c.ref; o.label = c.valor || ''; lista.append(o);
    if (vistos.size >= 40) break;
  }
});
$('buscar').addEventListener('change', () => {           // elegir en la lista (o Enter) lleva al componente
  const q = $('buscar').value.trim().toLowerCase();
  const todos = candidatos();
  const c = todos.find(x => x.ref.toLowerCase() === q) || todos.find(x => x.ref.toLowerCase().includes(q));
  if (c) irA(c); else if (q) avisar(`No hay ningún ${$('buscar').value} en esta etapa`, 'error');
});
$('buscar').addEventListener('keydown', ev => { if (ev.key === 'Escape') { $('buscar').value = ''; $('buscar').blur(); } ev.stopPropagation(); });

$('cablear').addEventListener('click', () => {
  const v = vistas.esquematico;
  if (v.cableado) v.cancelarCableado(); else v.iniciarCableado();
});
$('serigrafia').addEventListener('click', async () => {
  avisar('KiCad busca serigrafía encimada…');
  try {
    const r = await pedir('/api/editar', { op: 'serigrafia', etapa: 'pcb' });
    const n = r.resultado || {};
    avisar(n.marcadas ? `Serigrafía: ${n.referencias} referencias y ${n.rotulos} rótulos apartados de ${n.marcadas} marcados`
      : 'KiCad no marca serigrafía encimada', 'ok');
  } catch (err) { avisar(err.message, 'error'); }
});
// Clic izquierdo fuera del lienzo y de los controles: nada queda seleccionado.
document.addEventListener('click', ev => {
  if (ev.button !== 0 || ev.target.closest('#hoja, #placa, button, input, select, textarea, a, label, dialog, #chat, #eventos')) return;
  const vista = vistas[pestana];
  if (vista?.seleccion) vista.seleccionar(null);
});
window.addEventListener('keydown', ev => {
  const vista = vistas[pestana];
  const foco = ev.target instanceof Element ? ev.target : null;   // el foco puede estar en el documento mismo
  if (!vista || foco?.closest('input, textarea, select, dialog, [data-bloque]')) return;
  // Con el foco en un botón (recién pulsada una etapa), Enter y espacio son del botón; el resto de atajos siguen valiendo.
  if (foco?.closest('button') && (ev.key === 'Enter' || ev.key === ' ')) return;
  const tecla = ev.key.toLowerCase();
  if ((ev.ctrlKey || ev.metaKey) && tecla === 'z') {
    ev.preventDefault();
    editar({ op: 'deshacer', etapa: pestana }).catch(() => {});
  } else if (vista.cableado && ev.key === 'Enter') {
    ev.preventDefault(); vista.terminarCableado();
  } else if (vista.cableado && ev.key === 'Escape') {
    vista.cancelarCableado();
  } else if (tecla === 'w' && pestana === 'esquematico' && vista.editable && !iaOcupada()) {
    vista.iniciarCableado();
  } else if ((ev.key === 'Delete' || ev.key === 'Backspace') && pestana === 'esquematico' && vista.editable && vista.cableSeleccionado()) {
    editar({ op: 'borrar_cable', clave: vista.seleccion, etapa: 'esquematico' }).catch(() => {});
  } else if ((tecla === 'x' || tecla === 'y') && pestana === 'esquematico' && vista.seleccion && vista.editable && !vista.cableSeleccionado() && !vista.etiquetaSeleccionada()) {
    // Como en KiCad: X voltea en horizontal (espejo «y» del archivo), Y en vertical (espejo «x»).
    editar({ op: 'espejar', clave: vista.seleccion, eje: tecla === 'x' ? 'y' : 'x', etapa: 'esquematico' }).catch(() => {});
  } else if (tecla === 'r' && vista.seleccion && vista.editable) {
    const op = pestana === 'esquematico' && vista.etiquetaSeleccionada() ? 'rotar_etiqueta' : 'rotar';
    editar({ op, clave: vista.seleccion, grados: 90, etapa: pestana }).catch(() => {});
  } else if (tecla === 'f' && pestana === 'pcb' && vista.seleccion && vista.editable) {
    editar({ op: 'voltear', clave: vista.seleccion, etapa: 'pcb' }).catch(() => {});
  } else if (ev.key === 'Escape') {
    vista.seleccionar(null);
  }
});

const fabricador = new VistaFabricacion(cuerpo => pedir('/api/fabricacion', cuerpo));
const documentador = new VistaDocumentos({ pedir: cuerpo => pedir('/api/documentos', cuerpo) });
const simulador = new VistaSimulacion({
  pedir: cuerpo => pedir('/api/simulacion', cuerpo),
  seleccionar: ref => {
    if (!modelos.esquematico) return;
    pestana = 'esquematico'; eligioUsuario = true;
    const s = modelos.esquematico.simbolos.find(x => x.ref === ref);
    if (s) { vistas.esquematico.seleccion = s.uuid; enlazarSeleccion('esquematico', s.uuid); }
    mostrar();
  },
});
const parametros = new URLSearchParams(location.search);
const fichas = new VistaBloques({
  contexto: () => actual,
  simulacion: id => {
    pestana = 'simulacion'; eligioUsuario = true; mostrar();
    simulador.id = id; simulador.firma = ''; simulador.consultar();
  },
});
vistas.esquematico.onBloque = uuid => fichas.abrir(uuid);
$('bloques-acercar').onclick = () => vistas.esquematico.enfocarBloque($('bloques-lista').value);
$('bloques-ficha').onclick = () => fichas.abrir($('bloques-lista').value);
if (parametros.has('captura')) {
  // Modo captura: una sola lectura del estado, sin conexión en vivo, para imágenes de evidencia.
  $('conexion').className = 'enlazado';
  $('conexion').textContent = 'instantánea';
  fetch('/api/estado' + (proyectoUrl ? '?proyecto=' + encodeURIComponent(proyectoUrl) : '')).then(r => r.json()).then(estado => {
    aplicar(estado);
    const pedida = parametros.get('pestana');
    if (pedida) { pestana = pedida; eligioUsuario = true; mostrar(); }
    const sola = parametros.get('capa');
    if (sola && modelos.pcb) { vistas.pcb.soloCapa(sola); aplicarPanel(); }
  });
} else {
  if (proyectoUrl) cargando(null);
  conectar();
}
