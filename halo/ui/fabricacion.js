'use strict';

class VistaFabricacion {
  constructor(pedir) {
    this.pedir = pedir; this.actual = {}; this.resultado = null; this.ocupado = false;
    this.el = id => document.getElementById(id);
    this.el('fab-preparar').onclick = () => this.preparar();
  }
  actualizar(actual, visible) {
    this.actual = actual;
    this.el('fabricacion').hidden = !visible;
    this.el('fab-preparar').disabled = this.ocupado;
    if (this.resultado && (this.resultado.proyecto_id !== actual.proyecto_id || this.resultado.revision !== actual.revision)) {
      this.resultado = null;
      this.el('fab-resultado').replaceChildren();
      this.el('fab-estado').textContent = 'El diseño cambió. Prepara un paquete de esta versión.';
    }
  }
  async preparar() {
    if (this.ocupado) return;
    this.ocupado = true; this.el('fab-preparar').disabled = true;
    const contexto = { proyecto_id: this.actual.proyecto_id, expected_revision: this.actual.revision };
    this.el('fab-resultado').replaceChildren();
    this.el('fab-estado').textContent = 'KiCad comprueba y exporta una copia. Puede tardar unos minutos…';
    try {
      const r = await this.pedir(contexto);
      if (contexto.proyecto_id !== this.actual.proyecto_id || contexto.expected_revision !== this.actual.revision) {
        this.el('fab-estado').textContent = 'El diseño cambió durante la preparación. Repite para la versión actual.';
        return;
      }
      this.resultado = r;
      this.el('fab-estado').textContent = r.veredicto === 'NOT READY' ? 'No lista para fabricar' : 'Archivos preparados · revisión pendiente';
      const caja = this.el('fab-resultado');
      for (const texto of [...r.bloqueos, ...r.pendientes]) {
        const p = document.createElement('p'); p.textContent = texto; caja.append(p);
      }
      const inventario = document.createElement('p');
      inventario.textContent = `${r.archivos.length} archivos comprobados · ${r.inventario.capas_cobre.length} capas de cobre`;
      caja.append(inventario);
      const informe = document.createElement('a'); informe.textContent = 'Ver informe y manifiesto';
      informe.href = `/api/fabricacion?id=${r.id}`; informe.target = '_blank'; informe.rel = 'noopener'; caja.append(informe);
      if (r.zip) {
        const enlace = document.createElement('a'); enlace.textContent = 'Descargar Gerbers para revisión';
        enlace.href = `/api/fabricacion?id=${r.id}&descargar=1`; enlace.download = r.zip;
        caja.append(document.createElement('br'), enlace);
      }
    } catch (e) { this.el('fab-estado').textContent = e.message; }
    finally { this.ocupado = false; this.el('fab-preparar').disabled = false; }
  }
}
