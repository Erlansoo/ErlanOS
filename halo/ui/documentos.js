'use strict';

// Documentación del proyecto: memoria (Markdown) e informe (LaTeX → PDF) en <carpeta>/docs, guía de etapas
// y secciones generadas que avisan cuando su fuente cambió. Se refresca por latido «documentos» y por
// revisión del proyecto, nunca por temporizador.
class VistaDocumentos {
  constructor({ pedir }) {
    this.pedir = pedir;
    this.proyecto = null; this.revision = null; this.visible = false;
    this.datos = null; this.doc = null; this.editando = false; this.verPdfDe = null;
    this.nombre = new URLSearchParams(location.search).get('documento');   // enlace directo: ?pestana=documentos&documento=memoria.md
    this.cargando = false; this.pendiente = false;
    this.el = id => document.getElementById('doc-' + id);
    this.el('memoria').onclick = () => this.orden({ op: 'crear', plantilla: 'memoria' }, r => { this.nombre = r.nombre; this.verPdfDe = null; });
    this.el('informe').onclick = () => this.orden({ op: 'crear', plantilla: 'informe' }, r => { this.nombre = r.nombre; this.verPdfDe = null; });
    this.el('actualizar').onclick = () => this.regenerar(true);
    this.alVuelo = localStorage.getItem('halo-doc-al-vuelo') === 'si';
    this.el('solo').checked = this.alVuelo;
    this.el('solo').onchange = ev => {
      this.alVuelo = ev.target.checked;
      localStorage.setItem('halo-doc-al-vuelo', this.alVuelo ? 'si' : 'no');
      if (this.alVuelo) this.regenerar(false);
    };
    this.el('compilar').onclick = () => this.orden({ op: 'compilar', nombre: this.nombre }, r => { this.estado(`PDF compilado en ${r.segundos} s (${r.avisos} avisos de LaTeX).`); this.verPdfDe = r.pdf; });
    this.el('docx').onclick = () => this.orden({ op: 'convertir', nombre: this.nombre, formato: 'docx' }, r => this.estado(`Guardado ${r.archivo} en la carpeta docs del proyecto.`));
    this.el('verpdf').onclick = () => { this.verPdfDe = this.verPdfDe ? null : (this.doc && this.doc.pdf); this.dibujar(); };
    this.el('editar').onclick = () => { this.editando = !this.editando; if (this.editando) this.el('editor').value = this.doc.texto; this.dibujar(); };
    this.el('guardar').onclick = () => this.guardar();
    window.addEventListener('halo-latido', ev => { if ((ev.detail || []).includes('documentos') && this.visible) this.cargar(); });
  }

  actualizar(actual, visible) {
    const proyecto = actual.proyecto_id || null;
    const cambioProyecto = proyecto !== this.proyecto;
    if (cambioProyecto) { if (this.proyecto) this.nombre = null; this.proyecto = proyecto; this.doc = null; this.datos = null; this.editando = false; this.verPdfDe = null; }
    const cambioRevision = actual.revision !== this.revision;
    this.revision = actual.revision;
    const recien = visible && !this.visible;
    this.visible = visible;
    if (visible && proyecto && (cambioProyecto || cambioRevision || recien)) {
      // Con «al vuelo» puesto, un cambio del proyecto regenera solo las secciones marcadas; si no, sólo
      // se recarga la vista y el documento queda avisando de que está desfasado.
      if (this.alVuelo && cambioRevision && !cambioProyecto && this.nombre) this.regenerar(false);
      else this.cargar();
    }
  }

  async regenerar(aviso) {
    if (!this.nombre) { if (aviso) this.estado('Elige antes un documento de la lista.', true); return; }
    await this.orden({ op: 'actualizar', nombre: this.nombre }, r => this.estado(
      r.cambio ? 'Secciones regeneradas con el estado actual del proyecto.'
               : (aviso ? 'Las secciones ya estaban al día: no había nada que regenerar.' : '')));
  }

  estado(texto, error) {
    this.el('estado').textContent = texto || '';
    this.el('estado').className = error ? 'error' : '';
  }

  async leerJson(url) {
    const r = await fetch(url, { headers: { 'X-Halo-Request': '1' } });
    const datos = await r.json();
    if (!datos.ok) throw new Error(datos.error || `error ${r.status}`);
    return datos;
  }

  async cargar() {
    if (!this.proyecto) return;
    if (this.cargando) { this.pendiente = true; return; }
    this.cargando = true;
    try {
      const base = '?proyecto=' + encodeURIComponent(this.proyecto);
      this.datos = await this.leerJson('/api/documentos' + base);
      const nombres = this.datos.documentos.map(d => d.nombre);
      if (!nombres.includes(this.nombre)) this.nombre = nombres.find(n => /\.(md|tex)$/i.test(n)) || nombres[0] || null;
      if (this.nombre && /\.(md|tex|txt|html)$/i.test(this.nombre)) {
        this.doc = await this.leerJson('/api/documento' + base + '&nombre=' + encodeURIComponent(this.nombre));
      } else this.doc = this.nombre ? { nombre: this.nombre, tipo: this.nombre.toLowerCase().endsWith('.pdf') ? 'pdf' : 'otro' } : null;
      this.dibujar();
    } catch (err) {
      this.estado('No pude leer los documentos: ' + err.message, true);
    } finally {
      this.cargando = false;
      if (this.pendiente) { this.pendiente = false; this.cargar(); }
    }
  }

