"""Conversaciones con Claude Code por proyecto: el binario oficial, sin modificar, hablándole por su
entrada y su salida en JSON.

Cada chat es una sesión de Claude Code con identificador propio (`--session-id`), guardada por Claude
en ~/.claude/projects. Un turno es un proceso `claude -p --input-format stream-json
--output-format stream-json --permission-prompts host`: el Halo le escribe el mensaje, lee lo que
va llegando (texto parcial, herramientas, resultados) y responde los permisos con lo que el usuario
decide en pantalla. Al terminar el turno el proceso se cierra: la sesión queda libre para otro turno,
para una consola o para el celular (Remote Control).

La lista de chats de un proyecto vive en <carpeta>/.halo/chats.json; el historial, en la propia
transcripción de Claude Code.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import transcripcion

REPO = Path(__file__).resolve().parents[2]
HOME = Path.home()
CATALOGO = HOME / ".claude" / "cache" / "model-catalog"
# Los mismos modelos que ofrece la app de Claude; si el catálogo que guarda el CLI está a mano, se lee de ahí.
MODELOS_BASE = [
    {"id": "", "nombre": "Modelo por defecto", "seccion": "main"},
    {"id": "claude-opus-5", "nombre": "Opus 5", "seccion": "main"},
    {"id": "claude-fable-5-1", "nombre": "Fable 5.1", "seccion": "main"},
    {"id": "claude-sonnet-5", "nombre": "Sonnet 5", "seccion": "main"},
    {"id": "claude-haiku-4-5-20251001", "nombre": "Haiku 4.5", "seccion": "main"},
    {"id": "claude-fable-5", "nombre": "Fable 5", "seccion": "overflow"},
    {"id": "claude-opus-4-8", "nombre": "Opus 4.8", "seccion": "overflow"},
    {"id": "claude-opus-4-7", "nombre": "Opus 4.7", "seccion": "overflow"},
    {"id": "claude-opus-4-6", "nombre": "Opus 4.6", "seccion": "overflow"},
    {"id": "claude-sonnet-4-6", "nombre": "Sonnet 4.6", "seccion": "overflow"},
]
ALIAS = ("fable", "opus", "sonnet", "haiku")
ESFUERZOS = ("", "low", "medium", "high", "xhigh", "max")


def modelos_disponibles() -> list[dict]:
    """Catálogo de modelos: el que la app de Claude dejó en ~/.claude/cache/model-catalog, o la lista fija."""
    vistos: dict[str, dict] = {}
    for archivo in sorted(CATALOGO.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            modelos = json.loads(archivo.read_text(encoding="utf-8"))["catalog"]["config"]["models"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for m in modelos:   # varios catálogos (app y CLI): se juntan por identificador, el más reciente manda
            if isinstance(m, dict) and m.get("id") and m["id"] not in vistos:
                vistos[m["id"]] = {"id": m["id"], "nombre": m.get("name") or m["id"], "seccion": m.get("section") or "main"}
    if not vistos:
        return list(MODELOS_BASE)
    principales = [m for m in vistos.values() if m["seccion"] == "main"]
    otros = [m for m in vistos.values() if m["seccion"] != "main"]
    return [MODELOS_BASE[0], *principales, *otros]


def modelo_valido(modelo: str) -> bool:
    return modelo in ALIAS or any(m["id"] == modelo for m in modelos_disponibles())
PERMISOS = ("manual", "acceptEdits", "plan", "auto", "bypassPermissions")   # los mismos modos que la app de Claude
PERMISOS_VIEJOS = {"dontAsk": "bypassPermissions"}   # «no preguntar» del CLI deniega todo; el usuario quería «acepta todo»
CONFIG = REPO / "build" / "halo" / "config.json"


def carpetas_extra() -> list[str]:
    """Carpetas fuera del proyecto que Claude Code puede leer y tocar en todos los chats (build/halo/config.json)."""
    try:
        lista = json.loads(CONFIG.read_text(encoding="utf-8")).get("carpetas_extra") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [str(Path(c)) for c in lista if isinstance(c, str) and Path(c).is_dir()]
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NUEVA_CONSOLA = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


class ErrorChat(ValueError):
    pass


def ejecutable_claude() -> str | None:
    """El binario oficial de Claude Code: HALO_CLAUDE_CLI, el del PATH, o el más nuevo instalado."""
    forzado = os.environ.get("HALO_CLAUDE_CLI")
    if forzado:
        return forzado if Path(forzado).is_file() else None
    encontrado = shutil.which("claude")
    if encontrado:
        return encontrado
    # La app de Claude se instala como paquete MSIX: dentro de su contenedor %APPDATA% apunta a
    # AppData\Local\Packages\<paquete>\LocalCache\Roaming. Se buscan las dos rutas, porque el Halo puede
    # correr dentro del contenedor (lanzado por la app) o fuera (Halo.exe, una terminal cualquiera).
    candidatos = [p for patron in ("AppData/Roaming/Claude/claude-code/*/claude.exe",
                                   "AppData/Local/Packages/*/LocalCache/Roaming/Claude/claude-code/*/claude.exe",
                                   ".local/bin/claude.exe",
                                   ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe")
                  for p in HOME.glob(patron) if p.is_file()]
    return str(max(candidatos, key=lambda p: p.stat().st_mtime)) if candidatos else None


def _hora() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _explicar(error: str) -> str:
    """Los fallos del CLI que el usuario puede resolver se explican en su idioma; el resto se muestra tal cual."""
    bajo = error.lower()
    if "refresh oauth token" in bajo or "mid-refresh" in bajo:
        # La sesión sigue iniciada: otro proceso de Claude Code estaba renovando el token (o dejó el candado
        # ~/.claude/.oauth_refresh.lock a medias). Es pasajero; no hay que volver a iniciar sesión.
        return ("Claude Code estaba renovando su sesión en otro proceso y no pudo hacerlo a la vez (" + error.strip() +
                "). Espera un minuto y vuelve a enviar el mensaje; si sigue pasando, cierra las otras ventanas de "
                "Claude Code o borra la carpeta ~/.claude/.oauth_refresh.lock.")
    if any(p in bajo for p in ("authenticate", "oauth", "log in", "login", "not logged", "api key")):
        return ("Claude Code no tiene sesión iniciada en este equipo (" + error.strip() + "). Abre una terminal y ejecuta "
                "«claude auth login»; el Halo nunca maneja tus credenciales.")
    return error


class Turno:
    """Un mensaje del usuario y todo lo que Claude Code hace hasta responder."""

    def __init__(self, servicio: "ServicioChat", carpeta: Path, chat: dict, texto: str, adjuntos: list | None = None):
        self.servicio, self.carpeta, self.chat, self.texto = servicio, carpeta, chat, texto
        self.adjuntos = adjuntos or []
        self.piezas: list[dict] = [{"tipo": "usuario", "texto": texto, "hora": _hora(),
                                    **({"adjuntos": len(self.adjuntos)} if self.adjuntos else {})}]
        self.parcial = ""
        self.pendientes: dict[str, dict] = {}
        self.error: str | None = None
        self.resultado: dict | None = None
        self.terminado = False
        self.inicio = time.time()
        self.base = 0   # piezas que ya tenía la transcripción al empezar: lo demás se muestra desde este turno
        self.reintento = False   # segundo intento tras un fallo pasajero de renovación de sesión
        self.proceso = None
        self._stderr: list[str] = []
        self._cerrojo = threading.Lock()

    # ------------------------------------------------------------ arranque
    def comando(self) -> list[str]:
        chat = self.chat
        # «--permission-prompt-tool stdio» es lo que hace que los permisos lleguen aquí por el flujo: sin eso el
        # CLI decide «preguntar», no encuentra a quién y lo da por denegado (el usuario nunca vería la tarjeta).
        base = [*self.servicio.comando, "-p", "--verbose", "--input-format", "stream-json", "--output-format", "stream-json",
                "--include-partial-messages", "--permission-prompts", "host", "--permission-prompt-tool", "stdio",
                "--add-dir", str(REPO)]
        if chat.get("origen") and not chat.get("sesion_creada"):
            base += ["--resume", chat["origen"], "--fork-session"]
        elif chat.get("sesion_creada") and transcripcion.archivo_de(chat["id"], self.servicio.transcripciones) is not None:
            base += ["--resume", chat["id"]]
        else:
            base += ["--session-id", chat["id"]]   # también si un turno anterior falló antes de dejar transcripción
        if chat.get("titulo"):
            base += ["--name", chat["titulo"]]
        if chat.get("modelo"):
            base += ["--model", chat["modelo"]]
        if chat.get("esfuerzo"):
            base += ["--effort", chat["esfuerzo"]]
        if chat.get("permisos"):
            base += ["--permission-mode", chat["permisos"]]
            if chat["permisos"] == "bypassPermissions":
                base.append("--allow-dangerously-skip-permissions")   # sin esto, en modo -p el CLI no admite saltar permisos
        for carpeta in carpetas_extra():
            base += ["--add-dir", carpeta]
        base += ["--append-system-prompt", self.servicio.contexto(self.carpeta)]
        return base

    def iniciar(self) -> None:
        self.proceso = subprocess.Popen(self.comando(), cwd=str(self.carpeta), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                        creationflags=SIN_VENTANA)
        threading.Thread(target=self._leer_stderr, daemon=True).start()
        threading.Thread(target=self._leer, daemon=True, name=f"halo-chat:{self.chat['id'][:8]}").start()
        # Las imágenes van delante del texto, como bloques de contenido del propio protocolo de Claude Code.
        contenido = [{"type": "image", "source": {"type": "base64", "media_type": a["tipo"], "data": a["datos"]}}
                     for a in self.adjuntos]
        contenido.append({"type": "text", "text": self.texto})
        self._escribir({"type": "user", "message": {"role": "user", "content": contenido}})

    def _escribir(self, objeto: dict) -> None:
        with self._cerrojo:
            if self.proceso and self.proceso.stdin and not self.proceso.stdin.closed:
                try:
                    self.proceso.stdin.write(json.dumps(objeto, ensure_ascii=False) + "\n")
                    self.proceso.stdin.flush()
                except (OSError, ValueError):
                    pass

    def _cerrar_entrada(self) -> None:
        with self._cerrojo:
            try:
                if self.proceso and self.proceso.stdin and not self.proceso.stdin.closed:
                    self.proceso.stdin.close()
            except OSError:
                pass

    def _leer_stderr(self) -> None:
        for linea in self.proceso.stderr:
            self._stderr.append(linea.rstrip())
            del self._stderr[:-40]

    # ------------------------------------------------------------ eventos del CLI
    def _leer(self) -> None:
        try:
            for linea in self.proceso.stdout:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    dato = json.loads(linea)
                except ValueError:
                    continue
                if isinstance(dato, dict):
                    self._evento(dato)
        finally:
            codigo = self.proceso.wait()
            self._cerrar_entrada()
            for tubo in (self.proceso.stdout, self.proceso.stderr):
                try:
                    tubo.close()
                except OSError:
                    pass
            if self.resultado is None and self.error is None:
                detalle = "\n".join(self._stderr[-6:]).strip()
                self.error = _explicar(detalle) if detalle else f"Claude Code terminó sin responder (código {codigo})"
            if self.parcial.strip():
                self.piezas.append({"tipo": "asistente", "texto": self.parcial, "hora": _hora()})
                self.parcial = ""
            for pendiente in self.pendientes.values():
                pendiente["pieza"]["estado"] = "sin respuesta"
            self.pendientes.clear()
            # Primero se persiste lo aprendido en el turno (permisos «siempre», fecha); sólo después el chat queda libre.
            self.servicio._terminar_turno(self)
            self.terminado = True
            self.servicio._avisar()

    def _evento(self, d: dict) -> None:
        tipo = d.get("type")
        if tipo == "system" and d.get("subtype") == "init":
            sesion = d.get("session_id")
            if sesion:
                self.servicio._sesion_confirmada(self.carpeta, self.chat, sesion)
        elif tipo == "stream_event":
            ev = d.get("event") or {}
            if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                self.parcial += ev["delta"].get("text", "")
                self.servicio._avisar(suave=True)
            return
        elif tipo == "assistant":
            self.parcial = ""
            self.piezas.extend(transcripcion.piezas_de_mensaje("assistant", d.get("message") or {}, _hora()))
        elif tipo == "user":
            self.piezas.extend(p for p in transcripcion.piezas_de_mensaje("user", d.get("message") or {}, _hora())
                               if p["tipo"] != "usuario")
        elif tipo == "control_request":
            self._peticion_control(d)
        elif tipo == "result":
            self.resultado = {k: d.get(k) for k in ("subtype", "is_error", "num_turns", "duration_ms", "total_cost_usd", "session_id")}
            if d.get("is_error"):
                self.error = _explicar(transcripcion.texto_de(d.get("result") or d.get("subtype") or "error", 800))
            self._cerrar_entrada()
        self.servicio._avisar()

    def _peticion_control(self, d: dict) -> None:
        req = d.get("request") or {}
        rid = str(d.get("request_id", ""))
        if req.get("subtype") != "can_use_tool":
            self._escribir({"type": "control_response", "response": {"subtype": "error", "request_id": rid,
                                                                     "error": "El Halo sólo atiende permisos de herramientas"}})
            return
        nombre = str(req.get("tool_name", "?"))
        entrada = req.get("input")
        pieza = {"tipo": "permiso", "id": rid, "nombre": nombre, "entrada": transcripcion.resumir_entrada(nombre, entrada),
                 "estado": "pendiente", "hora": _hora()}
        if nombre in set(self.chat.get("siempre", [])):
            pieza["estado"] = "permitido siempre"
            self.piezas.append(pieza)
            self._escribir({"type": "control_response", "response": {"subtype": "success", "request_id": rid,
                                                                     "response": {"behavior": "allow", "updatedInput": entrada}}})
            return
        self.pendientes[rid] = {"pieza": pieza, "entrada": entrada, "nombre": nombre}
        self.piezas.append(pieza)

    # ------------------------------------------------------------ órdenes del usuario
    def responder_permiso(self, rid: str, permitir: bool, siempre: bool = False, motivo: str = "") -> None:
        pendiente = self.pendientes.pop(rid, None)
        if pendiente is None:
            raise ErrorChat("Ese permiso ya no está pendiente")
        pendiente["pieza"]["estado"] = "permitido" if permitir else "denegado"
        if permitir:
            respuesta = {"behavior": "allow", "updatedInput": pendiente["entrada"]}
            if siempre:
                self.chat.setdefault("siempre", [])
                if pendiente["nombre"] not in self.chat["siempre"]:
                    self.chat["siempre"].append(pendiente["nombre"])
                pendiente["pieza"]["estado"] = "permitido siempre"
        else:
            respuesta = {"behavior": "deny", "message": motivo or "El usuario no permitió esta acción desde el Halo."}
        self._escribir({"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": respuesta}})

    def cancelar(self) -> None:
        self._escribir({"type": "control_request", "request_id": uuid.uuid4().hex, "request": {"subtype": "interrupt"}})
        for _ in range(30):
            # El turno puede estar recién creado y todavía sin proceso: entonces no hay nada que esperar.
            if self.terminado or self.proceso is None or self.proceso.poll() is not None:
                break
            time.sleep(0.1)
        if self.proceso is not None and self.proceso.poll() is None:
            self.proceso.kill()
        if self.error is None and self.resultado is None:
            self.error = "Turno detenido por el usuario"

    def estado(self) -> dict:
        return {"activo": not self.terminado, "parcial": self.parcial, "error": self.error, "resultado": self.resultado,
                "segundos": round(time.time() - self.inicio, 1), "pendientes": [p["pieza"] for p in self.pendientes.values()]}


TIPOS_IMAGEN = ("image/png", "image/jpeg", "image/gif", "image/webp")
MAX_ADJUNTO = 4 * 1024 * 1024        # 4 MB por imagen, ya en base64


def _revisar_adjuntos(adjuntos) -> list:
    """Sólo imágenes, y del tamaño que acepta el protocolo. Se comprueba aquí y no en el navegador."""
    if not adjuntos:
        return []
    if not isinstance(adjuntos, list) or len(adjuntos) > 8:
        raise ErrorChat("Como mucho ocho imágenes por mensaje")
    limpios = []
    for a in adjuntos:
        if not isinstance(a, dict):
            raise ErrorChat("Adjunto mal formado")
        tipo, datos = str(a.get("tipo") or ""), str(a.get("datos") or "")
        if tipo not in TIPOS_IMAGEN:
            raise ErrorChat(f"Sólo acepto imágenes ({', '.join(t.split('/')[1] for t in TIPOS_IMAGEN)}); esto es {tipo or 'desconocido'}")
        if not datos or len(datos) > MAX_ADJUNTO:
            raise ErrorChat("Cada imagen puede ocupar hasta 4 MB")
        limpios.append({"tipo": tipo, "datos": datos, "nombre": str(a.get("nombre") or "imagen")})
    return limpios


class ServicioChat:
    def __init__(self, comando: list[str] | None = None, transcripciones: Path | None = None):
        self._comando = comando
        self.transcripciones = Path(transcripciones) if transcripciones else transcripcion.TRANSCRIPCIONES
        self.turnos: dict[str, Turno] = {}
        self.cola: dict[str, list[dict]] = {}   # lo que escribes mientras un chat piensa, para mandárselo al terminar
        self.externos: dict[str, tuple] = {}
        self.banderas_consola = NUEVA_CONSOLA   # las pruebas lo ponen a 0 para no abrir ventanas
        self.al_cambiar = None
        self._ultimo_aviso = 0.0
        self._aviso_pendiente = None
        self._sesion_estado: tuple[float, dict] | None = None
        self.cerrojo = threading.RLock()

    # ------------------------------------------------------------ utilidades
    @property
    def comando(self) -> list[str]:
        if self._comando:
            return list(self._comando)
        exe = ejecutable_claude()
        if not exe:
            raise ErrorChat("No encuentro Claude Code instalado (claude.exe); define HALO_CLAUDE_CLI si está en otro sitio")
        return [exe]

    def disponible(self) -> dict:
        """Si hay binario y si tiene sesión iniciada; se consulta al CLI como mucho cada 5 minutos."""
        try:
            comando = self.comando
        except ErrorChat as exc:
            return {"instalado": False, "sesion": False, "motivo": str(exc)}
        ahora = time.time()
        if self._sesion_estado and ahora - self._sesion_estado[0] < 300:
            return self._sesion_estado[1]
        try:
            r = subprocess.run([*comando, "auth", "status"], capture_output=True, text=True, timeout=20, encoding="utf-8",
                               errors="replace", creationflags=SIN_VENTANA)
            datos = json.loads(r.stdout.strip() or "{}")
            estado = {"instalado": True, "sesion": bool(datos.get("loggedIn")), "metodo": datos.get("authMethod"),
                      "motivo": "" if datos.get("loggedIn") else "Sin sesión: abre una terminal y ejecuta «claude auth login»"}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            estado = {"instalado": True, "sesion": None, "motivo": f"No pude consultar la sesión: {exc}"}
        self._sesion_estado = (ahora, estado)
        return estado

    def contexto(self, carpeta: Path) -> str:
        try:
            from ..documentos.servicio import resumen_guia
            guia = resumen_guia(carpeta)
        except Exception as exc:  # noqa: BLE001 - la guía nunca impide chatear
            guia = f"(guía no disponible: {exc})"
        return (f"Estás trabajando dentro del Halo, en el proyecto de la carpeta {carpeta}. El usuario ve en vivo el "
                "esquemático, la placa, los documentos y la bitácora de ese proyecto. Los motores del Halo están en el repositorio "
                f"{REPO}: esquemáticos con halo.kicad.esquematico, placas por etapas con "
                "«python -m halo.kicad.pcb <carpeta> <etapa>» usando el Python de KiCad, simulación con halo.simulacion, "
                "documentación con «python -m halo.documentos.cli <carpeta> memoria|informe|actualizar|compilar|guia» "
                "(memoria en Markdown e informe en LaTeX en <carpeta>/docs, con secciones generadas que se marcan "
                "desactualizadas cuando cambia su fuente). Las etapas son una guía, no una regla: propón la siguiente y "
                f"deja que el usuario decida. Estado actual: {guia}. "
                "Cada cambio que hagas en los archivos del proyecto aparece en el Halo al instante. "
                + (f"Además del proyecto y del repositorio puedes leer y usar estas carpetas: {', '.join(carpetas_extra())}. " if carpetas_extra() else "")
                + "Responde en español.")

    def _avisar(self, suave: bool = False) -> None:
        if not self.al_cambiar:
            return
        ahora = time.time()
        if suave and ahora - self._ultimo_aviso < 0.12:
            if self._aviso_pendiente is None:
                self._aviso_pendiente = threading.Timer(0.12, self._avisar_ahora)
                self._aviso_pendiente.daemon = True
                self._aviso_pendiente.start()
            return
        self._avisar_ahora()

    def _avisar_ahora(self) -> None:
        self._aviso_pendiente = None
        self._ultimo_aviso = time.time()
        try:
            self.al_cambiar()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ chats de un proyecto
    def _archivo(self, carpeta: Path) -> Path:
        return Path(carpeta) / ".halo" / "chats.json"

    def _cargar(self, carpeta: Path) -> dict:
        archivo = self._archivo(carpeta)
        if archivo.is_file():
            try:
                datos = json.loads(archivo.read_text(encoding="utf-8"))
                if isinstance(datos, dict) and isinstance(datos.get("chats"), list):
                    for c in datos["chats"]:
                        c["permisos"] = PERMISOS_VIEJOS.get(c.get("permisos"), c.get("permisos"))
                    return datos
            except (OSError, ValueError):
                pass
        return {"chats": [], "activo": None}

    def _guardar(self, carpeta: Path, datos: dict) -> None:
        from ..kicad.sesion import escribir_atomico
        escribir_atomico(self._archivo(carpeta), json.dumps(datos, ensure_ascii=False, indent=2))

    def _chat(self, datos: dict, chat_id: str) -> dict:
        for c in datos["chats"]:
            if c["id"] == chat_id:
                return c
        raise ErrorChat("Ese chat no existe en este proyecto")

    def _estado_chat(self, c: dict) -> str:
        if c["id"] in self.turnos and not self.turnos[c["id"]].terminado:
            return "turno"
        externo = self.externos.get(c["id"])
        return externo[1] if externo else "libre"

    def listar(self, carpeta: Path) -> dict:
        with self.cerrojo:
            datos = self._cargar(carpeta)
            chats = [{**c, "estado": self._estado_chat(c)} for c in datos["chats"]]
        return {"chats": chats, "activo": datos.get("activo"), "modelos": modelos_disponibles(), "esfuerzos": ESFUERZOS,
                "permisos": PERMISOS, "claude": self.disponible()}

    def crear(self, carpeta: Path, titulo: str = "", modelo: str = "", esfuerzo: str = "", permisos: str = "manual",
              origen: str | None = None) -> dict:
        if not modelo_valido(modelo) or esfuerzo not in ESFUERZOS or permisos not in PERMISOS:
            raise ErrorChat("Modelo, esfuerzo o permisos no admitidos")
        with self.cerrojo:
            datos = self._cargar(carpeta)
            numero = len(datos["chats"]) + 1
            chat = {"id": str(uuid.uuid4()), "titulo": (titulo or f"Chat {numero}").strip()[:80], "creado": _hora(),
                    "modelo": modelo, "esfuerzo": esfuerzo, "permisos": permisos, "origen": origen, "sesion_creada": False,
                    "siempre": [], "ultimo": None}
            datos["chats"].append(chat)
            datos["activo"] = chat["id"]
            self._guardar(carpeta, datos)
        self._avisar()
        return chat

    def opciones(self, carpeta: Path, chat_id: str, **cambios) -> dict:
        with self.cerrojo:
            datos = self._cargar(carpeta)
            chat = self._chat(datos, chat_id)
            for clave, valor in cambios.items():
                if clave == "titulo" and isinstance(valor, str) and valor.strip():
                    chat["titulo"] = valor.strip()[:80]
                elif clave == "modelo" and isinstance(valor, str) and modelo_valido(valor):
                    chat["modelo"] = valor
                elif clave == "esfuerzo" and valor in ESFUERZOS:
                    chat["esfuerzo"] = valor
                elif clave == "permisos" and valor in PERMISOS:
                    chat["permisos"] = valor
                elif clave == "activo" and valor:
                    datos["activo"] = chat_id
                elif clave == "siempre" and isinstance(valor, list):
                    chat["siempre"] = [str(v) for v in valor][:50]
            self._guardar(carpeta, datos)
        self._avisar()
        return chat

    def quitar(self, carpeta: Path, chat_id: str) -> None:
        """Saca el chat de la lista del proyecto; la conversación sigue en Claude Code y se puede volver a enlazar."""
        if chat_id in self.turnos and not self.turnos[chat_id].terminado or chat_id in self.externos:
            raise ErrorChat("Ese chat está en uso; espera a que termine")
        with self.cerrojo:
            datos = self._cargar(carpeta)
            datos["chats"] = [c for c in datos["chats"] if c["id"] != chat_id]
            if datos.get("activo") == chat_id:
                datos["activo"] = datos["chats"][-1]["id"] if datos["chats"] else None
            self._guardar(carpeta, datos)
        self._avisar()

    def bifurcar(self, carpeta: Path, chat_id: str) -> dict:
        with self.cerrojo:
            origen = self._chat(self._cargar(carpeta), chat_id)
        if not origen.get("sesion_creada"):
            raise ErrorChat("Ese chat aún no tiene ninguna conversación que bifurcar")
        return self.crear(carpeta, f"{origen['titulo']} · rama", origen.get("modelo", ""), origen.get("esfuerzo", ""),
                          origen.get("permisos", "manual"), origen=origen["id"])

    # ------------------------------------------------------------ turnos
    def _ocupado(self, carpeta: Path, datos: dict, chat_id: str) -> str | None:
        """Cada chat va por su cuenta: un turno a la vez dentro de un mismo chat, pero los demás chats del
        proyecto siguen libres (puedes dejar uno pensando y trabajar en otro, o tener uno en el celular)."""
        for c in datos["chats"]:
            if c["id"] != chat_id:
                continue
            estado = self._estado_chat(c)
            if estado == "libre":
                return None
            return {"turno": "Este chat todavía está respondiendo; espera o púlsale Detener",
                    "consola": "Este chat está abierto en una consola; ciérrala para seguir aquí",
                    "remoto": "Este chat está en el celular (Remote Control); cierra esa sesión para seguir aquí"}[estado]
        return None

    def enviar(self, carpeta: Path, chat_id: str, texto: str, adjuntos: list | None = None) -> dict:
        texto = (texto or "").strip()
        adjuntos = _revisar_adjuntos(adjuntos)
        if not texto and adjuntos:
            texto = "(mira la imagen)"
        if not 1 <= len(texto) <= 20000:
            raise ErrorChat("Escribe un mensaje de hasta 20000 caracteres")
        with self.cerrojo:
            datos = self._cargar(carpeta)
            chat = self._chat(datos, chat_id)
            motivo = self._ocupado(carpeta, datos, chat_id)
            if motivo:
                # Mientras piensa, lo que escribas se guarda y se le manda al terminar: así puedes añadir
                # algo a lo que ya le pediste sin esperar ni interrumpirlo. Con la consola o el celular no
                # vale, porque ahí el turno no lo gobierna el Halo.
                if self._estado_chat(chat) != "turno":
                    raise ErrorChat(motivo)
                cola = self.cola.setdefault(chat_id, [])
                if sum(len(x["texto"]) for x in cola) + len(texto) > 40000:
                    raise ErrorChat("Ya hay demasiado escrito esperando; espera a que termine el turno")
                cola.append({"texto": texto, "adjuntos": adjuntos})
                self._avisar()
                return {"chat": chat_id, "encolado": True, "en_cola": len(cola)}
            self.turnos.pop(chat_id, None)
            turno = Turno(self, Path(carpeta), chat, texto, adjuntos)
            turno.base = len(transcripcion.leer(chat["id"], self.transcripciones)) if chat.get("sesion_creada") else 0
            self.turnos[chat_id] = turno
            datos["activo"] = chat_id
            self._guardar(carpeta, datos)
            try:
                turno.iniciar()
            except OSError as exc:
                self.turnos.pop(chat_id, None)
                raise ErrorChat(f"No pude lanzar Claude Code: {exc}") from exc
        self._avisar()
        return {"chat": chat_id, "turno": turno.estado()}

    def _sesion_confirmada(self, carpeta: Path, chat: dict, sesion: str) -> None:
        with self.cerrojo:
            datos = self._cargar(carpeta)
            c = self._chat(datos, chat["id"])
            if c["id"] != sesion:
                # Una bifurcación: Claude Code asignó un identificador nuevo; el chat pasa a llamarse así.
                c["id"] = sesion
                if datos.get("activo") == chat["id"]:
                    datos["activo"] = sesion
                turno = self.turnos.pop(chat["id"], None)
                if turno:
                    self.turnos[sesion] = turno
                chat["id"] = sesion
            c["sesion_creada"] = True
            chat["sesion_creada"] = True
            self._guardar(carpeta, datos)

    def _terminar_turno(self, turno: Turno) -> None:
        with self.cerrojo:
            datos = self._cargar(turno.carpeta)
            try:
                c = self._chat(datos, turno.chat["id"])
                c["ultimo"] = _hora()
                c["siempre"] = turno.chat.get("siempre", [])
                if turno.resultado and not turno.error:
                    c["sesion_creada"] = True
                self._guardar(turno.carpeta, datos)
            except ErrorChat:
                pass
        if turno.error and "refresh oauth token" in turno.error.lower() and not turno.reintento:
            # Fallo pasajero: otro proceso renovaba el token o dejó su candado a medias. Se limpia el candado
            # si lleva un rato abandonado y se repite el turno una sola vez, sin que el usuario haga nada.
            candado = HOME / ".claude" / ".oauth_refresh.lock"
            try:
                if candado.exists() and time.time() - candado.stat().st_mtime > 15:
                    shutil.rmtree(candado, ignore_errors=True)
            except OSError:
                pass
            turno.error = "Claude Code estaba renovando su sesión en otro proceso; se reintenta en unos segundos…"
            threading.Timer(4.0, self._reintentar, args=(turno,)).start()
            self._avisar()
            return
        if self.cola.get(turno.chat["id"]) and not turno.error:
            threading.Timer(0.5, self._vaciar_cola, args=(turno.carpeta, turno.chat["id"])).start()
        self._avisar()

    def _vaciar_cola(self, carpeta: Path, chat_id: str) -> None:
        """Lo que escribiste mientras pensaba se manda junto, en un solo turno, en cuanto queda libre."""
        with self.cerrojo:
            cola = self.cola.pop(chat_id, [])
        if not cola:
            return
        texto = "\n\n".join(x["texto"] for x in cola)
        adjuntos = [a for x in cola for a in x["adjuntos"]]
        try:
            self.enviar(carpeta, chat_id, texto, adjuntos)
        except ErrorChat:
            with self.cerrojo:                      # si volvió a ocuparse, se queda esperando al siguiente final
                self.cola.setdefault(chat_id, cola[:0] + cola)
            self._avisar()

    def _reintentar(self, previo: Turno) -> None:
        with self.cerrojo:
            chat_id = previo.chat["id"]
            if self.turnos.get(chat_id) is not previo:
                return
            try:
                chat = self._chat(self._cargar(previo.carpeta), chat_id)
            except ErrorChat:
                return
            nuevo = Turno(self, previo.carpeta, chat, previo.texto)
            nuevo.reintento = True
            nuevo.base = previo.base
            self.turnos[chat_id] = nuevo
            try:
                nuevo.iniciar()
            except OSError as exc:
                nuevo.error, nuevo.terminado = f"No pude relanzar Claude Code: {exc}", True
        self._avisar()

    def permiso(self, carpeta: Path, chat_id: str, rid: str, permitir: bool, siempre: bool = False, motivo: str = "") -> dict:
        turno = self.turnos.get(chat_id)
        if turno is None or turno.terminado:
            raise ErrorChat("No hay un turno en marcha en ese chat")
        turno.responder_permiso(str(rid), bool(permitir), bool(siempre), motivo)
        self._avisar()
        return turno.estado()

    def cancelar(self, carpeta: Path, chat_id: str) -> dict:
        turno = self.turnos.get(chat_id)
        if turno is None or turno.terminado:
            raise ErrorChat("No hay un turno en marcha en ese chat")
        turno.cancelar()
        self._avisar()
        return turno.estado()

    def conversacion(self, carpeta: Path, chat_id: str) -> dict:
        with self.cerrojo:
            datos = self._cargar(carpeta)
            chat = self._chat(datos, chat_id)
        # La transcripción oficial es la verdad; mientras un turno corre (o si terminó sin dejar rastro), lo que
        # va llegando por el flujo se muestra a continuación de lo que ya había.
        piezas = list(transcripcion.leer(chat["id"], self.transcripciones)) if chat.get("sesion_creada") else []
        turno = self.turnos.get(chat_id)
        estado_turno = None
        if turno is not None:
            estado_turno = turno.estado()
            if not turno.terminado or len(piezas) <= turno.base:
                piezas = piezas[:turno.base] + turno.piezas
            if turno.terminado and turno.error:
                piezas.append({"tipo": "error", "texto": turno.error, "hora": _hora()})
        for x in self.cola.get(chat_id, []):
            piezas.append({"tipo": "usuario", "texto": x["texto"], "hora": "", "en_cola": True,
                           **({"adjuntos": len(x["adjuntos"])} if x["adjuntos"] else {})})
        return {"chat": {**chat, "estado": self._estado_chat(chat)}, "piezas": piezas, "turno": estado_turno,
                "en_cola": len(self.cola.get(chat_id, []))}

    # ------------------------------------------------------------ consola y celular
    def comando_consola(self, chat: dict, remoto: bool = False) -> list[str]:
        """Claude Code interactivo sobre la misma sesión: en una consola propia o, con Remote Control, también desde el celular."""
        comando = list(self.comando)
        comando += ["--resume", chat["id"]] if chat.get("sesion_creada") else ["--session-id", chat["id"]]
        if chat.get("titulo"):
            comando += ["--name", chat["titulo"]]
        if chat.get("permisos"):
            comando += ["--permission-mode", chat["permisos"]]
        for carpeta in [str(REPO), *carpetas_extra()]:
            comando += ["--add-dir", carpeta]
        if remoto:
            comando += ["--remote-control", f"Halo · {chat['titulo']}"]
        return comando

    def consola(self, carpeta: Path, chat_id: str, remoto: bool = False) -> dict:
        with self.cerrojo:
            datos = self._cargar(carpeta)
            chat = self._chat(datos, chat_id)
            motivo = self._ocupado(carpeta, datos, chat_id)
            if motivo:
                raise ErrorChat(motivo)
            try:
                proceso = subprocess.Popen(self.comando_consola(chat, remoto), cwd=str(carpeta), creationflags=self.banderas_consola)
            except OSError as exc:
                raise ErrorChat(f"No pude abrir la consola: {exc}") from exc
            modo = "remoto" if remoto else "consola"
            self.externos[chat_id] = (proceso, modo)
            chat["sesion_creada"] = True
            self._guardar(carpeta, datos)
        threading.Thread(target=self._esperar_externo, args=(carpeta, chat_id, proceso), daemon=True).start()
        self._avisar()
        return {"chat": chat_id, "estado": modo}

    def _esperar_externo(self, carpeta: Path, chat_id: str, proceso) -> None:
        proceso.wait()
        with self.cerrojo:
            self.externos.pop(chat_id, None)
            datos = self._cargar(carpeta)
            try:
                self._chat(datos, chat_id)["ultimo"] = _hora()
                self._guardar(carpeta, datos)
            except ErrorChat:
                pass
        transcripcion._cache.clear()
        self._avisar()

    def cerrar_externo(self, carpeta: Path, chat_id: str) -> None:
        externo = self.externos.get(chat_id)
        if externo and externo[0].poll() is None:
            externo[0].terminate()

    # ------------------------------------------------------------ entrada HTTP
    def operar(self, peticion: dict) -> dict:
        carpeta = Path(str(peticion.get("proyecto_id") or ""))
        if not carpeta.is_dir():
            raise ErrorChat("La petición no indica un proyecto abierto")
        op = peticion.get("op")
        chat_id = str(peticion.get("chat") or "")
        if op == "nuevo":
            chat = self.crear(carpeta, str(peticion.get("titulo") or ""), str(peticion.get("modelo") or ""),
                              str(peticion.get("esfuerzo") or ""), str(peticion.get("permisos") or "manual"))
            return {"ok": True, "chat": chat["id"], **self.listar(carpeta)}
        if op == "enviar":
            return {"ok": True, **self.enviar(carpeta, chat_id, str(peticion.get("texto") or ""),
                                              peticion.get("adjuntos"))}
        if op == "permiso":
            return {"ok": True, "turno": self.permiso(carpeta, chat_id, str(peticion.get("id") or ""), bool(peticion.get("permitir")),
                                                       bool(peticion.get("siempre")), str(peticion.get("motivo") or ""))}
        if op == "cancelar":
            return {"ok": True, "turno": self.cancelar(carpeta, chat_id)}
        if op == "opciones":
            campos = {k: peticion[k] for k in ("titulo", "modelo", "esfuerzo", "permisos", "activo", "siempre") if k in peticion}
            return {"ok": True, "chat": self.opciones(carpeta, chat_id, **campos)}
        if op == "quitar":
            self.quitar(carpeta, chat_id)
            return {"ok": True, **self.listar(carpeta)}
        if op == "bifurcar":
            chat = self.bifurcar(carpeta, chat_id)
            return {"ok": True, "chat": chat["id"], **self.listar(carpeta)}
        if op in ("consola", "remoto"):
            return {"ok": True, **self.consola(carpeta, chat_id, remoto=op == "remoto")}
        raise ErrorChat("Operación de chat desconocida")


SERVICIO = ServicioChat()
