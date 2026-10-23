'use strict';

class VistaBloques {
  constructor({ contexto, simulacion }) {
    this.contexto = contexto; this.simulacion = simulacion; this.peticion = 0;
    this.dialogo = document.getElementById('ficha-bloque');
    this.cuerpo = document.getElementById('bloque-contenido');
    document.getElementById('bloque-cerrar').onclick = () => this.dialogo.close();
    this.dialogo.addEventListener('close', () => { this.peticion++; });
  }
  nodo(tag, texto, padre = this.cuerpo) {
    const n = document.createElement(tag); n.textContent = texto; padre.append(n); return n;
  }
  seccion(titulo, items) {
    if (!items?.length) return;
    this.nodo('h3', titulo);
    const lista = this.nodo('ul', '');
    for (const item of items) this.nodo('li', item, lista);
  }
  actualizar(actual) {
    if (this.dialogo.open && (actual.proyecto_id !== this.proyecto || actual.revision !== this.revision)) {
      this.dialogo.close();
    }
  }
  async abrir(uuid) {
    const numero = ++this.peticion, actual = this.contexto();
    this.proyecto = actual.proyecto_id; this.revision = actual.revision;
    this.cuerpo.replaceChildren();
    document.getElementById('bloque-titulo').textContent = 'Ficha del bloque';
    this.nodo('p', 'Leyendo decisiones y evidencias…');
    if (!this.dialogo.open) this.dialogo.showModal();
    try {
      const q = new URLSearchParams({ uuid, proyecto_id: this.proyecto });
      const r = await fetch(`/api/bloques?${q}`), data = await r.json();
      if (numero !== this.peticion || this.contexto().proyecto_id !== this.proyecto) return;
      if (!r.ok || !data.ok) throw new Error(data.error || 'No se pudo abrir la ficha');
      if (data.revision !== this.contexto().revision) throw new Error('El diseño cambió. Vuelve a abrir la ficha.');
      this.dibujar(data.ficha);
    } catch (e) {
      if (numero !== this.peticion) return;
      this.cuerpo.replaceChildren(); this.nodo('p', e.message);
    }
  }
  dibujar(f) {
    this.cuerpo.replaceChildren();
    document.getElementById('bloque-titulo').textContent = f.titulo;
    this.nodo('p', f.documentado ? (f.vigencia === 'vigente' ? 'Documentación vinculada a esta revisión' : 'El diseño cambió: revisar esta documentación') : 'Este bloque todavía no tiene documentación.').className = 'bloque-vigencia';
    this.nodo('p', f.funcion || 'Función pendiente de documentar.');
    this.seccion('Decisiones de diseño', f.decisiones);
    this.seccion('Señales hacia otros bloques', f.interfaces);
    if (f.componentes?.length) {
      this.nodo('h3', 'Componentes del bloque');
      this.nodo('p', f.componentes.map(c => `${c.ref} · ${c.valor}`).join('   /   ')).className = 'bloque-componentes';
    }
    this.seccion('Referencias que faltan en el diseño', f.referencias_ausentes);
    this.nodo('h3', 'Pruebas y experimentos');
    if (!f.pruebas?.length) this.nodo('p', 'No hay simulaciones o experimentos registrados para este bloque.');
    for (const p of f.pruebas || []) {
      const tarjeta = this.nodo('article', ''); tarjeta.className = 'bloque-prueba';
      this.nodo('strong', p.titulo || p.tipo || 'Prueba', tarjeta);
      this.nodo('span', (p.verificacion || 'pendiente').replaceAll('_', ' '), tarjeta).className = 'bloque-sello';
      this.nodo('p', p.descripcion || '', tarjeta);
      if (p.resultado) this.nodo('p', `Resultado registrado: ${p.resultado}`, tarjeta);
      if (p.ruta) this.nodo('small', `Evidencia: ${p.ruta}`, tarjeta);
      if (p.simulacion_id && ['vigente', 'no_validada'].includes(p.verificacion)) {
        const b = this.nodo('button', 'Abrir simulación', tarjeta);
        b.onclick = () => { this.dialogo.close(); this.simulacion(p.simulacion_id); };
      }
    }
    if (f.fuentes?.length) {
      this.nodo('h3', 'Fuentes y documentación');
      for (const fuente of f.fuentes) {
        const p = this.nodo('p', '');
        if (fuente.url && /^https?:\/\//i.test(fuente.url)) {
          const a = this.nodo('a', fuente.titulo || fuente.url, p);
          a.href = fuente.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        } else this.nodo('span', fuente.titulo || fuente.ruta || 'Referencia', p);
        if (fuente.nota) this.nodo('small', fuente.nota, p);
      }
    }
    this.seccion('Pendiente de comprobar', f.pendientes);
    this.nodo('p', 'Una ficha explica el diseño; no sustituye ERC, simulaciones con modelos adecuados ni ensayos físicos.').className = 'bloque-nota';
  }
}
