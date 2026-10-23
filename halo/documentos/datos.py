"""Lo que un proyecto sabe de sí mismo, reunido para documentarlo: esquemático, verificación, simulación,
placa, fabricación y renders. Sólo lectura; cada bloque lleva la revisión de su fuente para saber después
si la documentación sigue al día.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .. import proyectos
from ..kicad.vista import cargar_modelo
from ..kicad.vista_pcb import cargar_placa

FUENTES = ("esquematico", "simulacion", "pcb", "fabricacion")


def _json(ruta: Path):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha(*partes: bytes) -> str:
    h = hashlib.sha256()
    for p in partes:
        h.update(p)
        h.update(b"\0")
    return h.hexdigest()[:12]


_digestos: dict[str, tuple[tuple[int, int], str]] = {}


def _sha_archivo(ruta: Path | None) -> str:
    """SHA-256 abreviado de un archivo, recordado por (tamaño, mtime): se consulta en cada listado."""
    if not ruta or not ruta.is_file():
        return "ausente"
    st = ruta.stat()
    clave, firma = str(ruta), (st.st_size, st.st_mtime_ns)
    previo = _digestos.get(clave)
    if previo and previo[0] == firma:
        return previo[1]
    valor = _sha(ruta.read_bytes())
    _digestos[clave] = (firma, valor)
    return valor


def _simulaciones(carpeta: Path) -> list[dict]:
    """Trabajos de simulación del proyecto, del más nuevo al más viejo, con sus medidas."""
    try:
        from ..simulacion.servicio import SERVICIO
    except Exception:  # noqa: BLE001 - la simulación es opcional para documentar
        return []
    trabajos = []
    for meta in SERVICIO.listar(str(carpeta)):
        if meta.get("proyecto_id") != str(carpeta) or meta.get("ejemplo"):
            continue
        try:
            completo = SERVICIO.leer(meta["id"])
        except (OSError, ValueError, KeyError):
            completo = meta
        resultado = completo.get("resultado") or {}
        analisis = completo.get("analisis")
        creado = completo.get("creado")
        trabajos.append({"id": completo.get("id"), "nombre": completo.get("nombre"), "estado": completo.get("estado"),
                         "vigencia": completo.get("vigencia"),
                         "analisis": analisis.get("tipo") if isinstance(analisis, dict) else analisis,
                         "creado": time.strftime("%Y-%m-%d %H:%M", time.localtime(creado)) if isinstance(creado, (int, float)) else creado,
                         "criterios": resultado.get("criterios"),
                         "medidas": [{k: m.get(k) for k in ("id", "sonda", "metrica", "valor", "unidad", "estado", "min", "max")}
                                     for m in resultado.get("medidas", [])]})
    return trabajos


def _fabricaciones(carpeta: Path) -> list[dict]:
    try:
        from .. import fabricacion
    except Exception:  # noqa: BLE001
        return []
    salidas = []
    for meta in sorted(fabricacion.SALIDA.glob("*/informe.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        datos = _json(meta) or {}
        if datos.get("proyecto_id") == str(carpeta):
            archivos = datos.get("archivos") or []
            salidas.append({"id": datos.get("id"), "revision": datos.get("revision"),
                            "fecha": time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.stat().st_mtime)),
                            "archivos": len(archivos), "drc": datos.get("drc"), "veredicto": datos.get("veredicto")})
    return salidas


def recopilar(carpeta: Path) -> dict:
    carpeta = Path(carpeta).resolve()
    archivos = proyectos.archivos_de(carpeta)
    sch = Path(archivos["esquematico"]) if archivos.get("esquematico") else None
    pcb = Path(archivos["pcb"]) if archivos.get("pcb") else None
    halo = carpeta / ".halo"
    datos = {"carpeta": str(carpeta), "nombre": proyectos.nombre_de(carpeta), "fecha": time.strftime("%Y-%m-%d"),
             "revisiones": {}, "esquematico": None, "pcb": None, "simulacion": [], "fabricacion": [], "renders": []}

    if sch and sch.is_file():
        modelo = cargar_modelo(sch)
        fichas = {f.get("rectangulo_uuid"): f for f in ((_json(halo / "bloques.json") or {}).get("bloques") or [])}
        bloques = []
        for b in modelo.get("bloques", []):
            ficha = fichas.get(b["uuid"]) or {}
            dentro = [s for s in modelo["simbolos"] if not s.get("power") and b["a"][0] <= s["at"][0] <= b["b"][0] and b["a"][1] <= s["at"][1] <= b["b"][1]]
            bloques.append({"titulo": b.get("titulo") or "Bloque", "funcion": ficha.get("funcion", ""),
                            "decisiones": ficha.get("decisiones", []), "pendientes": ficha.get("pendientes", []),
                            "referencias": sorted({s["ref"] for s in dentro})})
        verificacion = _json(halo / "verificacion.json") or {}
        datos["esquematico"] = {
            "archivo": sch.name, "titulo": modelo.get("titulo") or sch.stem,
            "componentes": sorted(({"ref": s["ref"], "valor": s.get("valor", ""), "simbolo": s.get("lib_id", "")}
                                   for s in modelo["simbolos"] if not s.get("power") and s.get("unidad", 1) == 1),
                                  key=lambda c: (c["ref"].rstrip("0123456789"), int(c["ref"][len(c["ref"].rstrip("0123456789")):] or 0))),
            "etiquetas": sorted({e["texto"] for e in modelo.get("etiquetas", []) if e.get("tipo") == "global"}),
            "cables": len(modelo.get("cables") or []), "bloques": bloques,
            "verificacion": {"erc": verificacion.get("erc"), "netlist": verificacion.get("netlist"), "reglas": verificacion.get("reglas")},
        }
        datos["revisiones"]["esquematico"] = _sha(_sha_archivo(sch).encode(), json.dumps(verificacion, sort_keys=True).encode())
    else:
        datos["revisiones"]["esquematico"] = "ausente"

    if pcb and pcb.is_file():
        placa = cargar_placa(pcb)
        estado = _json(halo / "placa.json") or {}
        drc = _json(halo / "verificacion-pcb.json") or {}
        lados = {"F": 0, "B": 0}
        for h in placa.get("huellas", []):
            lados[h.get("lado", "F")] = lados.get(h.get("lado", "F"), 0) + 1
        contorno = estado.get("contorno") or placa.get("limites")
        tamano = estado.get("tamano") or ([round(contorno[2] - contorno[0], 2), round(contorno[3] - contorno[1], 2)] if contorno and len(contorno) == 4 else None)
        datos["pcb"] = {
            "archivo": pcb.name, "etapa": estado.get("etapa"), "capas": placa.get("capas") or estado.get("capas"),
            "tamano": tamano, "huellas": len(placa.get("huellas", [])), "arriba": lados.get("F", 0), "abajo": lados.get("B", 0),
            "pistas": len(placa.get("pistas", [])), "vias": len(placa.get("vias", [])),
            "zonas": [{"red": z.get("red"), "capa": z.get("capa")} for z in placa.get("zonas", [])],
            "planos": estado.get("planos") or {}, "anchos": estado.get("anchos") or {},
            "drc": ({"errores": (drc.get("drc") or {}).get("errores"), "avisos": (drc.get("drc") or {}).get("avisos"),
                     "pendientes": drc.get("pendientes"), "paridad": drc.get("paridad")} if drc else None),
        }
        datos["revisiones"]["pcb"] = _sha(_sha_archivo(pcb).encode(), json.dumps(drc, sort_keys=True).encode())
        datos["renders"] = [p.name for p in sorted(halo.glob("3d-*.png"))]
    else:
        datos["revisiones"]["pcb"] = "ausente"

    datos["simulacion"] = _simulaciones(carpeta)
    datos["revisiones"]["simulacion"] = _sha(json.dumps([(t["id"], t["estado"], t["criterios"]) for t in datos["simulacion"]]).encode()) if datos["simulacion"] else "ausente"
    datos["fabricacion"] = _fabricaciones(carpeta)
    datos["revisiones"]["fabricacion"] = _sha(json.dumps([(f["id"], f["revision"]) for f in datos["fabricacion"]]).encode()) if datos["fabricacion"] else "ausente"
    return datos


def revisiones(carpeta: Path) -> dict:
    """Sólo las revisiones de cada fuente, sin cargar los modelos completos (para saber qué está desactualizado)."""
    carpeta = Path(carpeta).resolve()
    archivos = proyectos.archivos_de(carpeta)
    halo = carpeta / ".halo"
    sch = Path(archivos["esquematico"]) if archivos.get("esquematico") else None
    pcb = Path(archivos["pcb"]) if archivos.get("pcb") else None
    r = {}
    if sch and sch.is_file():
        r["esquematico"] = _sha(_sha_archivo(sch).encode(), json.dumps(_json(halo / "verificacion.json") or {}, sort_keys=True).encode())
    else:
        r["esquematico"] = "ausente"
    if pcb and pcb.is_file():
        r["pcb"] = _sha(_sha_archivo(pcb).encode(), json.dumps(_json(halo / "verificacion-pcb.json") or {}, sort_keys=True).encode())
    else:
        r["pcb"] = "ausente"
    sims = _simulaciones(carpeta)
    r["simulacion"] = _sha(json.dumps([(t["id"], t["estado"], t["criterios"]) for t in sims]).encode()) if sims else "ausente"
    fabs = _fabricaciones(carpeta)
    r["fabricacion"] = _sha(json.dumps([(f["id"], f["revision"]) for f in fabs]).encode()) if fabs else "ausente"
    return r
