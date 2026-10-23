'use strict';

const REJILLA = 1.27;

// Dibuja la lista de dibujo que envía el servidor (mm, Y hacia abajo) en un SVG, con
// desplazamiento y zoom. Si la vista es editable, permite arrastrar símbolos, etiquetas,
// textos y módulos enteros (por el borde o el título del bloque): el cambio se muestra al
// instante y se confirma cuando el archivo real cambia.
class VistaEsquematico {
  constructor(svg) {
    this.svg = svg;
    this.modelo = null;
    this.vista = null;
    this.usuarioMovio = false;   // mientras el usuario no mueva el lienzo, la vista sigue al trabajo
    this.editable = false;
    this.onEditar = null;        // callback({op, clave, campo, dx, dy, etapa})
    this.onSeleccion = null;
    this.onBloque = null;
    this.seleccion = null;
    this.rejilla = REJILLA;
    if (!this.svg.hasAttribute('tabindex')) this.svg.tabIndex = -1;
    this.etapa = 'esquematico';
    this._interaccion();
  }

  el(tag, attrs, padre) {
    const nodo = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs || {})) nodo.setAttribute(k, v);
    if (padre) padre.appendChild(nodo);
    return nodo;
  }

  claveDe(nodo) { return nodo.dataset.uuid; }

  nodosDe(objetivo) { return [objetivo]; }

  dibujar(modelo) {
    const primera = !this.modelo || this.modelo.archivo !== modelo.archivo;
    this.modelo = modelo;
    const recientes = new Set(modelo.recientes || []);
    this.svg.replaceChildren();
    const [, pw, ph] = modelo.papel;
    const raiz = this.el('g', {}, this.svg);
    this.el('rect', { x: 0, y: 0, width: pw, height: ph, class: 'papel' }, raiz);
    this.el('rect', { x: 5, y: 5, width: pw - 10, height: ph - 10, class: 'marco' }, raiz);

    // Cada elemento que cae dentro de un bloque lleva su índice: al arrastrar el bloque se mueve con él.
    const bloques = modelo.bloques || [];
    const cajas = bloques.map(b => [Math.min(b.a[0], b.b[0]), Math.min(b.a[1], b.b[1]), Math.max(b.a[0], b.b[0]), Math.max(b.a[1], b.b[1])]);
    const bloqueDe = (x, y) => cajas.findIndex(([x0, y0, x1, y1]) => x0 < x && x < x1 && y0 < y && y < y1);
    const marcar = (nodo, i) => { if (nodo && i >= 0) nodo.dataset.enBloque = i; return nodo; };

    bloques.forEach((b, i) => {
      const [x0, y0, x1, y1] = cajas[i];
      const g = this.el('g', { class: 'grupo-bloque', 'data-bloque': b.uuid, 'data-indice': i }, raiz);
      g.setAttribute('tabindex', '0');
      g.setAttribute('role', 'button');
      g.setAttribute('aria-label', `Abrir ficha: ${b.titulo || 'Bloque'}`);
      if (b.virtual) g.dataset.virtual = 'true';
      const ayuda = this.el('title', {}, g);
      ayuda.textContent = `${b.titulo || 'Bloque'} · doble clic o Enter para abrir su ficha${b.virtual ? '' : ' · arrastra el borde para moverlo'}`;
      this.el('rect', { x: x0, y: y0, width: x1 - x0, height: y1 - y0, class: b.discontinuo ? 'bloque discontinuo' : 'bloque' }, g);
      this.el('rect', { x: x0, y: y0, width: x1 - x0, height: y1 - y0, class: 'agarre-bloque' }, g);
      if (b.titulo) {
        const t = this.texto({ p: [x0, y0 - 1.27], s: b.titulo, size: b.titulo_size || 2, jh: 'left', jv: 'bottom', c: 'nota' }, g);
        t.classList.add('negrita', 'titulo-bloque');
      }
    });
    for (const c of modelo.cables) {
      const clases = ['cable'];
      if (recientes.has(c.uuid)) clases.push('reciente');
      if (this.seleccion === c.uuid) clases.push('seleccionado');
      const puntos = c.pts.map(p => p.join(',')).join(' ');
      const nodo = this.el('polyline', { points: puntos, class: clases.join(' '), 'data-cable': c.uuid }, raiz);
      // Área de agarre invisible y más ancha: la línea es muy fina para tomarla con el puntero.
      if (this.editable) this.el('polyline', { points: puntos, class: 'agarre-cable', 'data-cable': c.uuid }, raiz);
      const i = c.pts.length ? bloqueDe(c.pts[0][0], c.pts[0][1]) : -1;
      marcar(nodo, i >= 0 && c.pts.every(p => bloqueDe(p[0], p[1]) === i) ? i : -1);
    }
    for (const b of modelo.buses) {
      this.el('polyline', { points: b.pts.map(p => p.join(',')).join(' '), class: 'bus' }, raiz);
    }
    for (const s of modelo.simbolos) {
      const clases = ['simbolo'];
      if (recientes.has(s.uuid)) clases.push('reciente');
      if (this.seleccion === s.uuid) clases.push('seleccionado');
      if (this.refEnlazada === s.ref) clases.push('enlazado');
      const g = this.el('g', { class: clases.join(' '), 'data-uuid': s.uuid, 'data-ref': s.ref }, raiz);
      const titulo = this.el('title', {}, g);
      titulo.textContent = `${s.ref} ${s.valor} (${s.lib_id})`;
      const i = s.bbox ? bloqueDe((s.bbox[0] + s.bbox[2]) / 2, (s.bbox[1] + s.bbox[3]) / 2) : -1;
      marcar(g, i);
      if (s.bbox) {
        const [x0, y0, x1, y1] = s.bbox;
        this.el('rect', { x: x0 - 0.6, y: y0 - 0.6, width: x1 - x0 + 1.2, height: y1 - y0 + 1.2, class: 'zona-agarre' }, g);
      }
      for (const p of s.prims) {
        if (p.t === 'text' && p.campo) {
          const t = this.texto(p, raiz);
          t.dataset.campo = p.campo;
          t.dataset.uuid = p.uuid;
          t.classList.add('campo');
          marcar(t, i);
        } else {
          this.primitiva(p, g);
        }
      }
    }
    for (const [x, y] of modelo.uniones) marcar(this.el('circle', { cx: x, cy: y, r: 0.46, class: 'union' }, raiz), bloqueDe(x, y));
    for (const [x, y] of modelo.sin_conexion) {
      const d = 0.63;
      marcar(this.el('path', { d: `M${x - d} ${y - d} L${x + d} ${y + d} M${x - d} ${y + d} L${x + d} ${y - d}`, class: 'nc' }, raiz), bloqueDe(x, y));
    }
    for (const e of modelo.etiquetas) marcar(this.etiqueta(e, raiz), bloqueDe(e.p[0], e.p[1]));
    for (const t of modelo.textos) {
      const nodo = this.texto(t, raiz);
      if (t.negrita) nodo.classList.add('negrita');
      marcar(nodo, bloqueDe(t.p[0], t.p[1]));
    }

    if (primera) this.usuarioMovio = false;
    if (this.usuarioMovio) this.aplicarVista();
    else this.ajustar();
    // Si lo seleccionado ya no existe (lo borró la IA, cambió de archivo), el panel no debe seguir nombrándolo.
    if (this.seleccion && !this.svg.querySelector('.seleccionado')) this.seleccionar(null);
    if (this.cableado) this._previa(this.cableado.cursor);
  }

  etiqueta(e, raiz) {
    const g = this.el('g', { class: `etiqueta etiqueta-${e.tipo}${this.seleccion === (e.uuid || e.texto) ? ' seleccionado' : ''}`, 'data-etiqueta': e.uuid || e.texto }, raiz);
    const size = e.size || 1.27;
    if (e.tipo !== 'global') {
      // Como KiCad: 180° y 270° no ponen el texto patas arriba, lo llevan al otro lado del anclaje (la justificación lo hace).
      this.texto({ p: e.p, s: e.texto, size, ang: e.ang % 180, jh: e.jh, jv: e.jv, c: 'etiqueta' }, g);
      this.el('circle', { cx: e.p[0], cy: e.p[1], r: 0.3, class: 'ancla' }, g);
      return g;
    }
    // Etiqueta global: caja con punta según su forma (entrada, salida, bidireccional).
    const h = size * 1.6, punta = h / 2, margen = size * 0.45;
    const medida = this.el('text', { x: 0, y: 0, 'font-size': size * 1.28, class: 't-etiqueta' }, this.svg);
    medida.textContent = e.texto;
    const ancho = medida.getComputedTextLength ? medida.getComputedTextLength() : e.texto.length * size;
    medida.remove();
    const entra = e.forma === 'input' || e.forma === 'bidirectional' || e.forma === 'tri_state';
    const sale = e.forma === 'output' || e.forma === 'bidirectional' || e.forma === 'tri_state';
    const x0 = 0, x1 = (entra ? punta : margen) + ancho + (sale ? punta : margen) + margen;
    const pts = [];
    pts.push(entra ? [x0, 0] : [x0, -h / 2]);
    if (entra) pts.push([punta, -h / 2]);
    pts.push(sale ? [x1 - punta, -h / 2] : [x1, -h / 2]);
    if (sale) pts.push([x1, 0]);
    pts.push(sale ? [x1 - punta, h / 2] : [x1, h / 2]);
    if (entra) pts.push([punta, h / 2]);
    else pts.push([x0, h / 2]);
    const izquierda = e.ang === 180 || e.ang === 270;
    const giro = e.ang === 90 ? -90 : e.ang === 270 ? 90 : 0;
    const local = this.el('g', { transform: `translate(${e.p[0]} ${e.p[1]}) rotate(${giro}) scale(${izquierda && giro === 0 ? -1 : 1} 1)` }, g);
    this.el('polygon', { points: pts.map(p => p.join(',')).join(' '), class: 'caja-global' }, local);
    const texto = this.el('text', {
      x: (entra ? punta : margen) + margen * 0.5 + ancho / 2, y: 0, 'font-size': size * 1.28,
      'text-anchor': 'middle', 'dominant-baseline': 'central', class: 't-etiqueta',
    }, local);
    if (izquierda && giro === 0) texto.setAttribute('transform', `scale(-1 1) translate(${-2 * ((entra ? punta : margen) + margen * 0.5 + ancho / 2)} 0)`);
    texto.textContent = e.texto;
    return g;
  }

  primitiva(p, g) {
    const ancho = Math.max(p.w || 0, 0.1524);
    const relleno = p.fill === 'background' ? 'relleno-background' : p.fill === 'outline' ? 'relleno-outline' : '';
    switch (p.t) {
      case 'poly': {
        const tag = p.closed || relleno ? 'polygon' : 'polyline';
        this.el(tag, { points: p.pts.map(q => q.join(',')).join(' '), class: `cuerpo ${relleno}`, 'stroke-width': ancho }, g);
        break;
      }
      case 'circle':
        this.el('circle', { cx: p.c[0], cy: p.c[1], r: p.r, class: `cuerpo ${relleno}`, 'stroke-width': ancho }, g);
        break;
      case 'path':
        this.el('path', { d: p.d, class: `cuerpo ${relleno}`, 'stroke-width': ancho }, g);
        break;
      case 'pin':
        this.el('line', { x1: p.a[0], y1: p.a[1], x2: p.b[0], y2: p.b[1], class: 'pin' }, g);
        if (p.forma === 'inverted' || p.forma === 'inverted_clock') {
          const dx = p.b[0] - p.a[0], dy = p.b[1] - p.a[1];
          const n = Math.hypot(dx, dy) || 1;
          this.el('circle', { cx: p.b[0] - dx / n * 0.5, cy: p.b[1] - dy / n * 0.5, r: 0.5, class: 'cuerpo', 'stroke-width': 0.1524 }, g);
        }
        break;
      case 'text':
        this.texto(p, g);
        break;
    }
  }

  texto(p, padre) {
    const anclas = { left: 'start', right: 'end', center: 'middle' };
    const bases = { top: 'hanging', bottom: 'alphabetic', center: 'central' };
    const t = this.el('text', {
      x: p.p[0], y: p.p[1],
      'font-size': (p.size || 1.27) * 1.28,
      'text-anchor': anclas[p.jh] || 'middle',
      'dominant-baseline': bases[p.jv] || 'central',
      class: `t-${p.c || 'nota'}`,
    }, padre);
    if (p.ang) t.setAttribute('transform', `rotate(${-p.ang} ${p.p[0]} ${p.p[1]})`);
    t.textContent = p.s;
    return t;
  }

  ajustar() {
    if (!this.modelo) return;
    const [x0, y0, x1, y1] = this.modelo.limites;
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const [minW, minH] = this.minimo || [110, 75];
    const w = Math.max(x1 - x0, minW), h = Math.max(y1 - y0, minH);
    const margen = Math.max(w, h) * 0.12;
    const r = this.svg.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    // La bitácora flota abajo a la izquierda: se reserva ese alto para no tapar el trabajo.
    const b = document.getElementById('bitacora');
    const tapa = b ? Math.min(0.45, Math.max(0, r.bottom - b.getBoundingClientRect().top + 12) / Math.max(1, r.height)) : 0;
    const alto = (h + 2 * margen) / (1 - tapa);
    this.vista = { x: cx - w / 2 - margen, y: cy - h / 2 - margen, w: w + 2 * margen, h: alto };
    this.aplicarVista();
  }

  aplicarVista() {
    if (!this.vista) return;
    const r = this.svg.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;  // vista oculta: no se calcula con tamaño cero
    const aspecto = r.width / Math.max(1, r.height);
    let { x, y, w, h } = this.vista;
    if (w / h < aspecto) { const nw = h * aspecto; x -= (nw - w) / 2; w = nw; }
    else { const nh = w / aspecto; y -= (nh - h) / 2; h = nh; }
    this.vista = { x, y, w, h };
    this.svg.setAttribute('viewBox', `${x} ${y} ${w} ${h}`);
  }

  punto(ev) {
    const pt = this.svg.createSVGPoint();
    pt.x = ev.clientX;
    pt.y = ev.clientY;
    const m = this.svg.getScreenCTM();
    return m ? pt.matrixTransform(m.inverse()) : { x: 0, y: 0 };
  }

  seleccionar(clave) {
    this.seleccion = clave;
    for (const g of this.svg.querySelectorAll('[data-ref]')) g.classList.toggle('seleccionado', this.claveDe(g) === clave);
    for (const c of this.svg.querySelectorAll('[data-cable]')) c.classList.toggle('seleccionado', c.dataset.cable === clave);
    for (const e of this.svg.querySelectorAll('[data-etiqueta]')) e.classList.toggle('seleccionado', e.dataset.etiqueta === clave);
    if (this.onSeleccion) this.onSeleccion(clave);
  }

  // Lleva la vista a un punto de la hoja (mm) y selecciona lo que haya con ese uuid: para ir a un error del ERC.
  enfocar(pos, uuid) {
    if (pos && this.vista) {
      const w = Math.min(this.vista.w, 90), h = w * (this.vista.h / this.vista.w);
      this.vista = { x: pos.x - w / 2, y: pos.y - h / 2, w, h };
      this.usuarioMovio = true; this.aplicarVista();
    }
    if (uuid && this.svg.querySelector(`[data-uuid="${uuid}"], [data-etiqueta="${uuid}"], [data-cable="${uuid}"]`)) this.seleccionar(uuid);
  }

  etiquetaSeleccionada() {
    return this.seleccion ? this.modelo?.etiquetas?.find(e => (e.uuid || e.texto) === this.seleccion) || null : null;
  }

  // ---- previa en vivo: lo conectado sigue al gesto, como en KiCad; el motor decide al soltar
  _igual(p, q) { return Math.abs(p[0] - q[0]) < 0.01 && Math.abs(p[1] - q[1]) < 0.01; }

  _puntas(uuid) {   // puntas de los pines de un símbolo
    const s = this.modelo?.simbolos?.find(x => x.uuid === uuid);
    return s ? s.prims.filter(q => q.t === 'pin').map(q => q.a) : [];
  }

  _extremosEn(punto, salvo) {   // cables con un extremo exactamente en el punto
    const r = [];
    for (const c of this.modelo?.cables || []) {
      if (c.uuid === salvo || c.pts.length < 2) continue;
      if (this._igual(c.pts[0], punto)) r.push({ uuid: c.uuid, i: 0, pts: c.pts });
      else if (this._igual(c.pts[c.pts.length - 1], punto)) r.push({ uuid: c.uuid, i: c.pts.length - 1, pts: c.pts });
    }
    return r;
  }

  _goma(puntos) {   // goma elástica: los extremos de cable que están en esos puntos siguen al arrastre
    const gomas = [];
    for (const q of puntos) for (const x of this._extremosEn(q)) gomas.push({ ...x, base: x.pts.map(v => v.slice()) });
    return { gomas, estirados: [], codos: [] };
  }

  _interior(q, a, b) {
    if (Math.abs(a[0] - b[0]) < 0.01 && Math.abs(q[0] - a[0]) < 0.01) return Math.min(a[1], b[1]) + 0.01 < q[1] && q[1] < Math.max(a[1], b[1]) - 0.01;
    if (Math.abs(a[1] - b[1]) < 0.01 && Math.abs(q[1] - a[1]) < 0.01) return Math.min(a[0], b[0]) + 0.01 < q[0] && q[0] < Math.max(a[0], b[0]) - 0.01;
    return false;
  }

  _ocupado(q, salvo) {   // pin, unión, etiqueta o interior de otro cable en el punto: la misma regla que el motor
    const m = this.modelo;
    if ((m.simbolos || []).some(s => s.prims.some(x => x.t === 'pin' && this._igual(x.a, q)))) return true;
    if ((m.uniones || []).some(u => this._igual(u, q)) || (m.etiquetas || []).some(e => this._igual(e.p, q))) return true;
    return (m.cables || []).some(c => c.uuid !== salvo && c.pts.length === 2 && this._interior(q, c.pts[0], c.pts[1]));
  }

  _planCable(uuid) {   // al jalar un tramo: el vecino en escuadra se estira; si hay pin, unión o varios, queda un codo
    const c = this.modelo.cables.find(x => x.uuid === uuid);
    const plan = { gomas: [], estirados: [], codos: [] };
    if (!c || c.pts.length !== 2) return plan;
    const horizontal = Math.abs(c.pts[0][1] - c.pts[1][1]) < 0.01;
    for (const q of c.pts) {
      const vecinos = this._extremosEn(q, uuid);
      const escuadra = vecinos.filter(v => (Math.abs(v.pts[0][1] - v.pts[v.pts.length - 1][1]) < 0.01) !== horizontal);
      const ocupado = this._ocupado(q, uuid);
      if (vecinos.length === 1 && escuadra.length === 1 && !ocupado) {
        plan.estirados.push({ ...escuadra[0], base: escuadra[0].pts.map(v => v.slice()) });
      } else if (vecinos.length || ocupado) {
        plan.codos.push({ viejo: q.slice(), nodo: this.el('polyline', { class: 'cable previa', points: '' }, this.svg) });
      }
    }
    return plan;
  }

  _ptsDe(uuid, pts) {
    const puntos = pts.map(q => q.join(',')).join(' ');
    for (const n of this.svg.querySelectorAll(`[data-cable="${uuid}"]`)) n.setAttribute('points', puntos);
  }

  _aplicarVivo(vivo, dx, dy) {
    if (!vivo) return;
    for (const g of [...vivo.gomas, ...vivo.estirados]) {
      const pts = g.base.map(q => q.slice());
      pts[g.i] = [+(g.base[g.i][0] + dx).toFixed(4), +(g.base[g.i][1] + dy).toFixed(4)];
      this._ptsDe(g.uuid, pts);
      for (const n of this.svg.querySelectorAll(`[data-cable="${g.uuid}"]`)) n.classList.add('goma');
    }
    for (const k of vivo.codos) k.nodo.setAttribute('points', `${k.viejo.join(',')} ${k.viejo[0] + dx},${k.viejo[1] + dy}`);
  }

  _deshacerVivo(vivo) {
    if (!vivo) return;
    for (const g of [...vivo.gomas, ...vivo.estirados]) {
      this._ptsDe(g.uuid, g.base);
      for (const n of this.svg.querySelectorAll(`[data-cable="${g.uuid}"]`)) n.classList.remove('goma');
    }
    for (const k of vivo.codos) k.nodo.remove();
  }

  cableSeleccionado() { return !!(this.seleccion && this.svg.querySelector(`[data-cable="${this.seleccion}"]`)); }

  // ---- cablear a mano, como en KiCad: clic en cada esquina, doble clic o Enter termina, Escape cancela
  alRejilla(v) { return +(Math.round(v / this.rejilla) * this.rejilla).toFixed(4); }

  iniciarCableado() {
    if (!this.editable || this.cableado) return;
    this.seleccionar(null);
    this.cableado = { puntos: [], cursor: null };
    this.svg.classList.add('cableando');
    if (this.onCableado) this.onCableado(true);
  }

  cancelarCableado() {
    if (!this.cableado) return;
    this.cableado = null;
    this.svg.classList.remove('cableando');
    this.svg.querySelector('.cable.previa')?.remove();
    if (this.onCableado) this.onCableado(false);
  }

  // Un tramo nuevo va en escuadra: primero horizontal y luego vertical, salvo que ya esté alineado.
  _escuadra(q, p) {
    if (!q || q[0] === p[0] || q[1] === p[1]) return [p];
    return [[p[0], q[1]], p];
  }

  _previa(cursor) {
    this.svg.querySelector('.cable.previa')?.remove();
    if (!this.cableado) return;
    const ultimo = this.cableado.puntos[this.cableado.puntos.length - 1];
    const pts = [...this.cableado.puntos, ...(cursor && ultimo ? this._escuadra(ultimo, cursor) : [])];
    if (pts.length < 2) return;
    this.el('polyline', { points: pts.map(p => p.join(',')).join(' '), class: 'cable previa' }, this.svg);
  }

  _puntoCableado(ev) {
    const p = this.punto(ev);
    return [this.alRejilla(p.x), this.alRejilla(p.y)];
  }

  agregarPunto(p) {
    const puntos = this.cableado.puntos, ultimo = puntos[puntos.length - 1];
    if (ultimo && ultimo[0] === p[0] && ultimo[1] === p[1]) return;
    puntos.push(...this._escuadra(ultimo, p));
    this._previa(null);
  }

  terminarCableado() {
    if (!this.cableado) return;
    const puntos = this.cableado.puntos.slice();
    this.cancelarCableado();
    if (puntos.length >= 2 && this.onEditar) {
      this.onEditar({ op: 'trazar_cable', puntos, etapa: this.etapa, expected_revision: this.revision, proyecto_id: this.proyectoId })
        .catch(() => {});
    }
  }

  enfocarBloque(uuid) {
    const b = this.modelo?.bloques?.find(b => b.uuid === uuid);
    if (!b) return;
    const r = this.svg.getBoundingClientRect();
    const ancho = Math.abs(b.a[0] - b.b[0]) + 16, alto = Math.abs(b.a[1] - b.b[1]) + 16;
    const aspecto = Math.max(r.width, 1) / Math.max(r.height, 1);
    const w = Math.max(ancho, alto * aspecto), h = Math.max(alto, ancho / aspecto);
    this.vista = { x: (b.a[0] + b.b[0]) / 2 - w / 2, y: (b.a[1] + b.b[1]) / 2 - h / 2 - 2, w, h };
    this.usuarioMovio = true; this.aplicarVista();
  }

  _interaccion() {
    let gesto = null;
    this.svg.addEventListener('dblclick', ev => {
      // Los componentes, cables y etiquetas mantienen su propia interacción.
      if (this.cableado) { ev.preventDefault(); this.terminarCableado(); return; }
      const bloque = ev.target.closest('[data-bloque]');
      if (bloque && this.onBloque) { ev.preventDefault(); this.onBloque(bloque.dataset.bloque); }
    });
    this.svg.addEventListener('keydown', ev => {
      const bloque = ev.target.closest('[data-bloque]');
      if (bloque && this.onBloque && (ev.key === 'Enter' || ev.key === ' ')) {
        ev.preventDefault(); ev.stopPropagation(); this.onBloque(bloque.dataset.bloque);
      }
    });
    this.svg.addEventListener('wheel', ev => {
      if (!this.vista) return;
      ev.preventDefault();
      this.usuarioMovio = true;
      const r = this.svg.getBoundingClientRect();
      const fx = (ev.clientX - r.left) / r.width, fy = (ev.clientY - r.top) / r.height;
      const k = Math.exp(ev.deltaY * 0.0015);
      const { x, y, w, h } = this.vista;
      const nw = w * k, nh = h * k;
      this.vista = { x: x + (w - nw) * fx, y: y + (h - nh) * fy, w: nw, h: nh };
      this.svg.setAttribute('viewBox', `${this.vista.x} ${this.vista.y} ${nw} ${nh}`);
    }, { passive: false });

    this.svg.addEventListener('contextmenu', ev => ev.preventDefault());   // el botón derecho panea, no abre menú

    this.svg.addEventListener('pointerdown', ev => {
      if (!this.vista) return;
      if (ev.button === 2 || ev.button === 1) {            // derecho o central: panear, como en KiCad
        ev.preventDefault();
        gesto = { tipo: 'pan', x: ev.clientX, y: ev.clientY, v: { ...this.vista } };
        try { this.svg.setPointerCapture(ev.pointerId); } catch { /* punteros sintéticos */ }
        this.svg.classList.add('arrastrando');
        return;
      }
      if (ev.button !== 0) return;
      // Un clic en la hoja se lleva el foco: así Enter, Supr y Escape van al lienzo y no al último botón pulsado.
      if (!ev.target.closest('[data-bloque]')) this.svg.focus({ preventScroll: true });
      if (this.cableado) { ev.preventDefault(); this.agregarPunto(this._puntoCableado(ev)); return; }
      let objetivo = this.editable ? ev.target.closest('[data-campo], [data-etiqueta], [data-ref], [data-bloque], [data-cable]') : null;
      if (objetivo?.dataset.virtual) objetivo = null;
      if (!objetivo) { gesto = { tipo: 'vacio', x: ev.clientX, y: ev.clientY }; return; }   // izquierdo en vacío: sólo deselecciona
      const inicio = this.punto(ev);
      let op = 'mover', clave = this.claveDe(objetivo), campo = null, nodos, eje = null, vivo = null;
      if (objetivo.dataset.campo) {
        op = 'mover_campo'; campo = objetivo.dataset.campo; clave = objetivo.dataset.uuid; nodos = [objetivo];
      } else if (objetivo.dataset.etiqueta) {
        op = 'mover_etiqueta'; clave = objetivo.dataset.etiqueta; nodos = [objetivo];
        const e = this.modelo.etiquetas.find(x => (x.uuid || x.texto) === clave);
        vivo = e ? this._goma([e.p]) : null;                      // su cable la sigue
      } else if (objetivo.dataset.bloque) {
        op = 'mover_bloque'; clave = objetivo.dataset.bloque;
        nodos = [objetivo, ...this.svg.querySelectorAll(`[data-en-bloque="${objetivo.dataset.indice}"]`)];
      } else if (objetivo.dataset.cable) {
        // Un tramo se jala perpendicular a sí mismo, como en KiCad; sus vecinos lo siguen en la previa.
        op = 'mover_cable'; clave = objetivo.dataset.cable;
        nodos = [...this.svg.querySelectorAll(`[data-cable="${clave}"]`)];
        const pts = objetivo.getAttribute('points').split(' ').map(s => s.split(',').map(Number));
        eje = pts.length > 1 && pts[0][1] === pts[pts.length - 1][1] ? 'y' : 'x';
        vivo = this._planCable(clave);
        // Las etiquetas apoyadas en medio del tramo viajan con él, como hará el motor.
        const c = this.modelo.cables.find(x => x.uuid === clave);
        if (c && c.pts.length === 2) {
          for (const e of this.modelo.etiquetas) {
            if (this._interior(e.p, c.pts[0], c.pts[1])) {
              const n = this.svg.querySelector(`[data-etiqueta="${e.uuid || e.texto}"]`);
              if (n) nodos.push(n);
            }
          }
        }
      } else {
        nodos = this.nodosDe(objetivo);
        vivo = this._goma(this._puntas(clave));                    // los cables de sus pines lo siguen
      }
      gesto = { tipo: 'editar', op, clave, campo, eje, vivo, inicio, dx: 0, dy: 0, revision: this.revision, proyectoId: this.proyectoId,
        nodos: nodos.map(n => ({ n, base: n.getAttribute('transform') || '' })) };
      try { this.svg.setPointerCapture(ev.pointerId); } catch { /* punteros sintéticos */ }
      this.svg.classList.add('arrastrando');
    });

    this.svg.addEventListener('pointermove', ev => {
      if (this.cableado) { this.cableado.cursor = this._puntoCableado(ev); this._previa(this.cableado.cursor); return; }
      if (!gesto || gesto.tipo === 'vacio') return;
      if (gesto.tipo === 'pan') {
        this.usuarioMovio = true;
        const r = this.svg.getBoundingClientRect();
        const dx = (ev.clientX - gesto.x) / r.width * gesto.v.w;
        const dy = (ev.clientY - gesto.y) / r.height * gesto.v.h;
        this.vista = { ...gesto.v, x: gesto.v.x - dx, y: gesto.v.y - dy };
        this.svg.setAttribute('viewBox', `${this.vista.x} ${this.vista.y} ${this.vista.w} ${this.vista.h}`);
        return;
      }
      const p = this.punto(ev);
      const paso = gesto.op === 'mover_campo' ? this.rejilla / 2 : this.rejilla;
      gesto.dx = Math.round((p.x - gesto.inicio.x) / paso) * paso;
      gesto.dy = Math.round((p.y - gesto.inicio.y) / paso) * paso;
      if (gesto.eje === 'y') gesto.dx = 0;
      if (gesto.eje === 'x') gesto.dy = 0;
      for (const { n, base } of gesto.nodos) {
        n.setAttribute('transform', `translate(${gesto.dx} ${gesto.dy}) ${base}`);
        n.classList.add('en-arrastre');
      }
      this._aplicarVivo(gesto.vivo, gesto.dx, gesto.dy);
    });

    const soltar = ev => {
      if (gesto && gesto.tipo === 'editar') {
        for (const { n } of gesto.nodos) n.classList.remove('en-arrastre');
        if (gesto.dx || gesto.dy) {
          if (this.onEditar) {
            const peticion = { op: gesto.op, clave: gesto.clave, dx: +gesto.dx.toFixed(4), dy: +gesto.dy.toFixed(4), etapa: this.etapa,
                               expected_revision: gesto.revision, proyecto_id: gesto.proyectoId };
            if (gesto.campo) peticion.campo = gesto.campo;
            const { nodos, vivo } = gesto;
            // La previa se queda hasta que llega el redibujo; si el motor se niega, todo vuelve a su sitio.
            this.onEditar(peticion).catch(() => { for (const { n, base } of nodos) n.setAttribute('transform', base); this._deshacerVivo(vivo); });
          }
        } else {
          this._deshacerVivo(gesto.vivo);
          // Un clic selecciona lo tocado (y suelta lo anterior); en un campo o el borde de un bloque sólo suelta.
          this.seleccionar(gesto.op === 'mover_campo' || gesto.op === 'mover_bloque' ? null : gesto.clave);
        }
      } else if (gesto && gesto.tipo === 'vacio' && ev && Math.abs(ev.clientX - gesto.x) + Math.abs(ev.clientY - gesto.y) < 3) {
        this.seleccionar(null);            // clic en el vacío: nada seleccionado
      }
      gesto = null;
      this.svg.classList.remove('arrastrando');
    };
    // Escape a medio arrastre lo cancela, como en KiCad: todo vuelve a donde estaba y no se envía nada.
    window.addEventListener('keydown', ev => {
      if (ev.key !== 'Escape' || !gesto || gesto.tipo !== 'editar') return;
      for (const { n, base } of gesto.nodos) { n.setAttribute('transform', base); n.classList.remove('en-arrastre'); }
      this._deshacerVivo(gesto.vivo);
      gesto = null;
      this.svg.classList.remove('arrastrando');
    });
    this.svg.addEventListener('pointerup', soltar);
    this.svg.addEventListener('pointercancel', soltar);
    window.addEventListener('resize', () => this.aplicarVista());
  }
}
