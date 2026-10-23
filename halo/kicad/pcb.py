"""La placa por etapas, desde la línea de comandos (corre con el Python de KiCad).

    PY="%LOCALAPPDATA%/Programs/KiCad/10.0/bin/python.exe"
    $PY -m halo.kicad.pcb <proyecto> crear [--ancho MM --alto MM] [--capas 2|4|6|8]
    $PY -m halo.kicad.pcb <proyecto> abrir                        mostrar en el Halo una placa existente
    $PY -m halo.kicad.pcb <proyecto> perfil [--refs A,B]          altura, calor, señales rápidas, cara posible
    $PY -m halo.kicad.pcb <proyecto> acomodar [--refs A,B] [--doble-cara] [--altura-max MM] [--altura-abajo MM]
    $PY -m halo.kicad.pcb <proyecto> colocar REF X Y [ROT] [--abajo]
    $PY -m halo.kicad.pcb <proyecto> mover REF DX DY | rotar REF [GRADOS] | voltear REF | fijar REF
    $PY -m halo.kicad.pcb <proyecto> clase NOMBRE [--pista MM] [--holgura MM] [--via D/T] [--redes PATRON,PATRON]
    $PY -m halo.kicad.pcb <proyecto> ancho RED MM
    $PY -m halo.kicad.pcb <proyecto> capa In1.Cu GND              nombre propio de una capa
    $PY -m halo.kicad.pcb <proyecto> cobre RED CAPA[,CAPA] [--region placa|x0,y0,x1,y1|cerca:R1,R2|x,y,x,y,...]
                                           [--prioridad N] [--conexion termica|solida|ninguna] [--holgura MM]
                                           [--nombre TEXTO] [--plano]
    $PY -m halo.kicad.pcb <proyecto> plano RED CAPA [--conexion ...]  capa entera; no lleva pistas
    $PY -m halo.kicad.pcb <proyecto> zona RED CAPA                 relleno de toda una capa de señal
    $PY -m halo.kicad.pcb <proyecto> prohibir CAPA[,CAPA]|todas --region ... [--sin pistas,vias,cobre,huellas,pads]
    $PY -m halo.kicad.pcb <proyecto> quitar-cobre [--nombre N] [--red R] [--capa C]
    $PY -m halo.kicad.pcb <proyecto> rellenar | zonas
    $PY -m halo.kicad.pcb <proyecto> costura RED [--paso MM]       vías que cosen el cobre de la red entre capas
    $PY -m halo.kicad.pcb <proyecto> bajar RED                     cada pad SMD de la red baja a su plano
    $PY -m halo.kicad.pcb <proyecto> rutear [--motor nativo|freerouting] [--redes A,B] [--pasadas N]
                                           [--hilos N] [--plazo SEGUNDOS]
    $PY -m halo.kicad.pcb <proyecto> despejar [--redes A,B]
    $PY -m halo.kicad.pcb <proyecto> serigrafia                    aparta referencias y rótulos que el DRC marque encimados
    $PY -m halo.kicad.pcb <proyecto> verificar [--corregir-serigrafia] | estado

<proyecto> es la carpeta que contiene <nombre>.kicad_sch. Con --autor usuario la operación
viene del Halo: no cambia el estado del halo (la bitácora la escribe el editor).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import sexpr as sx
from .placa import Placa
from .sesion import Sesion

SOLO_LECTURA = ("estado", "zonas", "perfil")
TEXTO_HALO = {"crear": "creando la placa", "abrir": "abriendo la placa", "acomodar": "acomodando componentes",
              "rutear": "trazando pistas", "verificar": "KiCad revisa la placa", "cobre": "rellenando cobre",
              "plano": "rellenando cobre", "zona": "rellenando cobre", "costura": "cosiendo planos",
              "bajar": "bajando pads al plano", "rellenar": "rellenando cobre", "serigrafia": "apartando serigrafía"}
TEXTO_FIN = {"crear": "placa creada", "abrir": "placa abierta", "acomodar": "componentes acomodados", "rutear": "pistas trazadas"}


def _nombre(carpeta: Path) -> str:
    """Nombre del proyecto: el único .kicad_pro; si no lo hay, el único esquemático o la única placa."""
    for patron in ("*.kicad_pro", "*.kicad_sch", "*.kicad_pcb"):
        candidatos = sorted(carpeta.glob(patron))
        if len(candidatos) == 1:
            return candidatos[0].stem
        if len(candidatos) > 1:
            raise ValueError("Hay varios proyectos en la carpeta; indica el principal con --proyecto-nombre NOMBRE")
    raise ValueError(f"no hay esquemático ni placa en {carpeta}")


def _titulo(sch: Path, nombre: str) -> str:
    if not sch.is_file():
        return nombre
    raiz = sx.loads(sch.read_text(encoding="utf-8"))
    tb = sx.find(raiz, "title_block")
    return str(sx.val(tb, "title", nombre) or nombre) if tb else nombre


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("proyecto", type=Path)
    parser.add_argument("etapa")
    parser.add_argument("args", nargs="*")
    parser.add_argument("--ancho", type=float)
    parser.add_argument("--alto", type=float)
    parser.add_argument("--capas", type=int, default=2)
    parser.add_argument("--pausa", type=float, default=0)
    parser.add_argument("--corregir-serigrafia", action="store_true")
    parser.add_argument("--proyecto-nombre")
    parser.add_argument("--refs")
    parser.add_argument("--redes")
    parser.add_argument("--motor", choices=["nativo", "freerouting"], default="nativo")
    parser.add_argument("--pasadas", type=int, default=20)
    parser.add_argument("--hilos", type=int, default=1, help="hilos de Freerouting; 1 es reproducible")
    parser.add_argument("--plazo", type=float, default=None, help="segundos antes de cortar Freerouting")
    parser.add_argument("--autor", choices=["IA", "usuario"], default="IA")
    parser.add_argument("--doble-cara", action="store_true")
    parser.add_argument("--altura-max", type=float)
    parser.add_argument("--altura-abajo", type=float, default=3.0)
    parser.add_argument("--abajo", action="store_true")
    parser.add_argument("--region")
    parser.add_argument("--prioridad", type=int, default=0)
    parser.add_argument("--conexion", choices=["termica", "solida", "ninguna"], default="termica")
    parser.add_argument("--holgura", type=float)
    parser.add_argument("--nombre")
    parser.add_argument("--plano", action="store_true")
    parser.add_argument("--sin")
    parser.add_argument("--red")
    parser.add_argument("--capa")
    parser.add_argument("--paso", type=float, default=5.0)
    parser.add_argument("--pista", type=float)
    parser.add_argument("--via")
    a = parser.parse_args(argv)

    carpeta = a.proyecto.resolve()
    try:
        nombre = a.proyecto_nombre or _nombre(carpeta)
        if Path(nombre).name != nombre or '/' in nombre or '\\' in nombre or nombre in ('.', '..'):
            raise ValueError('Nombre de proyecto inválido')
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    sch, pcb = carpeta / f"{nombre}.kicad_sch", carpeta / f"{nombre}.kicad_pcb"
    usuario = a.autor == "usuario"
    sesion = None
    if not usuario and a.etapa not in SOLO_LECTURA:
        sesion = Sesion(f"{_titulo(sch, nombre)} · placa", pcb, etapa="pcb", pausa=a.pausa,
                        archivos={"esquematico": sch, "pcb": pcb} if sch.is_file() else {"pcb": pcb},
                        continuar=a.etapa not in ("crear", "abrir"))
        sesion.halo("actuando", TEXTO_HALO.get(a.etapa, a.etapa))
    redes = a.redes.split(",") if a.redes else None
    refs = a.refs.split(",") if a.refs else None
    try:
        if a.etapa == "crear":
            p = Placa(carpeta, nombre, sesion, capas=a.capas)
            p.crear(a.ancho, a.alto)
            resultado = {"contorno": p.contorno_mm, "capas": len(p.pila)}
        else:
            from . import cobre as cu
            p = Placa.abrir(carpeta, nombre, sesion)
            e, args = a.etapa, a.args
            if e == "abrir":
                zonas = cu.zonas(p)
                p.publicar()
                cobre_zonas = [z for z in zonas if not z["regla"]]
                p._paso(f"Abro la placa: {len(p.huellas)} huellas, {len(p.pila)} capas ({', '.join(p.nombres_capas())}), "
                        f"{len(p.pistas)} tramos, {len(p.vias)} vías, {len(cobre_zonas)} zonas de cobre"
                        + (f", planos {'; '.join(f'{c}: ' + ', '.join(r) for c, r in p.planos.items())}" if p.planos else ""), "abrir")
                resultado = {"capas": p.nombres_capas(), "huellas": len(p.huellas), "zonas": zonas, "planos": p.planos}
            elif e == "perfil":
                resultado = {r: pr.resumen() for r, pr in sorted(p.perfiles(a.altura_abajo).items()) if not refs or r in refs}
            elif e == "acomodar":
                resultado = p.acomodar(refs, doble_cara=a.doble_cara, altura_max=a.altura_max, altura_abajo=a.altura_abajo)
            elif e == "colocar":
                p.colocar(args[0], float(args[1]), float(args[2]), float(args[3]) if len(args) > 3 else 0,
                          lado="B" if a.abajo else None)
                resultado = {"ok": True}
            elif e == "mover":
                resultado = {"ok": True, "quitadas": p.mover(args[0], float(args[1]), float(args[2]), autor=a.autor)}
            elif e == "rotar":
                resultado = {"ok": True, "quitadas": p.rotar(args[0], float(args[1]) if len(args) > 1 else 90, autor=a.autor)}
            elif e == "voltear":
                resultado = {"ok": True, "quitadas": p.voltear(args[0], autor=a.autor)}
            elif e == "fijar":
                p.fijar(args[0])
                resultado = {"ok": True}
            elif e == "clase":
                via = tuple(float(v) for v in a.via.split("/")) if a.via else None
                resultado = p.definir_clase(args[0], a.pista, a.holgura, via, redes or ())
            elif e == "ancho":
                p.fijar_ancho(args[0], float(args[1]))
                resultado = {"anchos": p.anchos}
            elif e == "capa":
                p.nombrar_capa(args[0], args[1])
                resultado = {"capas": p.nombres_capas()}
            elif e == "cobre":
                resultado = p.cobre(args[0], args[1].split(","), a.region, a.prioridad, a.conexion, a.holgura,
                                    a.nombre or "", a.plano)
            elif e == "plano":
                resultado = p.plano(args[0], args[1], a.conexion)
            elif e == "zona":
                resultado = p.zona(args[0], args[1] if len(args) > 1 else "B.Cu")
            elif e == "prohibir":
                capas = p.capas_cobre() if args[0] == "todas" else args[0].split(",")
                sin = tuple(a.sin.split(",")) if a.sin else ("pistas", "vias", "cobre")
                resultado = p.prohibir(capas, a.region, sin, a.nombre or "")
            elif e == "quitar-cobre":
                resultado = p.quitar_cobre(a.nombre, a.red, a.capa)
            elif e == "rellenar":
                resultado = {"zonas": p.rellenar()}
            elif e == "zonas":
                resultado = cu.zonas(p)
            elif e == "costura":
                resultado = p.costura(args[0] if args else "GND", a.paso)
            elif e == "bajar":
                resultado = p.bajar_a_plano(args[0])
            elif e == "rutear":
                resultado = (p.rutear_freerouting(redes, a.pasadas, a.hilos, a.plazo)
                             if a.motor == "freerouting" else p.rutear(redes))
            elif e == "despejar":
                if redes:
                    for red in redes:
                        p.despejar(red)
                else:
                    p.despejar()
                resultado = {"pistas": len(p.pistas)}
            elif e == "serigrafia":
                resultado = p.corregir_serigrafia()
            elif e == "verificar":
                d = p.verificar(corregir_serigrafia=a.corregir_serigrafia)
                resultado = {k: d[k] for k in ("drc", "pendientes", "paridad", "pistas", "vias", "capas")}
                resultado["violaciones"] = [f"{v.get('severity')} {v.get('type')}: {v.get('description')}" for v in d["violaciones"]]
            elif e == "estado":
                resultado = {"etapa": p.etapa, "capas": p.nombres_capas(), "contorno": p.contorno_mm, "colocadas": len(p.colocadas),
                             "abajo": sorted(r for r in p.colocadas if p.lado(p.huellas[r]) == "B"), "fijadas": sorted(p.fijadas),
                             "planos": p.planos, "pistas": len(p.pistas), "vias": len(p.vias),
                             "zonas": sum(1 for z in cu.zonas(p) if not z["regla"]),
                             "longitud_pendiente_mm": round(p.longitud_conexiones(), 1)}
            else:
                raise SystemExit(f"etapa desconocida: {a.etapa}")
        if sesion and a.etapa != "verificar":
            sesion.halo("reposo", TEXTO_FIN.get(a.etapa, "listo"))
        print(json.dumps(resultado, ensure_ascii=False, default=str))
        return 0
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, IndexError) as exc:
        if sesion:
            sesion.evento(str(exc), a.etapa, nivel="error")
            sesion.halo("error", "no se pudo")
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
