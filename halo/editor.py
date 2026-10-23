"""Edición desde el Halo: los cambios que el usuario hace a mano sobre el trabajo de la IA.

Esquemático: cada edición abre el archivo real y aplica la operación con el mismo motor que
usa la IA (cables re-trazados, rejilla, redes reconstruidas).
Placa: la operación corre en el Python de KiCad (pcbnew) con el motor de placa; mover una
huella quita las pistas de sus redes, que vuelven a verse como conexiones pendientes.
En ambos casos se guarda una copia previa para deshacer y queda un evento con autor
"usuario" en la bitácora. No se edita mientras la IA trabaja.
El 3D se genera sólo cuando se pide, y se guarda por versión de la placa.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import sys
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from .kicad.biblioteca import KICAD_DIR
from .kicad.esquematico import Esquematico
from .kicad.oraculo import CLI
from .kicad.sesion import escribir_atomico
from .kicad.proyecto import bloquear, comprobar, revision, captura, restaurar, ConflictoProyecto

REPO = Path(__file__).resolve().parents[1]
KICAD_PY = KICAD_DIR / "bin" / "python.exe"
CERROJO = threading.Lock()
OCUPADO = ("actuando", "verificando", "pensando")


class ErrorEdicion(Exception):
    pass


def _estado_ruta(carpeta: Path) -> Path:
    return Path(carpeta) / ".halo" / "actual.json"


def _actual(carpeta: Path) -> dict:
    ruta = _estado_ruta(carpeta)
    if ruta.is_file():
        return json.loads(ruta.read_text(encoding="utf-8"))
    from .proyectos import estado_inicial
    return estado_inicial(carpeta)


def _actualizar_actual(carpeta: Path, **campos) -> None:
    datos = _actual(carpeta)
    datos.update(campos)
    escribir_atomico(_estado_ruta(carpeta), json.dumps(datos, ensure_ascii=False, indent=2))


def _carpeta_de(peticion: dict) -> Path:
    pid = peticion.get('proyecto_id')
    if not isinstance(pid, str) or not pid:
        raise ErrorEdicion('La petición no indica el proyecto.')
    carpeta = Path(pid)
    if not carpeta.is_dir():
        raise ErrorEdicion('La carpeta del proyecto ya no existe.')
    return carpeta.resolve()


def _evento(actual: dict, texto: str, op: str, nivel: str = "") -> None:
    bitacora = actual.get("actividad")
    if not bitacora:
        return
    linea = {"t": int(time.time() * 1000), "recibo": None, "op": op, "texto": texto, "nivel": nivel, "autor": "usuario"}
    with open(bitacora, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(linea, ensure_ascii=False) + "\n")


def _copia_previa(ruta: Path, historial: Path) -> Path:
    historial.mkdir(parents=True, exist_ok=True)
    copia = historial / f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}{ruta.suffix}"
    shutil.copy2(ruta, copia)
    for vieja in sorted(historial.glob(f"*{ruta.suffix}"))[:-50]:
        vieja.unlink()
    return copia


def _sha(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _sellar(copia: Path, ruta: Path) -> None:
    """Anota cómo quedó el archivo tras tu cambio: deshacer sólo vale si nadie (la IA, KiCad) lo tocó después."""
    if copia.exists():
        copia.with_suffix(".sha").write_text(_sha(ruta), encoding="utf-8")


def _vaciar(historial: Path, sufijo: str) -> None:
    for c in historial.glob(f"*{sufijo}"):
        for extra in (c, c.with_suffix(".json"), c.with_suffix(".sha")):
            extra.unlink(missing_ok=True)


def _deshacer(actual: dict, ruta: Path, historial: Path, texto: str) -> dict:
    copias = sorted(historial.glob(f"*{ruta.suffix}")) if historial.is_dir() else []
    if not copias:
        raise ErrorEdicion("No hay cambios tuyos para deshacer.")
    sello = copias[-1].with_suffix(".sha")
    if sello.is_file() and sello.read_text(encoding="utf-8").strip() != _sha(ruta):
        # Desde tu último cambio el archivo lo tocó otro (la IA o KiCad): tu pila de deshacer se reinicia ahí.
        _vaciar(historial, ruta.suffix)
        raise ErrorEdicion("Desde tu último cambio trabajó la IA (o KiCad): la pila de deshacer se reinició; "
                           "Ctrl+Z sólo deshace lo que hagas de aquí en adelante.")
    shutil.copy2(copias[-1], ruta)
    copias[-1].unlink()
    sello.unlink(missing_ok=True)
    estado = copias[-1].with_suffix(".json")          # estado de etapa de la placa, si se guardó
    if estado.is_file():
        shutil.copy2(estado, ruta.parent / ".halo" / "placa.json")
        estado.unlink()
        _publicar_placa(ruta)
    _evento(actual, texto, "deshacer")
    return {"ok": True}


def _publicar_placa(ruta: Path) -> None:
    """Lleva al panel del Halo la etapa, capas, tamaño y planos que guardó el motor de placa."""
    archivo = ruta.parent / ".halo" / "placa.json"
    if not archivo.is_file():
        return
    datos = json.loads(archivo.read_text(encoding="utf-8"))
    c = datos.get("contorno") or [0, 0, 0, 0]
    _actualizar_actual(ruta.parent, placa={"etapa": datos.get("etapa"), "capas": datos.get("capas"),
                                           "tamano": [round(c[2] - c[0], 2), round(c[3] - c[1], 2)],
                                           "planos": datos.get("planos", {})})


def editar(peticion: dict) -> dict:
    # Run PCB edits with KiCad's interpreter, including lock, backup and commit.
    # The worker calls the engine in-process so the parent never holds a lock the child needs.
    if peticion.get('etapa') == 'pcb' and Path(sys.executable).resolve() != KICAD_PY.resolve():
        try:
            r = subprocess.run([str(KICAD_PY), '-m', 'halo.editor'], input=json.dumps(peticion),
                               capture_output=True, text=True, cwd=REPO, timeout=1000, encoding='utf-8')
            data = json.loads(r.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise ErrorEdicion(f'No se pudo completar la edición de placa: {exc}') from exc
        if r.returncode or not data.get('ok'):
            raise ErrorEdicion(data.get('error', 'Falló el motor de placa'))
        return data
    with CERROJO:
        carpeta = _carpeta_de(peticion)
        actual = _actual(carpeta)
        estado = (actual.get("halo") or {}).get("estado", "reposo")
        if estado in OCUPADO:
            raise ErrorEdicion("La IA está trabajando; espera a que termine para editar.")
        etapa = 'pcb' if peticion.get('etapa') == 'pcb' else 'esquematico'
        archivo = (actual.get('archivos') or {}).get(etapa)
        if not archivo:
            raise ErrorEdicion('No hay un proyecto abierto en esta etapa.')
        if Path(archivo).resolve().parent != carpeta:
            raise ErrorEdicion('Cambió el proyecto abierto; actualiza la vista.')
        try:
            with bloquear(carpeta):
                comprobar(carpeta, peticion.get('expected_revision'))
                copia = captura(carpeta)
                try:
                    respuesta = _editar_placa(actual, peticion) if etapa == 'pcb' else _editar_esquematico(actual, peticion)
                except BaseException:
                    restaurar(carpeta, copia)
                    raise
                nueva = revision(carpeta)
                campos = {'revision': nueva}
                if peticion.get('op') != 'verificar':
                    campos.update(verificacion=None, render3d=[])
                _actualizar_actual(carpeta, **campos)
                return {**respuesta, 'revision': nueva}
        except ConflictoProyecto as exc:
            raise ErrorEdicion(str(exc)) from exc


_MOTORES: dict[str, tuple[tuple[int, int], "Esquematico"]] = {}
_MOTORES_MUTEX = threading.Lock()


def _firma(ruta: Path) -> tuple[int, int]:
    st = ruta.stat()
    return (st.st_mtime_ns, st.st_size)


def _motor(ruta: Path) -> "Esquematico":
    """Motor caliente por archivo: abrir una placa grande cuesta 0,7 s y cada tecla lo abría de nuevo. Se reutiliza
    mientras el archivo sea el mismo (mtime y tamaño); si la IA, KiCad u otro lo tocaron, se vuelve a abrir."""
    clave = os.path.normcase(str(ruta))
    firma = _firma(ruta)
    with _MOTORES_MUTEX:
        guardado = _MOTORES.get(clave)
        if guardado and guardado[0] == firma and not getattr(guardado[1], "_invalido", False):
            return guardado[1]
        e = Esquematico.abrir(ruta)
        _MOTORES[clave] = (firma, e)
        return e


def _guardar_motor(e: "Esquematico", ruta: Path) -> None:
    e.guardar()
    with _MOTORES_MUTEX:
        _MOTORES[os.path.normcase(str(ruta))] = (_firma(ruta), e)


def _editar_esquematico(actual: dict, peticion: dict) -> dict:
    archivo = (actual.get("archivos") or {}).get("esquematico")
    if not archivo or not Path(archivo).is_file():
        raise ErrorEdicion("No hay un esquemático abierto.")
    ruta = Path(archivo)
    historial = ruta.parent / ".halo" / "historial"
    op = peticion.get("op")

    if op == "deshacer":
        return _deshacer(actual, ruta, historial, "Deshiciste tu último cambio")

    if op == "verificar":
        e = _motor(ruta)
        detalle = e.verificar(actualizar_intencion=False)
        resultado = {k: detalle[k] for k in ("erc", "netlist", "reglas", "erc_detalle")}
        _actualizar_actual(ruta.parent, verificacion={**resultado, "cuando": int(time.time() * 1000), 'revision': revision(ruta.parent)})
        conexiones = ("las conexiones siguen idénticas" if detalle["netlist"]["coinciden"]
                      else f"{detalle['netlist']['diferencias']} conexiones cambiaron")
        ok = detalle["erc"]["errores"] == 0 and detalle["netlist"]["coinciden"]
        _evento(actual, f"Verificaste tus cambios: ERC {detalle['erc']['errores']} errores, {conexiones}, "
                        f"{detalle['reglas']['problemas']} problemas de dibujo", "verificar",
                "verificacion" if ok else "error")
        return {"ok": True, "resultado": resultado}

    copia = _copia_previa(ruta, historial)
    try:
        e = _motor(ruta)
        dx, dy = float(peticion.get("dx", 0)), float(peticion.get("dy", 0))
        if op == "mover":
            e.mover(peticion["clave"], dx, dy, autor="usuario")
            texto = f"Moviste {e._buscar(peticion['clave']).ref}"
        elif op == "rotar":
            e.rotar(peticion["clave"], int(peticion.get("grados", 90)), autor="usuario")
            texto = f"Rotaste {e._buscar(peticion['clave']).ref}"
        elif op == "mover_etiqueta":
            e.mover_etiqueta(peticion["clave"], dx, dy, autor="usuario")
            texto = "Moviste una etiqueta; su cable se volvió a trazar"
        elif op == "mover_bloque":
            e.mover_bloque(peticion["clave"], dx, dy, autor="usuario")
            bloque = next((b for b in e.bloques if b["uuid"] == peticion["clave"]), None)
            texto = f"Moviste el bloque «{bloque['titulo'] if bloque else ''}» con todo su contenido"
        elif op == "mover_campo":
            e.mover_campo(peticion["clave"], peticion["campo"], dx, dy, autor="usuario")
            texto = f"Moviste un texto de {e._buscar(peticion['clave']).ref}"
        elif op == "espejar":
            e.espejar(peticion["clave"], peticion.get("eje", "y"), autor="usuario")
            texto = f"Volteaste {e._buscar(peticion['clave']).ref} en {'horizontal' if peticion.get('eje', 'y') == 'y' else 'vertical'}"
        elif op == "rotar_etiqueta":
            e.rotar_etiqueta(peticion["clave"], int(peticion.get("grados", 90)), autor="usuario")
            texto = "Rotaste una etiqueta sobre su punto de conexión"
        elif op == "mover_cable":
            e.mover_cable(peticion["clave"], dx, dy, autor="usuario")
            texto = "Moviste un cable; sus vecinos lo siguieron"
        elif op == "trazar_cable":
            red = e.trazar_cable(peticion.get("puntos") or [], autor="usuario")
            texto = f"Trazaste un cable en la red {red}"
        elif op == "borrar_cable":
            e.borrar_cable(peticion["clave"], autor="usuario")
            texto = "Borraste un cable"
        else:
            raise ErrorEdicion(f"Operación desconocida: {op}")
        _guardar_motor(e, ruta)
    except (KeyError, ValueError, RuntimeError) as exc:
        _MOTORES.pop(os.path.normcase(str(ruta)), None)   # tras un fallo, la próxima vez se abre limpio
        copia.unlink(missing_ok=True)
        raise ErrorEdicion(str(exc)) from exc
    _sellar(copia, ruta)
    _evento(actual, texto, op)
    return {"ok": True}


def _motor_placa(ruta: Path, argumentos: list[str]) -> dict:
    """Corre una etapa de halo.kicad.pcb con el Python de KiCad y devuelve su JSON."""
    from .kicad.pcb import main
    salida, errores = io.StringIO(), io.StringIO()
    with redirect_stdout(salida), redirect_stderr(errores):
        codigo = main([str(ruta.parent), *argumentos, '--autor', 'usuario', '--proyecto-nombre', ruta.stem])
    r = subprocess.CompletedProcess(argumentos, codigo, salida.getvalue(), errores.getvalue())
    if r.returncode != 0:
        lineas = (r.stderr or "").strip().splitlines()
        try:
            mensaje = json.loads(lineas[-1])["error"]
        except (IndexError, ValueError, KeyError):
            mensaje = ((r.stderr or r.stdout).strip()[-400:]) or f"el motor de placa falló ({r.returncode})"
        raise ErrorEdicion(mensaje)
    return json.loads(r.stdout.strip().splitlines()[-1])


def _editar_placa(actual: dict, peticion: dict) -> dict:
    archivo = (actual.get("archivos") or {}).get("pcb")
    if not archivo or not Path(archivo).is_file():
        raise ErrorEdicion("Todavía no hay placa.")
    ruta = Path(archivo)
    historial = ruta.parent / ".halo" / "historial-pcb"
    op = peticion.get("op")

    if op == "deshacer":
        return _deshacer(actual, ruta, historial, "Deshiciste tu último cambio en la placa")

    if op == "verificar":
        datos = _motor_placa(ruta, ["verificar"])
        resultado = {k: datos[k] for k in ("drc", "pendientes", "paridad", "pistas", "vias", "capas")}
        _actualizar_actual(ruta.parent, verificacion={**resultado, "cuando": int(time.time() * 1000), 'revision': revision(ruta.parent)})
        _publicar_placa(ruta)
        ok = resultado["drc"]["errores"] == 0 and not resultado["pendientes"] and not resultado["paridad"]
        paridad = "idéntica" if not resultado["paridad"] else f"{resultado['paridad']} diferencias"
        _evento(actual, f"Verificaste la placa: DRC {resultado['drc']['errores']} errores, "
                        f"{resultado['pendientes']} sin conectar, paridad {paridad}", "verificar",
                "verificacion" if ok else "error")
        return {"ok": True, "resultado": resultado}

    if op == "serigrafia":
        copia = _copia_previa(ruta, historial)
        estado = ruta.parent / ".halo" / "placa.json"
        if estado.is_file():
            shutil.copy2(estado, copia.with_suffix(".json"))
        try:
            datos = _motor_placa(ruta, ["serigrafia"])
        except ErrorEdicion:
            copia.unlink(missing_ok=True)
            copia.with_suffix(".json").unlink(missing_ok=True)
            raise
        _publicar_placa(ruta)
        _sellar(copia, ruta)
        resultado = {k: int(datos.get(k, 0)) for k in ("marcadas", "referencias", "rotulos")}
        if resultado["marcadas"]:
            texto = (f"Apartaste serigrafía encimada: {resultado['referencias']} referencias y "
                     f"{resultado['rotulos']} rótulos de {resultado['marcadas']} marcados por KiCad")
        else:
            texto = "Pediste corregir la serigrafía: KiCad no marca nada encimado"
            copia.unlink(missing_ok=True)
            copia.with_suffix(".json").unlink(missing_ok=True)
            copia.with_suffix(".sha").unlink(missing_ok=True)
        _evento(actual, texto, op)
        return {"ok": True, "resultado": resultado}

    ref = str(peticion.get("clave", ""))
    if op == "mover":
        argumentos = ["mover", ref, f"{float(peticion.get('dx', 0)):.4f}", f"{float(peticion.get('dy', 0)):.4f}"]
        texto = f"Moviste {ref} en la placa"
    elif op == "rotar":
        argumentos = ["rotar", ref, str(int(peticion.get("grados", 90)))]
        texto = f"Rotaste {ref} en la placa"
    elif op == "voltear":
        argumentos = ["voltear", ref]
        texto = f"Pasaste {ref} a la otra cara"
    else:
        raise ErrorEdicion(f"Operación desconocida en la placa: {op}")
    copia = _copia_previa(ruta, historial)
    estado = ruta.parent / ".halo" / "placa.json"
    if estado.is_file():
        shutil.copy2(estado, copia.with_suffix(".json"))
    try:
        datos = _motor_placa(ruta, argumentos)
    except ErrorEdicion:
        copia.unlink(missing_ok=True)
        copia.with_suffix(".json").unlink(missing_ok=True)
        raise
    _publicar_placa(ruta)
    _sellar(copia, ruta)
    if datos.get("quitadas"):
        texto += f"; sus pistas de {', '.join(datos['quitadas'])} quedan como conexiones pendientes"
    _evento(actual, texto, op)
    return {"ok": True}


def render3d(peticion: dict) -> dict:
    """Renders 3D de KiCad para la versión actual de la placa, reutilizando los ya hechos."""
    with CERROJO:
        carpeta_proyecto = _carpeta_de(peticion)
        actual = _actual(carpeta_proyecto)
        archivo = (actual.get("archivos") or {}).get("pcb")
        if not archivo or not Path(archivo).is_file():
            raise ErrorEdicion("Todavía no hay placa.")
        ruta = Path(archivo)
        version = ruta.stat().st_mtime_ns
        carpeta = ruta.parent / ".halo"
        carpeta.mkdir(parents=True, exist_ok=True)
        vistas = (("arriba", ["--side", "top"]),
                  ("perspectiva", ["--rotate", "-40,0,-30", "--perspective", "--zoom", "0.9"]))
        imagenes, generadas = [], 0
        for nombre, extra in vistas:
            destino = carpeta / f"3d-{version}-{nombre}.png"
            if not destino.is_file():
                subprocess.run([str(CLI), "pcb", "render", "-w", "1600", "-h", "1000", "--background", "opaque",
                                "--quality", "high", *extra, "-o", str(destino), str(ruta)],
                               capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
                generadas += 1
            if destino.is_file():
                imagenes.append(str(destino))
        for vieja in carpeta.glob("3d-*.png"):
            if str(vieja) not in imagenes:
                vieja.unlink(missing_ok=True)
        _actualizar_actual(carpeta_proyecto, render3d=imagenes)
        if generadas:
            _evento(actual, f"Generé la vista 3D a pedido ({len(imagenes)} vistas)", "render3d", "verificacion")
        return {"ok": True, "imagenes": imagenes, "cache": generadas == 0}


if __name__ == '__main__':
    try:
        respuesta = editar(json.loads(sys.stdin.read()))
        print(json.dumps(respuesta, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
