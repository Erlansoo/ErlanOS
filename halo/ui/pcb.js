'use strict';

// Dibuja la placa: tablero, cobre por capa (2 a 8 capas) con sus zonas (contorno, red, prioridad y
// relleno), pistas, pads, vías, serigrafía de ambas caras, áreas prohibidas y conexiones
// pendientes. Hereda desplazamiento, zoom y arrastre de la vista del esquemático: una huella se
// arrastra entera (pads, serigrafía y patio), se rota con R y se pasa a la otra cara con F.
const COLOR_CAPA = { F: '#d0603f', B: '#4f8fd6', In1: '#6fbf7f', In2: '#d6b24a', In3: '#b884d0', In4: '#5fc0c0', In5: '#d98cb3', In6: '#9fa6e0' };

class VistaPlaca extends VistaEsquematico {
  constructor(svg) {
    super(svg);
    this.minimo = [30, 20];  // las placas pequeñas se ven de cerca
    this.rejilla = 0.635;
    this.etapa = 'pcb';
    this.ocultas = new Set();
  }

  static sufijo(capa) { return String(capa).split('.')[0]; }

  nombreCapa(capa) {
    const propio = ((this.modelo && this.modelo.nombres_capa) || {})[capa];
    return propio ? `${capa} · ${propio}` : capa;
  }

  claveDe(nodo) { return nodo.dataset.ref; }

  nodosDe(objetivo) {
    return [...this.svg.querySelectorAll('[data-huella]')].filter(n => n.dataset.huella === objetivo.dataset.ref);
  }

  aplicarCapas() {
    for (const g of this.svg.querySelectorAll('[data-capa]')) g.classList.toggle('oculta', this.ocultas.has(g.dataset.capa));
    // Con una sola capa de cobre a la vista, su relleno se ve fuerte y lo demás se atenúa.
    const visibles = ((this.modelo && this.modelo.capas) || []).filter(c => !this.ocultas.has(c));
    this.svg.classList.toggle('solo', visibles.length === 1);
  }

  alternarCapa(capa) {
    if (this.ocultas.has(capa)) this.ocultas.delete(capa);
    else this.ocultas.add(capa);
    this.aplicarCapas();
  }

  soloCapa(capa) {
    const todas = (this.modelo && this.modelo.capas) || [];
    const yaSola = todas.every(c => c === capa || this.ocultas.has(c)) && !this.ocultas.has(capa);
    this.ocultas = new Set(yaSola ? [] : todas.filter(c => c !== capa));
    this.aplicarCapas();
  }

