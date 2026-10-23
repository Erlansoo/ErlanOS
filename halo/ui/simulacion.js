'use strict';

class VistaSimulacion {
  static masCercano(xs, objetivo) {
    let lo = 0, hi = xs.length;
    while (lo < hi) {
      const m = (lo + hi) >>> 1;
      if (xs[m] < objetivo) lo = m + 1;
      else hi = m;
    }
    if (lo === 0) return 0;
    if (lo === xs.length) return lo - 1;
    return objetivo - xs[lo - 1] <= xs[lo] - objetivo ? lo - 1 : lo;
  }

  constructor({ pedir, seleccionar }) {
    this.pedir = pedir; this.seleccionar = seleccionar;
    this.actual = {}; this.visible = false; this.id = ''; this.proyecto = null;
    this.ocupada = false; this.consultando = false; this.firma = ''; this.datos = null;
    this.el = id => document.getElementById(`sim-${id}`);
    this.el('ejecutar').onclick = () => this.simular();
    this.el('cancelar').onclick = () => this.accion(async () => {
      await this.pedir({ op: 'cancelar', id: this.id }); await this.consultar();
    });
    this.el('trabajos').onchange = () => { this.id = this.el('trabajos').value; this.firma = ''; this.consultar(); };
    this.el('plantilla').onclick = () => this.accion(async () => {
      const r = await this.pedir({ op: 'inventario', ...this.contexto() });
      this.el('config').value = JSON.stringify(r.config, null, 2);
      this.el('redes').textContent = `Redes disponibles: ${r.redes.join(', ')}`;
      this.estado('Plantilla preparada. Asigna modelos, fuentes y sondas antes de simular.');
    });
    this.el('guardar').onclick = () => this.accion(async () => {
      await this.pedir({ op: 'configurar', ...this.contexto(), config: this.config() });
      this.estado('Configuración guardada en el proyecto. Se validará al simular.');
    });
    this.el('comparar').onclick = () => this.accion(async () => {
      const r = await this.pedir({ op: 'comparar', id: this.id, base: this.el('base').value });
      this.el('comparacion')?.remove();
      const caja = this.nodo('div', '', 'sim-comparacion'); caja.id = 'sim-comparacion';
      caja.append(this.nodo('h3', 'Antes y después'));
      caja.append(this.nodo('p', `Base: ${r.vigencia_base} · actual: ${r.vigencia}. ${r.nota}`));
      for (const m of r.cambios) caja.append(this.nodo('p', `${m.id}: ${this.numero(m.antes)} → ${this.numero(m.despues)} · diferencia ${this.numero(m.delta)}`));
      if (!r.cambios.length) caja.append(this.nodo('p', 'No hay mediciones equivalentes para comparar.'));
      this.el('resultado').append(caja);
    });
    // Sin temporizador: el servidor avisa por SSE cuando cambia una simulación; sólo mientras una corre se consulta cada 2 s.
    window.addEventListener('halo-latido', ev => { if (ev.detail.includes('simulacion') && this.visible) this.consultar(); });
  }

  seguir(activa) {
    if (typeof setTimeout !== 'function') return;   // sin navegador (pruebas)
    clearTimeout(this.seguimiento);
    if (activa && this.visible) this.seguimiento = setTimeout(() => this.consultar(), 2000);
  }

