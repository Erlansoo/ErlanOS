"""Chat de Claude Code por proyecto, con un doble del CLI que habla el mismo protocolo JSON.

    python -m unittest halo.pruebas.test_chat
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from halo import servidor  # noqa: E402
from halo.chat import transcripcion  # noqa: E402
from halo.chat.servicio import ErrorChat, ServicioChat  # noqa: E402

FALSO = Path(__file__).with_name("falso_claude.py")


def esperar(condicion, segundos: float = 10.0) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.03)
    return False


class Chat(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raiz = Path(self.temp.name)
        self.proyecto = self.raiz / "proyecto"
        self.proyecto.mkdir()
        (self.proyecto / "a.kicad_sch").write_text("(kicad_sch (version 20250114) (generator halo))", encoding="utf-8")
        self.transcripciones = self.raiz / "transcripciones"
        os.environ["FALSO_CLAUDE_TRANSCRIPCIONES"] = str(self.transcripciones)
        self.addCleanup(os.environ.pop, "FALSO_CLAUDE_TRANSCRIPCIONES", None)
        self.servicio = ServicioChat([sys.executable, str(FALSO)], self.transcripciones)
        self.avisos = 0

        def contar():
            self.avisos += 1
        self.servicio.al_cambiar = contar

    def turno_terminado(self, chat_id):
        return lambda: chat_id in self.servicio.turnos and self.servicio.turnos[chat_id].terminado

    def test_crear_enviar_historial_y_retomar(self):
        chat = self.servicio.crear(self.proyecto, "Diseño", modelo="sonnet", esfuerzo="high")
        self.assertEqual(self.servicio.listar(self.proyecto)["activo"], chat["id"])
        self.assertTrue(self.servicio.listar(self.proyecto)["claude"]["sesion"])
        modelos = {m["id"]: m for m in self.servicio.listar(self.proyecto)["modelos"]}
        self.assertIn("claude-fable-5-1", modelos, "los mismos modelos que la app de Claude")
        self.assertEqual(modelos[""]["nombre"], "Modelo por defecto")
        self.servicio.enviar(self.proyecto, chat["id"], "hola")
        self.assertTrue(esperar(self.turno_terminado(chat["id"])))
        turno = self.servicio.turnos[chat["id"]]
        self.assertIsNone(turno.error, turno.error)
        self.assertEqual([p["tipo"] for p in turno.piezas], ["usuario", "asistente"])
        self.assertIn("Hola desde el doble", turno.piezas[1]["texto"])
        self.assertGreater(self.avisos, 2, "cada evento avisa a la interfaz")
        guardado = json.loads((self.proyecto / ".halo" / "chats.json").read_text(encoding="utf-8"))
        self.assertTrue(guardado["chats"][0]["sesion_creada"])
        historial = self.servicio.conversacion(self.proyecto, chat["id"])["piezas"]
        self.assertEqual([p["tipo"] for p in historial], ["usuario", "asistente"], "el historial sale de la transcripción oficial")
        self.servicio.enviar(self.proyecto, chat["id"], "sigue")
        self.assertTrue(esperar(self.turno_terminado(chat["id"])))
        self.assertIn("Retomo", self.servicio.turnos[chat["id"]].piezas[-1]["texto"], "el segundo turno reanuda la sesión")
        self.assertEqual(len(self.servicio.conversacion(self.proyecto, chat["id"])["piezas"]), 4)

    def test_permiso_permitir_y_denegar(self):
        chat = self.servicio.crear(self.proyecto, "Permisos")
        self.servicio.enviar(self.proyecto, chat["id"], "haz algo con permiso")
        turno = self.servicio.turnos[chat["id"]]
        self.assertTrue(esperar(lambda: turno.pendientes), "llega la petición de permiso")
        rid = next(iter(turno.pendientes))
        self.assertEqual(turno.pendientes[rid]["nombre"], "Bash")
        # Un turno a la vez: lo que escribas mientras piensa no lo interrumpe, se guarda para el final.
        r = self.servicio.enviar(self.proyecto, chat["id"], "otro")
        self.assertTrue(r["encolado"])
        self.assertEqual(self.servicio.cola[chat["id"]][0]["texto"], "otro")
        self.servicio.cola.pop(chat["id"])          # aquí sólo interesa que no arrancara un segundo turno
        self.servicio.permiso(self.proyecto, chat["id"], rid, permitir=True, siempre=True)
        self.assertTrue(esperar(self.turno_terminado(chat["id"])))
        tipos = [p["tipo"] for p in turno.piezas]
        self.assertEqual(tipos, ["usuario", "permiso", "herramienta", "resultado", "asistente"], tipos)
        self.assertEqual(turno.piezas[1]["estado"], "permitido siempre")
        self.assertIn("Bash", json.loads((self.proyecto / ".halo" / "chats.json").read_text(encoding="utf-8"))["chats"][0]["siempre"])
        # con «siempre», la siguiente petición del mismo tipo se concede sin preguntar
        self.servicio.enviar(self.proyecto, chat["id"], "otra vez con permiso")
        self.assertTrue(esperar(self.turno_terminado(chat["id"])))
        self.assertEqual(self.servicio.turnos[chat["id"]].piezas[1]["estado"], "permitido siempre")
        otro = self.servicio.crear(self.proyecto, "Negar")
        self.servicio.enviar(self.proyecto, otro["id"], "con permiso")
        t2 = self.servicio.turnos[otro["id"]]
        self.assertTrue(esperar(lambda: t2.pendientes))
        self.servicio.permiso(self.proyecto, otro["id"], next(iter(t2.pendientes)), permitir=False)
        self.assertTrue(esperar(self.turno_terminado(otro["id"])))
        self.assertIn("no ejecuto", t2.piezas[-1]["texto"])

    def test_cancelar_fallo_y_bifurcar(self):
        chat = self.servicio.crear(self.proyecto, "Largo")
        self.servicio.enviar(self.proyecto, chat["id"], "espera un rato")
        turno = self.servicio.turnos[chat["id"]]
        self.assertTrue(esperar(lambda: turno.parcial))
        self.servicio.cancelar(self.proyecto, chat["id"])
        self.assertTrue(esperar(self.turno_terminado(chat["id"])))
        self.assertEqual(turno.resultado["subtype"], "success")
        self.servicio.enviar(self.proyecto, chat["id"], "ahora falla")
        self.assertTrue(esperar(self.turno_terminado(chat["id"])))
        self.assertIn("fallo simulado", self.servicio.turnos[chat["id"]].error)
        rama = self.servicio.bifurcar(self.proyecto, chat["id"])
        self.servicio.enviar(self.proyecto, rama["id"], "hola rama")
        self.assertTrue(esperar(lambda: any(t.terminado for i, t in list(self.servicio.turnos.items()) if i != chat["id"])))
        chats = self.servicio.listar(self.proyecto)["chats"]
        self.assertEqual(len(chats), 2)
        nuevo = [c for c in chats if c["id"] != chat["id"]][0]
        self.assertNotEqual(nuevo["id"], rama["id"], "la bifurcación recibe el identificador que asigna Claude Code")
        self.assertTrue(nuevo["sesion_creada"])
        self.assertEqual(nuevo["origen"], chat["id"])

    def test_varios_chats_a_la_vez(self):
        """Un turno a la vez por chat, pero los demás chats del proyecto siguen libres."""
        uno = self.servicio.crear(self.proyecto, "Uno")
        otro = self.servicio.crear(self.proyecto, "Otro")
        self.servicio.enviar(self.proyecto, uno["id"], "espera un buen rato")
        primero = self.servicio.turnos[uno["id"]]
        self.assertTrue(esperar(lambda: primero.parcial))
        encolado = self.servicio.enviar(self.proyecto, uno["id"], "otra cosa")   # el mismo chat está ocupado
        self.assertTrue(encolado["encolado"], "no arranca un segundo turno; espera su vez")
        self.servicio.enviar(self.proyecto, otro["id"], "hola")           # el otro chat no está ocupado
        self.assertTrue(esperar(self.turno_terminado(otro["id"])))
        self.assertIn("Hola desde el doble", self.servicio.turnos[otro["id"]].piezas[-1]["texto"])
        estados = {c["titulo"]: c["estado"] for c in self.servicio.listar(self.proyecto)["chats"]}
        self.assertEqual(estados, {"Uno": "turno", "Otro": "libre"})
        self.servicio.cola.pop(uno["id"], None)     # lo encolado no debe arrancar al cancelar
        self.servicio.cancelar(self.proyecto, uno["id"])
        self.assertTrue(esperar(self.turno_terminado(uno["id"])))

    def test_lo_escrito_mientras_piensa_se_entrega_al_terminar(self):
        """Añadir algo a lo que ya le pediste no interrumpe el turno: se junta y se manda al acabar."""
        chat = self.servicio.crear(self.proyecto, "Cola")
        self.servicio.enviar(self.proyecto, chat["id"], "espera un buen rato")
        primero = self.servicio.turnos[chat["id"]]
        self.assertTrue(esperar(lambda: primero.parcial))
        self.assertTrue(self.servicio.enviar(self.proyecto, chat["id"], "y esto también")["encolado"])
        self.assertTrue(self.servicio.enviar(self.proyecto, chat["id"], "y esto")["encolado"])
        self.assertEqual(len(self.servicio.cola[chat["id"]]), 2)
        self.servicio.cancelar(self.proyecto, chat["id"])
        # Al quedar libre, lo guardado sale en un solo turno con los dos textos juntos.
        self.assertTrue(esperar(lambda: self.servicio.turnos[chat["id"]] is not primero, 8))
        self.assertEqual(self.servicio.turnos[chat["id"]].texto, "y esto también\n\ny esto")
        self.assertFalse(self.servicio.cola.get(chat["id"]))
        self.servicio.cancelar(self.proyecto, chat["id"])

    def test_solo_acepta_imagenes_como_adjunto(self):
        from halo.chat.servicio import _revisar_adjuntos
        self.assertEqual(_revisar_adjuntos(None), [])
        bien = _revisar_adjuntos([{"tipo": "image/png", "datos": "AAAA", "nombre": "captura.png"}])
        self.assertEqual(bien[0]["tipo"], "image/png")
        for malo in ([{"tipo": "application/pdf", "datos": "AA"}], [{"tipo": "image/png", "datos": ""}],
                     [{"tipo": "image/png", "datos": "A" * (4 * 1024 * 1024 + 1)}]):
            with self.assertRaises(ErrorChat):
                _revisar_adjuntos(malo)

    def test_comando_del_turno(self):
        """El CLI sólo manda los permisos al Halo si se le nombra la herramienta de permisos «stdio»."""
        chat = self.servicio.crear(self.proyecto, "Opciones", modelo="sonnet", esfuerzo="high", permisos="acceptEdits")
        from halo.chat.servicio import Turno
        comando = Turno(self.servicio, self.proyecto, chat, "hola").comando()
        for esperado in (["--permission-prompt-tool", "stdio"], ["--permission-prompts", "host"],
                         ["--input-format", "stream-json"], ["--output-format", "stream-json"],
                         ["--session-id", chat["id"]], ["--model", "sonnet"], ["--effort", "high"],
                         ["--permission-mode", "acceptEdits"], ["--name", "Opciones"]):
            i = comando.index(esperado[0])
            self.assertEqual(comando[i:i + 2], esperado, comando)
        self.assertIn("--include-partial-messages", comando)
        chat["sesion_creada"] = True
        self.assertIn("--session-id", Turno(self.servicio, self.proyecto, chat, "hola").comando(),
                      "sin transcripción escrita (turno fallido al arrancar) no se reanuda: se vuelve a crear")
        (self.transcripciones / "p").mkdir(parents=True, exist_ok=True)
        (self.transcripciones / "p" / f"{chat['id']}.jsonl").write_text("", encoding="utf-8")
        self.assertIn("--resume", Turno(self.servicio, self.proyecto, chat, "hola").comando())

    def test_consola_y_remoto(self):
        chat = self.servicio.crear(self.proyecto, "Consola")
        def valor(comando, bandera):
            return comando[comando.index(bandera) + 1]
        comando = self.servicio.comando_consola(chat)
        self.assertEqual(valor(comando, "--session-id"), chat["id"])
        self.assertEqual(valor(comando, "--name"), "Consola")
        self.assertIn(str(REPO), comando, "la consola también ve el repositorio del Halo")
        chat["sesion_creada"] = True
        (self.transcripciones / "p").mkdir(parents=True, exist_ok=True)
        (self.transcripciones / "p" / f"{chat['id']}.jsonl").write_text("", encoding="utf-8")
        comando = self.servicio.comando_consola(chat, remoto=True)
        self.assertEqual(valor(comando, "--resume"), chat["id"])
        self.assertEqual(valor(comando, "--remote-control"), "Halo · Consola")
        self.servicio.banderas_consola = 0
        with patch("subprocess.Popen") as falso_popen:
            proceso = falso_popen.return_value
            proceso.wait.side_effect = lambda: time.sleep(0.2)
            proceso.poll.return_value = None
            r = self.servicio.consola(self.proyecto, chat["id"], remoto=True)
            self.assertEqual(r["estado"], "remoto")
            self.assertEqual(falso_popen.call_args.kwargs["creationflags"], 0)
            self.assertEqual(self.servicio.listar(self.proyecto)["chats"][0]["estado"], "remoto")
            with self.assertRaises(ErrorChat):
                self.servicio.enviar(self.proyecto, chat["id"], "hola")   # mientras está en el celular, aquí no se escribe
            with self.assertRaises(ErrorChat):
                self.servicio.quitar(self.proyecto, chat["id"])
            self.assertTrue(esperar(lambda: self.servicio.listar(self.proyecto)["chats"][0]["estado"] == "libre"))
        guardado = json.loads((self.proyecto / ".halo" / "chats.json").read_text(encoding="utf-8"))["chats"][0]
        self.assertTrue(guardado["sesion_creada"] and guardado["ultimo"])

    def test_transcripcion_real(self):
        carpeta = self.transcripciones / "C--proyecto"
        carpeta.mkdir(parents=True)
        lineas = [
            {"type": "user", "message": {"role": "user", "content": "muévelo"}, "timestamp": "t1"},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Voy."},
                                                                             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "python x.py"}}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "ok"}]}]}},
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "subagente"}},
            {"type": "queue-operation", "operation": "enqueue"},
        ]
        (carpeta / "abc.jsonl").write_text("\n".join(json.dumps(l) for l in lineas), encoding="utf-8")
        piezas = transcripcion.leer("abc", self.transcripciones)
        self.assertEqual([p["tipo"] for p in piezas], ["usuario", "asistente", "herramienta", "resultado"])
        self.assertEqual(piezas[2]["entrada"], "python x.py")
        self.assertEqual(piezas[3]["texto"], "ok")
        self.assertEqual(transcripcion.leer("no-existe", self.transcripciones), [])

    def test_api_http(self):
        centro = servidor.Centro(self.raiz / "estado")
        self.addCleanup(centro.observador.cerrar)
        centro.abrir(self.proyecto)
        with patch.object(servidor, "CENTRO", centro), patch.object(servidor, "CHAT", self.servicio):
            http = ThreadingHTTPServer(("127.0.0.1", 0), servidor.Manejador)
            hilo = threading.Thread(target=http.serve_forever, daemon=True)
            hilo.start()
            base = f"http://127.0.0.1:{http.server_port}"

            def pedir(ruta, cuerpo=None):
                datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
                req = Request(base + ruta, datos, {"Content-Type": "application/json", "X-Halo-Request": "1"})
                with urlopen(req, timeout=15) as r:
                    return json.load(r)
            pid = str(self.proyecto.resolve())
            try:
                lista = pedir("/api/chat?proyecto=" + pid.replace("\\", "%5C"))
                self.assertEqual(lista["chats"], [])
                r = pedir("/api/chat", {"op": "nuevo", "proyecto_id": pid, "titulo": "Desde HTTP", "permisos": "acceptEdits"})
                chat = r["chat"]
                pedir("/api/chat", {"op": "enviar", "proyecto_id": pid, "chat": chat, "texto": "hola"})
                self.assertTrue(esperar(self.turno_terminado(chat)))
                conv = pedir("/api/chat?proyecto=" + pid.replace("\\", "%5C") + "&chat=" + chat)
                self.assertEqual(conv["chat"]["permisos"], "acceptEdits")
                self.assertEqual([p["tipo"] for p in conv["piezas"]], ["usuario", "asistente"])
                self.assertFalse(conv["turno"]["activo"])
            finally:
                http.shutdown()
                http.server_close()
                hilo.join()


if __name__ == "__main__":
    unittest.main()