  dibujar(modelo) {
    const primera = !this.modelo || this.modelo.archivo !== modelo.archivo;
    this.modelo = modelo;
    const recientes = new Set(modelo.recientes || []);
    this.svg.replaceChildren();
    const defs = this.el('defs', {}, this.svg);
    const raiz = this.el('g', {}, this.svg);

    const borde = this.limitesContorno(modelo);
    if (borde) {
      const [x0, y0, x1, y1] = borde;
      this.el('rect', { x: x0, y: y0, width: x1 - x0, height: y1 - y0, class: 'tablero' }, raiz);
    }

    const cobre = modelo.capas && modelo.capas.length ? modelo.capas : ['F.Cu', 'B.Cu'];
    const rayado = (id, color) => {
      const patron = this.el('pattern', { id, width: 1.2, height: 1.2, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' }, defs);
      this.el('line', { x1: 0, y1: 0, x2: 0, y2: 1.2, stroke: color, 'stroke-width': 0.28, 'stroke-opacity': 0.6 }, patron);
    };
    rayado('rayado-prohibido', '#e0503c');
    const grupo = (clase, capa) => this.el('g', capa ? { class: `capa ${clase}`, 'data-capa': capa } : { class: `capa ${clase}` }, raiz);
    const zonas = {}, pistas = {}, contornos = {};
    for (const c of [...cobre].reverse()) {   // de la cara inferior a la superior
      const s = VistaPlaca.sufijo(c);
      rayado(`rayado-${s}`, COLOR_CAPA[s] || '#aaa');
      zonas[c] = grupo(`capa-zonas-${s}`, c);
      pistas[c] = grupo(`capa-pistas-${s}`, c);
    }
    const padsG = grupo('capa-pads'), viasG = grupo('capa-vias'), fab = grupo('capa-fab');
    const silk = grupo('capa-silk'), crt = grupo('capa-patio');
    for (const c of [...cobre].reverse()) contornos[c] = grupo(`capa-contornos-${VistaPlaca.sufijo(c)}`, c);
    const bordeG = grupo('capa-borde'), rats = grupo('capa-ratsnest');

    for (const z of modelo.zonas) {
      if (!zonas[z.capa]) continue;
      this.el('polygon', { points: z.pts.map(p => p.join(',')).join(' '), class: `zona zona-${VistaPlaca.sufijo(z.capa)}` }, zonas[z.capa]);
    }
    for (const a of modelo.areas || []) this.area(a, contornos);
    for (const t of modelo.pistas) {
      if (!pistas[t.capa]) continue;
      const clase = `pista pista-${VistaPlaca.sufijo(t.capa)}${recientes.has(t.uuid) ? ' reciente' : ''}`;
      if (t.d) this.el('path', { d: t.d, class: clase, 'stroke-width': t.w, fill: 'none' }, pistas[t.capa]);
      else this.el('line', { x1: t.a[0], y1: t.a[1], x2: t.b[0], y2: t.b[1], class: clase, 'stroke-width': t.w }, pistas[t.capa]);
    }
    const [bx0, by0, bx1, by1] = borde || [-Infinity, -Infinity, Infinity, Infinity];
    for (const h of modelo.huellas) {
      const clases = ['huella'];
      if (h.lado === 'B') clases.push('abajo');
      if (recientes.has(h.uuid)) clases.push('reciente');
      if (this.seleccion === h.ref) clases.push('seleccionado');
      if (this.refEnlazada === h.ref) clases.push('enlazado');
      if (h.pads.length && h.pads.every(p => p.p[0] < bx0 || p.p[0] > bx1 || p.p[1] < by0 || p.p[1] > by1)) clases.push('en-espera');
      const g = this.el('g', { class: clases.join(' '), 'data-ref': h.ref, 'data-uuid': h.uuid, 'data-huella': h.ref }, padsG);
      const titulo = this.el('title', {}, g);
      titulo.textContent = `${h.ref} ${h.valor}${h.lado === 'B' ? ' · cara inferior' : ''}`;
      const caja = this.cajaHuella(h);
      if (caja) this.el('rect', { x: caja[0], y: caja[1], width: caja[2] - caja[0], height: caja[3] - caja[1], class: 'zona-agarre' }, g);
      for (const p of h.pads) this.pad(p, g);
      const grupos = {};
      for (const prim of h.prims) {
        const tipo = prim.capa.endsWith('SilkS') ? 'silk' : prim.capa.endsWith('CrtYd') ? 'patio' : 'fab';
        const clase = prim.capa.startsWith('B.') ? `${tipo} ${tipo}-B` : tipo;
        grupos[clase] = grupos[clase] || this.el('g', { 'data-huella': h.ref }, { silk, patio: crt, fab }[tipo]);
        this.grafico(prim, grupos[clase], clase);
      }
    }
    for (const v of modelo.vias) {
      this.el('circle', { cx: v.p[0], cy: v.p[1], r: v.d / 2, class: 'via' }, viasG);
      this.el('circle', { cx: v.p[0], cy: v.p[1], r: v.taladro / 2, class: 'taladro' }, viasG);
    }
    for (const g of modelo.graficos) {
      if (g.capa.endsWith('SilkS')) this.grafico(g, silk, g.capa.startsWith('B.') ? 'silk silk-B' : 'silk');
    }
    for (const g of modelo.contorno) this.grafico(g, bordeG, 'borde');
    for (const r of modelo.ratsnest) {
      this.el('line', { x1: r.a[0], y1: r.a[1], x2: r.b[0], y2: r.b[1], class: 'ratsnest' }, rats);
    }
    this.aplicarCapas();

    if (primera) this.usuarioMovio = false;
    if (this.usuarioMovio) this.aplicarVista();
    else this.ajustar();
  }

  // Zona de cobre o área prohibida: contorno punteado del color de su capa, nombre de la red y,
  // si KiCad aún no la rellenó, un rayado para que se vea dónde irá el cobre.
  area(a, contornos) {
    for (const capa of a.capas) {
      const destino = contornos[capa];
      if (!destino || !a.contornos.length) continue;
      const s = VistaPlaca.sufijo(capa);
      const g = this.el('g', { class: a.regla ? 'area area-prohibida' : 'area' }, destino);
      for (const pts of a.contornos) {
        this.el('polygon', {
          points: pts.map(p => p.join(',')).join(' '),
          class: a.regla ? 'contorno-prohibido' : `contorno-zona contorno-${s}`,
          fill: a.regla ? 'url(#rayado-prohibido)' : a.rellena ? 'none' : `url(#rayado-${s})`,
        }, g);
      }
      const pts = a.contornos[0];
      const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
      const x0 = Math.min(...xs), y0 = Math.min(...ys), w = Math.max(...xs) - x0, h = Math.max(...ys) - y0;
      const tam = Math.max(0.8, Math.min(3, Math.min(w, h) / 14));
      const red = String(a.red || '').replace(/^\//, '');
      let texto;
      if (a.regla) {
        const prohibe = Object.entries(a.regla).filter(([, v]) => v === 'not_allowed').map(([k]) => ({ tracks: 'pistas', vias: 'vías', pads: 'pads', copperpour: 'cobre', footprints: 'huellas' }[k] || k));
        texto = `prohibido: ${prohibe.join(', ') || '—'}`;
      } else {
        texto = `${red}${a.prioridad ? ` · p${a.prioridad}` : ''}${a.rellena ? '' : ' · sin rellenar'}`;
      }
      const t = this.el('text', { x: x0 + tam * 0.5, y: y0 + tam * 1.3, 'font-size': tam, class: `etiqueta-zona${a.regla ? ' etiqueta-prohibida' : ''}` }, g);
      if (!a.regla) t.setAttribute('fill', COLOR_CAPA[s] || '#ddd');
      t.textContent = texto;
      const ayuda = this.el('title', {}, g);
      ayuda.textContent = `${a.nombre ? a.nombre + ' · ' : ''}${texto} · ${this.nombreCapa(capa)} · conexión ${a.conexion}${a.area_mm2 ? ` · ${a.area_mm2} mm²` : ''}`;
    }
  }

  cajaHuella(h) {
    const pts = [];
    for (const prim of h.prims) {
      if (!prim.capa.endsWith('CrtYd')) continue;
      if (prim.pts) pts.push(...prim.pts);
      if (prim.c) pts.push([prim.c[0] - prim.r, prim.c[1] - prim.r], [prim.c[0] + prim.r, prim.c[1] + prim.r]);
    }
    if (!pts.length) {
      for (const p of h.pads) {
        const r = Math.max(p.w, p.h) / 2;
        pts.push([p.p[0] - r, p.p[1] - r], [p.p[0] + r, p.p[1] + r]);
      }
    }
    if (!pts.length) return null;
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  limitesContorno(modelo) {
    const pts = [];
    for (const g of modelo.contorno) {
      if (g.pts) pts.push(...g.pts);
      if (g.c) pts.push([g.c[0] - g.r, g.c[1] - g.r], [g.c[0] + g.r, g.c[1] + g.r]);
    }
    if (!pts.length) return null;
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  pad(p, g) {
    const radio = p.forma === 'circle' ? p.w / 2 : p.forma === 'oval' ? Math.min(p.w, p.h) / 2 :
      p.forma === 'roundrect' ? Math.min(p.w, p.h) * p.rr : 0;
    const clase = p.capas.length >= 2 ? 'pad' : p.capas[0] === 'B.Cu' ? 'pad pad-B' : 'pad';
    this.el('rect', {
      x: -p.w / 2, y: -p.h / 2, width: p.w, height: p.h, rx: radio, ry: radio, class: clase,
      transform: `translate(${p.p[0]} ${p.p[1]}) rotate(${-p.ang})`,
    }, g);
    if (p.taladro > 0) this.el('circle', { cx: p.p[0], cy: p.p[1], r: p.taladro / 2, class: 'taladro' }, g);
  }

  grafico(prim, destino, clase) {
    const ancho = Math.max(prim.w || 0.1, 0.08);
    switch (prim.t) {
      case 'poly':
        this.el(prim.relleno ? 'polygon' : 'polyline', {
          points: prim.pts.map(q => q.join(',')).join(' '), class: prim.relleno ? `${clase} relleno` : clase,
          'stroke-width': ancho,
        }, destino);
        break;
      case 'circle':
        this.el('circle', { cx: prim.c[0], cy: prim.c[1], r: prim.r, class: clase, 'stroke-width': ancho }, destino);
        break;
      case 'path':
        this.el('path', { d: prim.d, class: clase, 'stroke-width': ancho }, destino);
        break;
      case 'text':
        this.texto({ p: prim.p, s: prim.s, size: prim.size * 0.95, ang: prim.ang, jh: 'center', jv: 'center', c: clase.includes('silk-B') ? 'silk-B' : 'silk' }, destino);
        break;
    }
  }

  ajustar() {
    if (!this.modelo) return;
    // Encuadre: la placa y también las huellas que esperan fuera de ella.
    const pts = [];
    const borde = this.limitesContorno(this.modelo);
    if (borde) pts.push([borde[0], borde[1]], [borde[2], borde[3]]);
    for (const h of this.modelo.huellas) for (const p of h.pads) pts.push(p.p);
    if (pts.length) {
      const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
      this.modelo = { ...this.modelo, limites: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] };
    }
    super.ajustar();
  }
}