  contexto() { return { proyecto_id: this.actual.proyecto_id, expected_revision: this.actual.revision }; }
  config() {
    const t = this.el('config').value.trim();
    if (!t) throw new Error('Carga la plantilla y configura el banco de pruebas del proyecto.');
    return JSON.parse(t);
  }
  nodo(tag, texto = '', clase = '') {
    const n = document.createElement(tag); n.textContent = texto; n.className = clase; return n;
  }
  numero(n) { return n === null || n === undefined ? 'no disponible' : Number(n).toPrecision(5); }
  estado(texto, error = false) {
    this.el('estado').textContent = texto; this.el('estado').classList.toggle('sim-error', error);
  }
  async accion(fn) {
    if (this.ocupada) return;
    this.ocupada = true; this.el('ejecutar').disabled = true;
    try { await fn(); } catch (e) { this.estado(e.message, true); }
    finally { this.ocupada = false; this.el('ejecutar').disabled = false; }
  }
  actualizar(actual, visible) {
    const cambio = this.proyecto !== actual.proyecto_id;
    this.actual = actual;
    if (cambio) {
      this.proyecto = actual.proyecto_id; this.id = ''; this.firma = ''; this.datos = null;
      this.el('config').value = ''; this.el('redes').textContent = '';
      this.el('resultado').replaceChildren();
      this.cargarConfig = true;
    }
    const abrir = visible && !this.visible;
    this.visible = visible;
    if (visible && (abrir || cambio)) this.consultar();
  }
  async json(url) {
    const r = await fetch(url); const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'No se pudo consultar la simulación');
    return d;
  }
  async consultar() {
    if (this.consultando) return;
    this.consultando = true;
    const proyecto = this.proyecto;
    try {
      const r = await this.json('/api/simulacion' + (proyecto ? '?proyecto=' + encodeURIComponent(proyecto) : ''));
      if (proyecto !== this.proyecto) return;
      this.seguir(r.trabajos.some(t => t.estado === 'ejecutando' || t.estado === 'cancelando'));
      this.el('motor').textContent = r.motor_disponible ? 'ngspice · disponible' : 'ngspice · no encontrado';
      if (this.cargarConfig) {
        if (r.config) this.el('config').value = JSON.stringify(r.config, null, 2);
        this.el('banco').value = this.proyecto ? 'proyecto' : 'rc';   // con un proyecto abierto, su banco; los ejemplos se eligen a mano
        this.cargarConfig = false;
      }
      // Por defecto, la última ejecución del proyecto; los ejemplos sólo si no hay ninguna propia.
      if (!this.id && r.trabajos.length) this.id = (r.trabajos.find(t => t.proyecto_id === this.proyecto) || r.trabajos[0]).id;
      const base = this.el('base').value;
      const firmaLista = JSON.stringify([this.id, r.trabajos.map(t => [t.id, t.nombre, t.estado, t.vigencia])]);
      if (firmaLista !== this.firmaLista) {
        this.firmaLista = firmaLista;
        for (const nombre of ['trabajos', 'base']) {
          const select = this.el(nombre); select.replaceChildren(new Option(nombre === 'base' ? 'Sin comparación' : 'Sin ejecuciones', ''));
          for (const t of r.trabajos) {
            if (nombre === 'base' && (t.id === this.id || t.estado !== 'completado')) continue;
            const hora = new Date(t.creado * 1000).toLocaleTimeString('es');
            select.add(new Option(`${hora} · ${t.nombre} · ${t.estado} · ${t.vigencia}`, t.id));
          }
          select.value = nombre === 'base' ? base : this.id;
        }
      }
      if (this.id) {
        const id = this.id;
        const resumen = r.trabajos.find(t => t.id === id) || await this.json(`/api/simulacion?id=${encodeURIComponent(id)}&resumen=1`);
        if (proyecto !== this.proyecto || id !== this.id) return;
        const firma = `${resumen.id}:${resumen.estado}:${resumen.vigencia}`;
        this.el('cancelar').disabled = !['preparado', 'ejecutando'].includes(resumen.estado);
        if (firma === this.firma) return;
        // Results are immutable: fetch curves only on completion or explicit selection.
        const d = resumen.estado === 'completado'
          ? (this.datos?.id === id && this.datos.resultado ? { ...this.datos, ...resumen }
            : await this.json(`/api/simulacion?id=${encodeURIComponent(id)}`)) : resumen;
        if (proyecto !== this.proyecto || id !== this.id) return;
        this.datos = d;
        this.dibujar(d);
        this.firma = `${d.id}:${d.estado}:${d.vigencia}`;
      }
    } catch (e) { this.estado(e.message, true); }
    finally { this.consultando = false; }
  }
  simular() {
    return this.accion(async () => {
      this.estado('Preparando conexiones, modelos y mediciones…');
      const banco = this.el('banco').value;
      const proyecto = this.proyecto;
      const peticion = banco === 'proyecto' ? { ...this.contexto(), config: this.config() } : { ejemplo: banco };
      const r = await this.pedir({ op: 'preparar', ...peticion });
      if (proyecto !== this.proyecto) return;
      this.id = r.id; this.firma = '';
      await this.pedir({ op: 'ejecutar', id: r.id });
      if (proyecto !== this.proyecto) return;
      this.estado('Simulando en ngspice… puedes detener la ejecución.');
      await this.consultar();
    });
  }
  dibujar(d) {
    const caja = this.el('resultado'); caja.replaceChildren();
    const estados = { preparado: 'Listo para ejecutar', ejecutando: 'Simulando…', completado: 'Simulación completada',
      error: 'No se pudo verificar', cancelando: 'Deteniendo…', cancelado: 'Simulación cancelada',
      timeout: 'Tiempo límite agotado', interrumpido: 'Simulación interrumpida' };
    this.estado(`${estados[d.estado] || d.estado}${d.error ? ': ' + d.error : ''}`, !!d.error);
    const etiqueta = d.vigencia === 'ejemplo' ? 'EJEMPLO INDEPENDIENTE · no verifica el proyecto abierto'
      : d.vigencia === 'obsoleto' ? 'RESULTADO OBSOLETO · cambiaron las entradas; vuelve a simular' : 'VERSIÓN ACTUAL · circuito y modelos coinciden';
    caja.append(this.nodo('p', etiqueta, `sim-vigencia ${d.vigencia}`));
    const r = d.resultado;
    if (r) {
      const titulos = { cumple: 'Se cumplen los criterios definidos', no_cumple: 'Hay criterios que no se cumplen',
        no_verificado: 'Hay mediciones sin verificar', sin_criterios: 'Medido · sin criterios de aceptación' };
      caja.append(this.nodo('h2', `${titulos[r.criterios] || r.criterios}${d.vigencia === 'obsoleto' ? ' (versión anterior)' : ''}`));
      const grid = this.nodo('div', '', 'sim-medidas');
      for (const m of r.medidas) {
        const card = this.nodo('div', '', `sim-medida ${m.estado}`);
        card.append(this.nodo('small', m.id.replaceAll('_', ' ')), this.nodo('strong', `${this.numero(m.valor)} ${m.unidad || ''}`),
          this.nodo('span', `${m.estado.replaceAll('_', ' ')}${m.min !== undefined || m.max !== undefined ? ` · ${m.min ?? '−∞'} a ${m.max ?? '∞'}` : ''}`));
        grid.append(card);
      }
      caja.append(grid);
      const colores = ['#e8c080', '#86d8c4', '#a7bfff', '#efacbb'];
      r.series.forEach((s, i) => {
        const figura = this.nodo('figure', '', 'sim-grafica');
        const titulo = this.nodo('figcaption', `${s.id} · ${s.red || s.corriente} · ${s.unidad}${s.representacion === 'magnitud' ? ' (magnitud AC)' : ''}`);
        figura.append(titulo, this.grafica(s, r.eje, colores[i % colores.length]));
        const refs = this.nodo('div', '', 'sim-referencias');
        if (d.vigencia === 'vigente' && d.proyecto_id === this.proyecto) {
          for (const ref of s.referencias) {
            const b = this.nodo('button', ref); b.title = `Ver ${ref} en el esquemático`;
            b.onclick = () => this.seleccionar(ref); refs.append(b);
          }
        }
        figura.append(refs); caja.append(figura);
      });
      caja.append(this.nodo('p', `${r.puntos} puntos calculados · curvas reducidas conservando extremos · firma ${d.firma.slice(0, 12)}`, 'sim-pie'));
      const log = this.nodo('details'); log.append(this.nodo('summary', 'Informe del motor'), this.nodo('pre', `${r.motor}\n${r.log.join('\n')}`)); caja.append(log);
    }
    const limites = this.nodo('div', '', 'sim-limites');
    limites.append(this.nodo('h3', 'Alcance del ensayo'));
    for (const t of d.limitaciones || []) limites.append(this.nodo('p', t));
    for (const e of d.exclusiones || []) limites.append(this.nodo('p', `${e.ref} excluido: ${e.motivo}`));
    limites.append(this.nodo('p', 'Los resultados dependen del modelo y del banco de pruebas. ERC y DRC se comprueban por separado.'));
    caja.append(limites);
  }
  grafica(s, eje, color) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', '0 0 900 220');
    svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `Curva ${s.id} frente a ${eje.nombre}`);
    const datos = s.puntos;
    const transformar = x => eje.log ? Math.log10(x) : x;
    let xmin = transformar(datos[0][0]), xmax = transformar(datos.at(-1)[0]);
    if (xmin === xmax) xmax = xmin + 1;
    let ymin = s.min, ymax = s.max;
    const margen = Math.max((ymax - ymin) * 0.12, Math.abs(ymax) * 0.02, 0.001);
    ymin -= margen; ymax += margen;
    const x = v => 75 + (transformar(v) - xmin) / (xmax - xmin) * 790;
    const abscisas = datos.map(p => x(p[0]));
    const y = v => 175 - (v - ymin) / (ymax - ymin) * 150;
    const agregar = (tag, attrs, texto) => {
      const n = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      if (texto) n.textContent = texto;
      svg.append(n); return n;
    };
    for (let i = 0; i <= 4; i++) {
      const yy = 25 + i * 37.5;
      agregar('line', { x1: 75, x2: 865, y1: yy, y2: yy, stroke: '#ffffff18' });
      agregar('text', { x: 65, y: yy + 4, 'text-anchor': 'end' }, (ymax - i / 4 * (ymax - ymin)).toPrecision(3));
      const xx = xmin + i / 4 * (xmax - xmin);
      agregar('text', { x: 75 + i / 4 * 790, y: 202, 'text-anchor': 'middle' }, (eje.log ? 10 ** xx : xx).toPrecision(3));
    }
    agregar('text', { x: 865, y: 218, 'text-anchor': 'end' }, `${eje.nombre} (${eje.unidad || 'DC'})`);
    if (datos.length === 1) agregar('circle', { cx: x(datos[0][0]), cy: y(datos[0][1]), r: 5, fill: color });
    else agregar('polyline', { points: datos.map(p => `${x(p[0]).toFixed(2)},${y(p[1]).toFixed(2)}`).join(' '), fill: 'none', stroke: color, 'stroke-width': 2 });
    const cursor = agregar('line', { x1: 75, x2: 75, y1: 25, y2: 175, stroke: color, 'stroke-dasharray': '4 4', visibility: 'hidden' });
    const lectura = agregar('text', { x: 80, y: 16 }, '');
    svg.onpointermove = ev => {
      const punto = svg.createSVGPoint(); punto.x = ev.clientX; punto.y = ev.clientY;
      const local = punto.matrixTransform(svg.getScreenCTM().inverse());
      const indice = VistaSimulacion.masCercano(abscisas, local.x);
      const p = datos[indice]; cursor.setAttribute('x1', x(p[0])); cursor.setAttribute('x2', x(p[0])); cursor.setAttribute('visibility', 'visible');
      lectura.textContent = `${this.numero(p[0])} ${eje.unidad} · ${this.numero(p[1])} ${s.unidad}`;
    };
    svg.onpointerleave = () => { cursor.setAttribute('visibility', 'hidden'); lectura.textContent = ''; };
    return svg;
  }
}
