// Chat de Claude Code por proyecto: un lateral izquierdo, estilo mensajería, con los colores del Halo.
// Cada chat es una sesión oficial de Claude Code; el servidor sólo le habla por su flujo JSON.
// El panel se refresca cuando el servidor avisa (latido «chat»), nunca por temporizador.
(function () {
  const $ = id => document.getElementById(id);
  const panel = $('chat');
  const alternar = $('chat-alternar');
  const mensajes = $('chat-mensajes');
  let proyecto = null;          // proyecto (pestaña) al que pertenece esta ventana
  let lista = { chats: [], activo: null, claude: {}, modelos: [], esfuerzos: [], permisos: [] };
  let chatVisto = null;         // chat que se muestra en pantalla
  let conversacion = null;
  let refrescando = false, otraVez = false;
  let firmaDibujo = '';
  let chatDibujado = null;      // para saber cuándo el usuario cambió de chat o de proyecto
  let adjuntos = [];            // imágenes que acompañarán al próximo mensaje
  let detalle = localStorage.getItem('halo-chat-detalle') !== 'no';
  const scrollDe = new Map();   // dónde estaba leyendo cada chat

  // El reloj del turno se mueve solo: si dependiera del redibujo se quedaría clavado en el segundo
  // en que se pintó, porque el dibujo sólo se rehace cuando cambia el contenido.
  setInterval(() => {
    for (const n of document.querySelectorAll('.tarjeta.reloj[data-desde]')) {
      n.textContent = `Claude Code trabaja… ${Math.round((Date.now() - Number(n.dataset.desde)) / 1000)} s`;
    }
  }, 1000);
  const ETIQUETAS = {
    modelo: { '': 'Modelo por defecto' },   // los nombres reales llegan del servidor (catálogo de la app de Claude)
    esfuerzo: { '': 'Esfuerzo auto', low: 'Esfuerzo bajo', medium: 'Esfuerzo medio', high: 'Esfuerzo alto', xhigh: 'Esfuerzo muy alto', max: 'Esfuerzo máximo' },
    permisos: { auto: 'Auto · Claude decide los permisos', manual: 'Manual · pregunta antes de cambiar', acceptEdits: 'Aceptar ediciones · acepta todas las ediciones', plan: 'Plan · planea antes de cambiar', bypassPermissions: 'Saltar permisos · acepta todo' },
  };

  function proyectoActual() {
    try { return (typeof actual !== 'undefined' && actual && actual.proyecto_id) || (typeof proyectoFijo !== 'undefined' && proyectoFijo) || null; }
    catch (e) { return new URLSearchParams(location.search).get('proyecto'); }
  }
  const consulta = extra => '/api/chat?proyecto=' + encodeURIComponent(proyecto) + (extra || '');

  async function pedirChat(cuerpo) {
    const r = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Halo-Request': '1' },
      body: JSON.stringify({ ...cuerpo, proyecto_id: proyecto }) });
    const datos = await r.json().catch(() => ({ ok: false, error: 'respuesta inválida' }));
    if (!r.ok || !datos.ok) throw new Error(datos.error || `error ${r.status}`);
    return datos;
  }

  function avisar(texto, clase) {
    const aviso = $('chat-aviso');
    aviso.hidden = !texto;
    aviso.className = clase || '';
    aviso.innerHTML = '';
    if (!texto) return;
    // El único trozo con formato es la orden a copiar; el resto se inserta como texto.
    const partes = String(texto).split(/«([^»]+)»/);
    partes.forEach((p, i) => {
      if (i % 2) { const c = document.createElement('code'); c.textContent = p; aviso.appendChild(c); }
      else aviso.appendChild(document.createTextNode(p));
    });
  }

  // ------------------------------------------------ carga
  async function refrescar() {
    if (!proyecto || panel.hidden) return;
    if (refrescando) { otraVez = true; return; }
    refrescando = true;
    try {
      const r = await fetch(consulta(), { headers: { 'X-Halo-Request': '1' } });
      const datos = await r.json();
      if (!datos.ok) throw new Error(datos.error);
      lista = datos;
      if (!lista.chats.some(c => c.id === chatVisto)) chatVisto = lista.activo || (lista.chats[0] || {}).id || null;
      if (chatVisto) {
        const rc = await fetch(consulta('&chat=' + encodeURIComponent(chatVisto)), { headers: { 'X-Halo-Request': '1' } });
        const dc = await rc.json();
        conversacion = dc.ok ? dc : null;
        if (!dc.ok) avisar(dc.error);
      } else conversacion = null;
      dibujar();
    } catch (err) {
      avisar('No pude leer los chats: ' + err.message);
    } finally {
      refrescando = false;
      if (otraVez) { otraVez = false; refrescar(); }
    }
  }

  // ------------------------------------------------ dibujo
  function opciones(select, valores, etiquetas, elegido) {
    select.innerHTML = '';
    let grupo = null;   // los modelos traen sección: los principales sueltos, el resto bajo «Más modelos»
    for (const v of valores) {
      const id = typeof v === 'string' ? v : v.id;
      const o = document.createElement('option');
      o.value = id; o.textContent = typeof v === 'string' ? (etiquetas[id] || id) : v.nombre;
      if (id === elegido) o.selected = true;
      if (typeof v !== 'string' && v.seccion && v.seccion !== 'main') {
        if (!grupo) { grupo = document.createElement('optgroup'); grupo.label = 'Más modelos'; select.appendChild(grupo); }
        grupo.appendChild(o);
      } else select.appendChild(o);
    }
    if (elegido && ![...select.options].some(o => o.value === elegido)) {   // un modelo guardado que ya no está en la lista
      const o = document.createElement('option'); o.value = elegido; o.textContent = elegido; o.selected = true; select.appendChild(o);
    }
  }

  function dibujarLista() {
    const cont = $('chat-lista');
    cont.innerHTML = '';
    for (const c of lista.chats) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'chat-pestana'; b.role = 'tab';
      b.dataset.estado = c.estado; b.title = `${c.titulo} · ${c.estado}` + (c.ultimo ? ` · último ${c.ultimo.replace('T', ' ')}` : '');
      b.setAttribute('aria-selected', c.id === chatVisto ? 'true' : 'false');
      const punto = document.createElement('span'); punto.className = 'punto';
      b.appendChild(punto); b.appendChild(document.createTextNode(c.titulo));
      b.addEventListener('click', () => { chatVisto = c.id; pedirChat({ op: 'opciones', chat: c.id, activo: true }).catch(() => {}); refrescar(); });
      b.addEventListener('dblclick', () => renombrar(c));
      cont.appendChild(b);
    }
    const ocupado = lista.chats.some(c => c.estado !== 'libre');
    alternar.classList.toggle('ocupado', ocupado);
  }

  function formato(texto) {
    // Un formato mínimo y seguro: se escapa todo y luego se reconocen bloques de código, código en línea y negrita.
    const esc = s => s.replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
    const trozos = String(texto).split(/```/);
    return trozos.map((t, i) => {
      if (i % 2) return '<pre><code>' + esc(t.replace(/^[a-z]*\n/, '')) + '</code></pre>';
      return esc(t).replace(/`([^`\n]+)`/g, '<code>$1</code>').replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
    }).join('');
  }

  const hora = h => h ? h.replace(/^\d{4}-\d{2}-\d{2}T/, '').replace(/\.\d+Z?$/, '').slice(0, 5) : '';

  function pieza(p) {
    if (p.tipo === 'usuario' || p.tipo === 'asistente' || p.tipo === 'error') {
      const d = document.createElement('div');
      d.className = 'burbuja ' + p.tipo + (p.en_cola ? ' en-cola' : '');
      d.innerHTML = formato(p.texto);
      if (p.adjuntos) { const i = document.createElement('em'); i.textContent = `${p.adjuntos} imagen${p.adjuntos > 1 ? 'es' : ''}`; d.appendChild(i); }
      if (p.en_cola) { const t = document.createElement('time'); t.textContent = 'en cola'; d.appendChild(t); }
      else if (p.hora) { const t = document.createElement('time'); t.textContent = hora(p.hora); d.appendChild(t); }
      return d;
    }
    if (p.tipo === 'herramienta') {
      if (!detalle) return null;
      const d = document.createElement('div');
      d.className = 'tarjeta herramienta';
      const b = document.createElement('b'); b.textContent = p.nombre;
      d.appendChild(b); d.appendChild(document.createTextNode(' · ' + (p.entrada || '')));
      return d;
    }
    if (p.tipo === 'resultado') {
      if (!detalle) return null;
      const d = document.createElement('details');
      d.className = 'tarjeta resultado' + (p.error ? ' error' : '');
      const s = document.createElement('summary'); s.textContent = p.error ? 'resultado con error' : 'resultado';
      const pre = document.createElement('pre'); pre.textContent = p.texto || '(vacío)';
      d.appendChild(s); d.appendChild(pre);
      return d;
    }
    if (p.tipo === 'permiso') {
      const d = document.createElement('div');
      d.className = 'permiso';
      const s = document.createElement('small'); s.textContent = 'Claude Code pide permiso · ' + p.nombre;
      const c = document.createElement('code'); c.textContent = p.entrada || '';
      d.appendChild(s); d.appendChild(c);
      if (p.estado === 'pendiente') {
        const botones = document.createElement('div'); botones.className = 'botones';
        for (const [texto, clase, cuerpo] of [['Permitir', 'permitir', { permitir: true }], ['Siempre en este chat', 'permitir', { permitir: true, siempre: true }], ['Denegar', 'denegar', { permitir: false }]]) {
          const b = document.createElement('button'); b.type = 'button'; b.className = clase; b.textContent = texto;
          b.addEventListener('click', async () => {
            botones.querySelectorAll('button').forEach(x => { x.disabled = true; });
            try { await pedirChat({ op: 'permiso', chat: chatVisto, id: p.id, ...cuerpo }); } catch (err) { avisar(err.message); }
          });
          botones.appendChild(b);
        }
        d.appendChild(botones);
      } else {
        const e = document.createElement('span'); e.className = 'estado'; e.textContent = p.estado; d.appendChild(e);
      }
      return d;
    }
    return null;
  }

  function dibujar() {
    dibujarLista();
    const chat = conversacion ? conversacion.chat : null;
    const turno = conversacion ? conversacion.turno : null;
    const activo = !!(turno && turno.activo);
    $('chat-opciones').hidden = !chat;
    $('chat-redactor').hidden = !chat;
    if (chat) {
      opciones($('chat-modelo'), lista.modelos, ETIQUETAS.modelo, chat.modelo);
      opciones($('chat-esfuerzo'), lista.esfuerzos, ETIQUETAS.esfuerzo, chat.esfuerzo);
      opciones($('chat-permisos'), lista.permisos, ETIQUETAS.permisos, chat.permisos);
      const libre = chat.estado === 'libre';
      for (const id of ['chat-bifurcar', 'chat-consola', 'chat-remoto', 'chat-quitar']) $(id).disabled = !libre;
      $('chat-bifurcar').disabled = !libre || !chat.sesion_creada;
      // Mientras piensa se puede seguir escribiendo: lo que mandes se guarda y se le entrega al terminar.
      $('chat-enviar').disabled = !(libre || chat.estado === 'turno');
      $('chat-enviar').textContent = chat.estado === 'turno' ? 'Añadir' : 'Enviar';
      // Consola y Remoto se encienden mientras el chat está allí, para verlo de un vistazo.
      $('chat-consola').classList.toggle('activo', chat.estado === 'consola');
      $('chat-consola').textContent = chat.estado === 'consola' ? 'En consola' : 'Consola';
      $('chat-remoto').classList.toggle('activo', chat.estado === 'remoto');
      $('chat-remoto').textContent = chat.estado === 'remoto' ? 'En el celular' : 'Remoto';
      $('chat-sesion').textContent = 'sesión ' + chat.id.slice(0, 8) + '… · claude --resume';
      $('chat-sesion').dataset.id = chat.id;
      $('chat-detener').hidden = !activo;
      $('chat-texto').placeholder = libre ? 'Escribe a Claude Code… (Enter envía, Shift+Enter salta de línea)'
        : chat.estado === 'turno' ? 'Está respondiendo; lo que escribas se le añade al terminar…'
        : chat.estado === 'consola' ? 'Este chat está abierto en una consola; ciérrala para seguir aquí.'
        : 'Este chat está en el celular (Remote Control); cierra esa sesión para seguir aquí.';
    }
    // Los avisos informativos (sin sesión, sin chats) se recalculan en cada dibujo; un error de una orden
    // permanece hasta el siguiente dibujo que tenga algo que decir.
    const claude = lista.claude || {};
    if (claude.instalado === false) avisar(claude.motivo || 'No encuentro Claude Code instalado');
    else if (claude.sesion === false) avisar('Claude Code no tiene sesión iniciada en este equipo. Abre una terminal y ejecuta «claude auth login»; el Halo nunca maneja tus credenciales.', 'info');
    else if (!lista.chats.length) avisar('Pulsa + para abrir el primer chat de este proyecto.', 'info');
    else if ($('chat-aviso').className === 'info') avisar('');

    // Sólo se vuelve a dibujar la conversación si algo cambió (el texto parcial cambia en cada latido).
    const piezas = conversacion ? conversacion.piezas : [];
    const firma = JSON.stringify([chatVisto, piezas.length, piezas.map(p => p.estado || '').join(','), turno && turno.parcial, turno && turno.error, activo, detalle]);
    if (firma === firmaDibujo) return;
    firmaDibujo = firma;
    const cambioDeChat = chatDibujado !== chatVisto;
    if (!cambioDeChat) scrollDe.set(chatVisto, mensajes.scrollTop);
    const pegado = mensajes.scrollTop + mensajes.clientHeight >= mensajes.scrollHeight - 40;
    chatDibujado = chatVisto;
    mensajes.innerHTML = '';
    for (const p of piezas) { const el = pieza(p); if (el) mensajes.appendChild(el); }
    if (activo && turno.parcial) {
      const d = document.createElement('div'); d.className = 'burbuja asistente parcial'; d.innerHTML = formato(turno.parcial); mensajes.appendChild(d);
    } else if (activo && !piezas.some(p => p.tipo === 'permiso' && p.estado === 'pendiente')) {
      const d = document.createElement('div'); d.className = 'tarjeta reloj';
      d.dataset.desde = String(Date.now() - (turno.segundos || 0) * 1000);
      d.textContent = `Claude Code trabaja… ${Math.round(turno.segundos || 0)} s`;
      mensajes.appendChild(d);
    }
    // Al cambiar de chat o de proyecto se vuelve a donde estabas leyendo, y si es la primera vez, al final.
    // Antes se quedaba arriba del todo, que es justo lo que nadie quiere ver.
    if (cambioDeChat) mensajes.scrollTop = scrollDe.has(chatVisto) ? scrollDe.get(chatVisto) : mensajes.scrollHeight;
    else if (pegado || activo) mensajes.scrollTop = mensajes.scrollHeight;
  }

  // ------------------------------------------------ órdenes
  async function orden(cuerpo, exito) {
    try {
      const r = await pedirChat(cuerpo);
      if (exito) exito(r);
      refrescar();
    } catch (err) { avisar(err.message); }
  }

  // El chat nuevo hereda las opciones del que se está viendo; el nombre se cambia con doble clic en su pestaña.
  $('chat-nuevo').addEventListener('click', () => {
    if (!proyecto) return avisar('Abre un proyecto para chatear sobre él.');
    const base = conversacion ? conversacion.chat : {};
    orden({ op: 'nuevo', modelo: base.modelo || '', esfuerzo: base.esfuerzo || '', permisos: base.permisos || 'manual' }, r => { chatVisto = r.chat; });
  });
  let renombrando = null;
  function renombrar(c) {
    renombrando = c.id;
    $('chat-nombre').value = c.titulo;
    $('dialogo-chat').showModal();
    $('chat-nombre').select();
  }
  $('chat-nombre-cancelar').addEventListener('click', () => $('dialogo-chat').close());
  $('form-chat').addEventListener('submit', ev => {
    ev.preventDefault();
    const titulo = $('chat-nombre').value.trim();
    $('dialogo-chat').close();
    if (titulo && renombrando) orden({ op: 'opciones', chat: renombrando, titulo });
  });
  for (const [id, clave] of [['chat-modelo', 'modelo'], ['chat-esfuerzo', 'esfuerzo'], ['chat-permisos', 'permisos']]) {
    $(id).addEventListener('change', ev => orden({ op: 'opciones', chat: chatVisto, [clave]: ev.target.value }));
  }
  $('chat-bifurcar').addEventListener('click', () => orden({ op: 'bifurcar', chat: chatVisto }, r => { chatVisto = r.chat; }));
  // La primera vez en una carpeta, Claude Code interactivo pregunta si confías en ella y no arranca
  // (ni se enlaza con el celular) hasta que respondas en esa ventana.
  const AVISO_VENTANA = 'Se abrió una ventana de consola. Si es la primera vez que abres esta carpeta en Claude Code, '
    + 'te preguntará si confías en sus archivos: respóndele ahí. ';
  $('chat-consola').addEventListener('click', () => orden({ op: 'consola', chat: chatVisto },
    () => avisar(AVISO_VENTANA + 'Este chat queda ocupado hasta que cierres esa ventana.', 'info')));
  $('chat-remoto').addEventListener('click', () => orden({ op: 'remoto', chat: chatVisto },
    () => avisar(AVISO_VENTANA + 'Después aparecerá en la app de Claude de tu celular, con el nombre de este chat.', 'info')));
  // Quitar pide una segunda pulsación en vez de un diálogo: la conversación no se pierde, sólo sale de la lista.
  let confirmarQuitar = null;
  $('chat-quitar').addEventListener('click', () => {
    const b = $('chat-quitar');
    if (confirmarQuitar === chatVisto) {
      confirmarQuitar = null; b.textContent = 'Quitar';
      orden({ op: 'quitar', chat: chatVisto });
      return;
    }
    confirmarQuitar = chatVisto; b.textContent = '¿Quitar? Sigue guardado en Claude Code';
    setTimeout(() => { if (confirmarQuitar === chatVisto) { confirmarQuitar = null; b.textContent = 'Quitar'; } }, 5000);
  });
  $('chat-detener').addEventListener('click', () => orden({ op: 'cancelar', chat: chatVisto }));
  // El identificador es la llave para seguir el chat desde cualquier terminal: claude --resume <id>
  $('chat-sesion').addEventListener('click', async ev => {
    const id = ev.currentTarget.dataset.id;
    try { await navigator.clipboard.writeText('claude --resume ' + id); avisar('Copiado: «claude --resume ' + id + '»', 'info'); }
    catch (e) { avisar('Sesión ' + id, 'info'); }
  });
  $('chat-redactor').addEventListener('submit', ev => {
    ev.preventDefault();
    const texto = $('chat-texto').value.trim();
    if ((!texto && !adjuntos.length) || !chatVisto) return;
    const envio = adjuntos.slice();
    $('chat-texto').value = '';
    adjuntos = []; pintarAdjuntos();
    $('chat-enviar').disabled = true;
    orden({ op: 'enviar', chat: chatVisto, texto, adjuntos: envio }, r => {
      if (r.encolado) avisar(`Guardado: se lo mando en cuanto termine (${r.en_cola} esperando).`, 'info');
    });
  });

  // ---- imágenes: pegadas con Ctrl+V, elegidas con el botón o soltadas sobre el chat
  function pintarAdjuntos() {
    const caja = $('chat-adjuntos');
    caja.innerHTML = '';
    caja.hidden = !adjuntos.length;
    adjuntos.forEach((a, i) => {
      const f = document.createElement('figure');
      const img = document.createElement('img'); img.src = `data:${a.tipo};base64,${a.datos}`; img.alt = a.nombre;
      const x = document.createElement('button'); x.type = 'button'; x.textContent = '×'; x.title = `Quitar ${a.nombre}`;
      x.addEventListener('click', () => { adjuntos.splice(i, 1); pintarAdjuntos(); });
      f.append(img, x); caja.appendChild(f);
    });
  }
  async function agregarImagen(archivo) {
    if (!archivo || !/^image\/(png|jpeg|gif|webp)$/.test(archivo.type)) { avisar('Sólo puedo adjuntar imágenes (png, jpeg, gif o webp).'); return; }
    if (adjuntos.length >= 8) { avisar('Como mucho ocho imágenes por mensaje.'); return; }
    const datos = await new Promise(res => { const l = new FileReader(); l.onload = () => res(String(l.result).split(',')[1]); l.readAsDataURL(archivo); });
    if (datos.length > 4 * 1024 * 1024) { avisar(`«${archivo.name || 'la imagen'}» pasa de 4 MB.`); return; }
    adjuntos.push({ tipo: archivo.type, datos, nombre: archivo.name || 'captura.png' });
    pintarAdjuntos();
  }
  $('chat-imagen').addEventListener('click', () => $('chat-archivo').click());
  $('chat-archivo').addEventListener('change', async ev => {
    for (const f of ev.target.files) await agregarImagen(f);
    ev.target.value = '';
  });
  $('chat-texto').addEventListener('paste', async ev => {
    const files = [...(ev.clipboardData ? ev.clipboardData.files : [])];
    if (!files.length) return;                 // texto pegado: que siga su camino normal
    ev.preventDefault();
    for (const f of files) await agregarImagen(f);
  });
  for (const nombre of ['dragover', 'drop']) {
    panel.addEventListener(nombre, async ev => {
      if (!ev.dataTransfer || ![...ev.dataTransfer.types].includes('Files')) return;
      ev.preventDefault();
      if (nombre === 'drop') for (const f of ev.dataTransfer.files) await agregarImagen(f);
    });
  }
  // Cuando el chat vive en una consola o en el celular, nadie avisa al Halo de lo que allí se escribe:
  // la transcripción sí crece, así que se relee cada pocos segundos para verlo aquí a la vez.
  setInterval(() => {
    const chat = conversacion && conversacion.chat;
    if (!panel.hidden && chat && (chat.estado === 'consola' || chat.estado === 'remoto')) refrescar();
  }, 4000);

  $('chat-detalle').checked = detalle;
  $('chat-detalle').addEventListener('change', ev => {
    detalle = ev.target.checked;
    localStorage.setItem('halo-chat-detalle', detalle ? 'si' : 'no');
    firmaDibujo = ''; dibujar();
  });
  $('chat-texto').addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); $('chat-redactor').requestSubmit(); }
  });

  // ------------------------------------------------ panel
  function mostrarPanel(visible) {
    panel.hidden = !visible;
    document.body.classList.toggle('con-chat', visible);
    alternar.setAttribute('aria-pressed', visible ? 'true' : 'false');
    try { localStorage.setItem('halo-chat', visible ? '1' : '0'); } catch (e) { /* sin memoria local */ }
    if (visible) refrescar();
  }
  alternar.addEventListener('click', () => mostrarPanel(panel.hidden));
  window.addEventListener('halo-estado', ev => {
    const nuevo = (ev.detail || {}).proyecto || proyectoActual();
    if (nuevo !== proyecto) { proyecto = nuevo; chatVisto = null; conversacion = null; firmaDibujo = ''; refrescar(); }
  });
  window.addEventListener('halo-latido', ev => { if ((ev.detail || []).includes('chat')) refrescar(); });
  // El panel recuerda si estaba abierto; «?chat=1» en la dirección lo abre de entrada (Halo.exe, capturas).
  let inicial = '0';
  try { inicial = localStorage.getItem('halo-chat') || '0'; } catch (e) { /* sin memoria local */ }
  if (inicial === '1' || new URLSearchParams(location.search).get('chat') === '1') mostrarPanel(true);
})();