  async orden(cuerpo, exito) {
    if (!this.proyecto) return;
    if (cuerpo.op === 'compilar') this.estado('Compilando con MiKTeX… (unos segundos)');
    if (cuerpo.op === 'convertir') this.estado('Convirtiendo con pandoc…');
    try {
      const r = await this.pedir({ ...cuerpo, proyecto_id: this.proyecto });
      if (exito) exito(r);
      await this.cargar();
    } catch (err) {
      this.estado(err.message, true);
    }
  }

  async guardar() {
    const texto = this.el('editor').value;
    try {
      const r = await this.pedir({ op: 'guardar', nombre: this.nombre, texto, firma: this.doc.firma, proyecto_id: this.proyecto });
      this.doc.firma = r.firma; this.doc.texto = texto; this.editando = false;
      this.estado('Guardado.');
      await this.cargar();
    } catch (err) {
      this.estado(err.message, true);
    }
  }

  archivoUrl(ruta) {
    return '/api/documento/archivo?proyecto=' + encodeURIComponent(this.proyecto) + '&ruta=' + encodeURIComponent(ruta);
  }

  dibujar() {
    const datos = this.datos || { documentos: [], guia: { etapas: [], siguiente: '' }, herramientas: {} };
    // guía de etapas
    const etapas = this.el('etapas');
    etapas.replaceChildren(...datos.guia.etapas.map(e => {
      const li = document.createElement('li'); li.dataset.estado = e.estado;
      const punto = document.createElement('span'); punto.className = 'punto';
      const cuerpo = document.createElement('div');
      cuerpo.append(`${e.nombre} · ${e.estado}`);
      const em = document.createElement('em'); em.textContent = e.detalle; cuerpo.appendChild(em);
      li.append(punto, cuerpo);
      return li;
    }));
    const sig = this.el('siguiente');
    sig.replaceChildren();
    if (datos.guia.siguiente) { const b = document.createElement('b'); b.textContent = 'Siguiente paso sugerido: '; sig.append(b, datos.guia.siguiente); }
    // lista de documentos
    this.el('lista').replaceChildren(...datos.documentos.map(d => {
      const li = document.createElement('li');
      const b = document.createElement('button'); b.type = 'button';
      b.setAttribute('aria-current', d.nombre === this.nombre ? 'true' : 'false');
      const nombre = document.createElement('span'); nombre.textContent = d.nombre;
      const detalle = document.createElement('small');
      if (d.desactualizadas) { detalle.className = 'alerta'; detalle.textContent = `${d.desactualizadas} desactualizada${d.desactualizadas > 1 ? 's' : ''}`; }
      else if (d.tipo === 'latex') detalle.textContent = d.pdf ? (d.pdf_vigente ? 'PDF al día' : 'PDF viejo') : 'sin PDF';
      else detalle.textContent = d.modificado.slice(5);
      b.append(nombre, detalle);
      b.onclick = () => { this.nombre = d.nombre; this.editando = false; this.verPdfDe = null; this.estado(''); this.cargar(); };
      li.appendChild(b);
      return li;
    }));
    // documento principal
    const doc = this.doc;
    const tipo = doc ? doc.tipo : null;
    this.el('titulo').textContent = doc ? doc.nombre : '';
    document.querySelector('.doc-barra').hidden = !doc;
    this.el('vacio').hidden = !!doc;
    this.el('editar').textContent = this.editando ? 'Cancelar edición' : 'Editar';
    for (const [id, visible] of [['actualizar', tipo === 'markdown' || tipo === 'latex'], ['compilar', tipo === 'latex'],
      ['verpdf', tipo === 'latex' && !!(doc && doc.pdf)], ['docx', tipo === 'markdown'], ['editar', tipo === 'markdown' || tipo === 'latex' || tipo === 'texto'],
      ['guardar', this.editando], ['solo-caja', tipo === 'markdown' || tipo === 'latex']]) this.el(id).hidden = !visible;
    this.el('verpdf').textContent = this.verPdfDe ? 'Ver fuente' : 'Ver PDF';
    this.el('compilar').disabled = !datos.herramientas.pdflatex;
    this.el('compilar').title = datos.herramientas.pdflatex ? 'pdflatex (MiKTeX) compila el informe y el PDF se ve aquí' : 'MiKTeX no está disponible';
    this.el('docx').disabled = !datos.herramientas.pandoc;
    const hoja = this.el('hoja'), editor = this.el('editor'), pdf = this.el('pdf');
    hoja.hidden = editor.hidden = pdf.hidden = true;
    if (!doc) return;
    if (this.editando) { editor.hidden = false; return; }
    const pdfNombre = tipo === 'pdf' ? doc.nombre : (this.verPdfDe || (tipo === 'latex' && doc.pdf && doc.pdf_vigente ? doc.pdf : null));
    if (pdfNombre) {
      const url = this.archivoUrl(pdfNombre);
      if (pdf.dataset.url !== url) { pdf.src = url; pdf.dataset.url = url; }
      pdf.hidden = false;
      return;
    }
    hoja.hidden = false;
    if (tipo === 'markdown') hoja.innerHTML = doc.html;   // HTML producido por el servidor: todo el texto va escapado
    else if (doc.texto !== undefined) { const pre = document.createElement('pre'); pre.textContent = doc.texto; hoja.replaceChildren(pre); }
    else hoja.textContent = 'Este archivo no se muestra aquí; ábrelo desde la carpeta docs del proyecto.';
  }
}
