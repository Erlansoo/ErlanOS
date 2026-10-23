"""Motor nativo de placas por etapas: el motor de KiCad (pcbnew) desde dentro, con la IA al mando.

Etapas, cada una cuando el usuario la pide:
  crear      contorno del tamaño pedido (o estimado por el área de los componentes), 2/4/6 capas,
             huellas del esquemático en espera junto a la placa
  acomodar   colocación con oficio (acomodo.py): módulos y flujo del esquemático, conectores al
             borde, desacoplo junto al pin, calor, altura de los modelos 3D, señales rápidas y dos
             caras; la IA puede fijar, colocar, rotar o voltear cualquier pieza
  cobre      zonas por red y capa con prioridad (planos divididos), áreas prohibidas, costura y
             bajadas a plano (cobre.py), rellenadas al momento para verlas en el Halo
  rutear     enrutador propio (N capas, vías, 45°, reglas por clase de red, arrancar y reintentar) o
             Freerouting supervisado; se puede rutear por partes (lista de redes)
  verificar  DRC de KiCad con paridad contra el esquemático; si marca serigrafía, la corrige y repite

Corre con el Python de KiCad (trae pcbnew). Línea de comandos: halo/kicad/pcb.py.
"""
from __future__ import annotations

import heapq
import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pcbnew

from . import sexpr as sx
from .biblioteca import HUELLAS
from .clases import Clases, Regla
from .oraculo import CLI
from .oraculo import netlist as exportar_netlist
from .sesion import Sesion
from .verificador import informe_json
from .proyecto import revision, proteger_motor

G = 0.254
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
POTENCIA = ("GND", "GNDA", "GNDD", "VCC", "VDD", "VSS", "VBAT", "VIN")


def V(x: float, y: float):
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


def mm(v) -> float:
    return pcbnew.ToMM(v)


def pila_de_cobre(n: int) -> list[int]:
    return [pcbnew.F_Cu] + [getattr(pcbnew, f"In{i}_Cu") for i in range(1, n - 1)] + [pcbnew.B_Cu]


def es_potencia(red: str) -> bool:
    nombre = red.upper().lstrip("/")
    return nombre in POTENCIA or nombre.startswith(("+", "-")) or nombre.startswith(("VCC", "VDD", "GND"))


def dist_segmento(x, y, ax, ay, bx, by) -> float:
    vx, vy = bx - ax, by - ay
    l2 = vx * vx + vy * vy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / l2))
    return math.hypot(x - (ax + t * vx), y - (ay + t * vy))


def rotar_punto(x, y, ang):
    c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    return (x * c + y * s, -x * s + y * c)


def _toca_forma(raster, f) -> bool:
    """¿El cobre relleno toca este pad? Con alivio térmico el centro queda en el hueco: se miran puntos junto al borde."""
    if not raster.cerca(f.cx, f.cy, f.radio + 1):
        return False
    if raster.dentro(f.cx, f.cy):
        return True
    c, s = math.cos(f.ang), math.sin(f.ang)
    for g in (0.1, 0.3):
        hx, hy = f.hw + g, f.hh + g
        for lx, ly in ((hx, 0), (-hx, 0), (0, hy), (0, -hy), (hx, hy), (-hx, hy), (hx, -hy), (-hx, -hy)):
            if raster.dentro(f.cx + lx * c + ly * s, f.cy - lx * s + ly * c):
                return True
    return False


class FormaPad:
    """Cobre de un pad en milímetros: rectángulo girado o círculo, con sus capas (índices de la pila)."""

    def __init__(self, ref: str, pad, pila: list[int]):
        pos = pad.GetPosition()
        self.ref = ref
        self.numero = pad.GetNumber()
        self.red = pad.GetNetname()
        self.cx, self.cy = mm(pos.x), mm(pos.y)
        size = pad.GetSize(pcbnew.F_Cu)
        self.hw, self.hh = mm(size.x) / 2, mm(size.y) / 2
        self.ang = math.radians(pad.GetOrientationDegrees())
        self.circulo = pad.GetShape(pcbnew.F_Cu) == pcbnew.PAD_SHAPE_CIRCLE
        conjunto = pad.GetLayerSet()
        self.capas = [i for i, lid in enumerate(pila) if conjunto.Contains(lid)]
        self.pasante = len(self.capas) == len(pila)

    @property
    def radio(self) -> float:
        return self.hw if self.circulo else math.hypot(self.hw, self.hh)

    def distancia(self, x: float, y: float) -> float:
        dx, dy = x - self.cx, y - self.cy
        if self.circulo:
            return math.hypot(dx, dy) - self.hw
        c, s = math.cos(self.ang), math.sin(self.ang)
        px, py = dx * c - dy * s, dx * s + dy * c
        ex, ey = abs(px) - self.hw, abs(py) - self.hh
        if ex <= 0 and ey <= 0:
            return max(ex, ey)
        return math.hypot(max(ex, 0.0), max(ey, 0.0))


class Rejilla:
    """Celdas donde puede ir el eje de una pista de cierta red y ancho, por capa de la pila."""

    def __init__(self, placa: "Placa", red: str, ancho: float):
        x0, y0, x1, y1 = placa.contorno_mm
        borde = placa.margen_borde + ancho / 2
        self.i0, self.i1 = math.ceil((x0 + borde) / G), math.floor((x1 - borde) / G)
        self.j0, self.j1 = math.ceil((y0 + borde) / G), math.floor((y1 - borde) / G)
        self.ruteo = set(placa.capas_ruteo())
        n = len(placa.pila)
        propia = placa.regla(red)

        def radios(otra: str):
            h = max(propia.holgura, placa.regla(otra).holgura) if otra else propia.holgura
            return h + ancho / 2, h + propia.via_d / 2

        self.bloq = [set() for _ in range(n)]
        self.via = set()
        for f in placa.formas():
            if f.red == red:
                continue
            rt, rv = radios(f.red)
            for capa in f.capas:
                self._marcar(self.bloq[capa], f.distancia, f.cx, f.cy, f.radio, rt)
            self._marcar(self.via, f.distancia, f.cx, f.cy, f.radio, rv)
        for p in placa.pistas:
            if p["red"] == red:
                continue
            (ax, ay), (bx, by), w = p["a"], p["b"], p["w"]

            def d(x, y, ax=ax, ay=ay, bx=bx, by=by, w=w):
                return dist_segmento(x, y, ax, ay, bx, by) - w / 2

            rt, rv = radios(p["red"])
            cx, cy = (ax + bx) / 2, (ay + by) / 2
            radio = math.hypot(bx - ax, by - ay) / 2 + w / 2
            self._marcar(self.bloq[p["capa"]], d, cx, cy, radio, rt)
            self._marcar(self.via, d, cx, cy, radio, rv)
        for v in placa.vias:
            if v["red"] == red:
                continue

            def dv(x, y, v=v):
                return math.hypot(x - v["x"], y - v["y"]) - v["d"] / 2

            rt, rv = radios(v["red"])
            for capa in range(n):
                self._marcar(self.bloq[capa], dv, v["x"], v["y"], v["d"] / 2, rt)
            self._marcar(self.via, dv, v["x"], v["y"], v["d"] / 2, rv)
        for area in placa.areas_prohibidas():
            ax0, ay0, ax1, ay1 = area["caja"]
            for i in range(math.floor(ax0 / G), math.ceil(ax1 / G) + 1):
                for j in range(math.floor(ay0 / G), math.ceil(ay1 / G) + 1):
                    if area["raster"].dentro(i * G, j * G):
                        if area["pistas"]:
                            for capa in area["capas"]:
                                self.bloq[capa].add((i, j))
                        if area["vias"]:
                            self.via.add((i, j))

    @staticmethod
    def _marcar(conjunto, distancia, cx, cy, radio, r):
        alcance = radio + r + G
        for i in range(math.floor((cx - alcance) / G), math.ceil((cx + alcance) / G) + 1):
            for j in range(math.floor((cy - alcance) / G), math.ceil((cy + alcance) / G) + 1):
                if distancia(i * G, j * G) < r:
                    conjunto.add((i, j))

    def libre(self, i, j, capa) -> bool:
        return (capa in self.ruteo and self.i0 <= i <= self.i1 and self.j0 <= j <= self.j1
                and (i, j) not in self.bloq[capa])


class Placa:
    def __init__(self, carpeta: Path, nombre: str, sesion: Sesion | None = None, anchos: dict | None = None,
                 ancho_senal: float = 0.3, holgura: float = 0.25, via=(0.8, 0.4), margen_borde: float = 0.6,
                 capas: int = 2):
        if capas not in (2, 4, 6, 8):
            raise ValueError("KiCad admite placas de 2, 4, 6 u 8 capas en este motor")
        self.carpeta = Path(carpeta)
        self.nombre = nombre
        self.ruta = self.carpeta / f"{nombre}.kicad_pcb"
        self.esquema = self.carpeta / f"{nombre}.kicad_sch"
        self.estado_ruta = self.carpeta / ".halo" / "placa.json"
        self.sesion = sesion
        self.anchos = anchos or {}
        self.ancho_senal = ancho_senal
        self.holgura = holgura
        self.via_diametro, self.via_taladro = via
        self.margen_borde = margen_borde
        self.board = pcbnew.BOARD()
        self.board.SetCopperLayerCount(capas)
        self.pila = pila_de_cobre(capas)
        self.huellas: dict[str, object] = {}
        self.colocadas: set[str] = set()
        self.fijadas: set[str] = set()
        self.planos: dict[str, list[str]] = {}
        self.pistas: list[dict] = []
        self.vias: list[dict] = []
        self.contorno_mm = None
        self.netlist: dict[str, set] = {}
        self.etapa = "nueva"
        self.estimado = False
        self.clases = Clases(self.carpeta / f"{nombre}.kicad_pro")
        self._reglas: dict = {}
        self._prohibidas = None
        self._rellenos = None
        self._revision_proyecto = revision(self.carpeta)

    # ------------------------------------------------------------ abrir y guardar
    @classmethod
    def abrir(cls, carpeta: Path, nombre: str, sesion: Sesion | None = None) -> "Placa":
        carpeta = Path(carpeta)
        estado = {}
        archivo_estado = carpeta / ".halo" / "placa.json"
        if archivo_estado.is_file():
            estado = json.loads(archivo_estado.read_text(encoding="utf-8"))
        p = cls(carpeta, nombre, sesion, anchos=estado.get("anchos"), capas=estado.get("capas", 2))
        if not p.ruta.is_file():
            raise FileNotFoundError(f"no existe la placa {p.ruta}; primero hay que crearla")
        p._cargar(estado)
        return p

    def _cargar(self, estado: dict) -> None:
        """Lee la placa del disco y el estado de etapa guardado en .halo/placa.json."""
        self.board = pcbnew.LoadBoard(str(self.ruta))
        self.pila = pila_de_cobre(self.board.GetCopperLayerCount())
        self.huellas = {fp.GetReference(): fp for fp in self.board.GetFootprints()}
        self.colocadas = set()
        self.fijadas = set(estado.get("fijadas", []))
        self.planos = {k: (list(v) if isinstance(v, list) else [v]) for k, v in estado.get("planos", {}).items()}
        self.etapa = estado.get("etapa", "creada")
        self.estimado = estado.get("estimado", False)
        self._prohibidas = None
        self._rellenos = None
        caja = None
        for d in self.board.GetDrawings():
            if d.GetLayer() == pcbnew.Edge_Cuts:
                b = d.GetBoundingBox()
                r = (mm(b.GetX()), mm(b.GetY()), mm(b.GetX() + b.GetWidth()), mm(b.GetY() + b.GetHeight()))
                caja = r if caja is None else (min(caja[0], r[0]), min(caja[1], r[1]), max(caja[2], r[2]), max(caja[3], r[3]))
        self.contorno_mm = None
        if caja:
            grosor = 0.05
            self.contorno_mm = (caja[0] + grosor, caja[1] + grosor, caja[2] - grosor, caja[3] - grosor)
        for ref, fp in self.huellas.items():
            if self.contorno_mm and self._dentro(self._patio(fp)):
                self.colocadas.add(ref)
        self.netlist = {}
        for ref, fp in self.huellas.items():
            for pad in fp.Pads():
                if pad.GetNetname():
                    self.netlist.setdefault(pad.GetNetname(), set()).add((ref, pad.GetNumber()))
        self._leer_cobre()
        if "planos" not in estado:
            for lid in self.pila[1:-1]:
                redes = sorted({z.GetNetname() for z in self.board.Zones()
                                if not z.GetIsRuleArea() and z.GetLayerSet().Contains(lid) and z.GetNetname()})
                if redes and not any(t["capa"] == self.pila.index(lid) for t in self.pistas):
                    self.planos[pcbnew.LayerName(lid)] = redes
        if "etapa" not in estado:
            self.etapa = "ruteada" if self.pistas else "acomodada" if self.colocadas == set(self.huellas) else "creada"

    def _marca_memoria(self):
        return self.etapa

    def _volver_memoria(self, marca, cambiados: bool) -> None:
        """Tras un fallo: la etapa vuelve a la previa y, si ya se había escrito algo, se recarga todo del disco."""
        if cambiados and self.ruta.is_file():
            estado = json.loads(self.estado_ruta.read_text(encoding="utf-8")) if self.estado_ruta.is_file() else {}
            self.anchos = estado.get("anchos") or {}
            self._reglas.clear()
            self._cargar(estado)
        self.etapa = marca

    def _leer_cobre(self) -> None:
        self.pistas, self.vias = [], []
        for t in self.board.GetTracks():
            clase = t.GetClass()
            if clase == "PCB_VIA":
                pos = t.GetPosition()
                try:
                    diametro = mm(t.GetWidth(pcbnew.F_Cu))
                except TypeError:
                    diametro = mm(t.GetWidth())
                self.vias.append({"x": mm(pos.x), "y": mm(pos.y), "d": diametro or self.via_diametro, "red": t.GetNetname()})
            elif t.GetLayer() in self.pila:
                a, b = t.GetStart(), t.GetEnd()
                self.pistas.append({"a": (mm(a.x), mm(a.y)), "b": (mm(b.x), mm(b.y)), "w": mm(t.GetWidth()),
                                    "capa": self.pila.index(t.GetLayer()), "red": t.GetNetname()})

    def guardar(self) -> Path:
        pcbnew.SaveBoard(str(self.ruta), self.board)
        self.estado_ruta.parent.mkdir(parents=True, exist_ok=True)
        self.estado_ruta.write_text(json.dumps({
            "etapa": self.etapa, "estimado": self.estimado, "capas": len(self.pila), "anchos": self.anchos, "planos": self.planos,
            "fijadas": sorted(self.fijadas), "contorno": self.contorno_mm}, indent=2, ensure_ascii=False), encoding="utf-8")
        self.publicar()
        return self.ruta

    def publicar(self) -> None:
        if self.sesion:
            x0, y0, x1, y1 = self.contorno_mm or (0, 0, 0, 0)
            self.sesion.marcar(placa={"etapa": self.etapa, "capas": len(self.pila),
                                      "tamano": [round(x1 - x0, 2), round(y1 - y0, 2)], "planos": self.planos})

    def _paso(self, texto: str, op: str, **extra) -> None:
        self.guardar()
        if self.sesion:
            self.sesion.evento(texto, op, **extra)
            self.sesion.esperar()

    def _decir(self, texto: str, op: str, nivel: str = "") -> None:
        if self.sesion:
            self.sesion.evento(texto, op, nivel=nivel)

    # ------------------------------------------------------------ etapa 1: crear
    def crear(self, ancho: float | None = None, alto: float | None = None, origen=(100.0, 100.0)) -> None:
        self._cargar_huellas()
        estimado = ancho is None or alto is None
        if estimado:
            area = 0.0
            for fp in self.huellas.values():
                x0, y0, x1, y1 = self._patio(fp)
                area += (x1 - x0 + 1.2) * (y1 - y0 + 1.2)
            util = area * 2.4
            ancho = max(20.0, math.ceil(math.sqrt(util * 1.4)))
            alto = max(15.0, math.ceil(util / ancho))
        x0, y0 = origen
        self._contorno_rect(x0, y0, x0 + ancho, y0 + alto)
        self.estimado = estimado
        self._poner_en_espera()
        self.etapa = "creada"
        motivo = "estimado por el área de los componentes" if estimado else "pedido"
        self._paso(f"Placa de {ancho:g} × {alto:g} mm ({motivo}), {len(self.pila)} capas de cobre, "
                   f"{len(self.huellas)} huellas en espera", "crear")

    def _contorno_rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        for d in list(self.board.GetDrawings()):
            if d.GetLayer() == pcbnew.Edge_Cuts:
                self.board.Delete(d)
        forma = pcbnew.PCB_SHAPE(self.board)
        forma.SetShape(pcbnew.SHAPE_T_RECT)
        forma.SetStart(V(x0, y0))
        forma.SetEnd(V(x1, y1))
        forma.SetLayer(pcbnew.Edge_Cuts)
        forma.SetWidth(pcbnew.FromMM(0.1))
        self.board.Add(forma)
        self.contorno_mm = (x0, y0, x1, y1)

    def _biblioteca(self, nick: str) -> Path:
        """Carpeta .pretty de una biblioteca: tabla del proyecto, tabla del usuario o bibliotecas de KiCad."""
        tablas = [self.carpeta / "fp-lib-table"]
        if os.environ.get("APPDATA"):
            tablas.append(Path(os.environ["APPDATA"]) / "kicad" / "10.0" / "fp-lib-table")
        for tabla in tablas:
            if not tabla.is_file():
                continue
            for m in re.finditer(r'\(lib\s*\(name\s*"?([^")]+?)"?\)\s*\(type\s*"?[^")]*"?\)\s*\(uri\s*"?([^")]+?)"?\)',
                                 tabla.read_text(encoding="utf-8")):
                if m.group(1) == nick:
                    uri = m.group(2).replace("${KIPRJMOD}", str(self.carpeta))
                    uri = re.sub(r"\$\{KICAD\d*_FOOTPRINT_DIR\}", str(HUELLAS).replace("\\", "/"), uri)
                    return Path(uri)
        return HUELLAS / f"{nick}.pretty"

    def _cargar_huellas(self) -> None:
        raiz = sx.loads(self.esquema.read_text(encoding="utf-8"))
        simbolos: dict[str, tuple] = {}
        for s in sx.find_all(raiz, "symbol"):
            props = {str(p[1]): str(p[2]) for p in sx.find_all(s, "property")}
            ref = props.get("Reference", "")
            if not ref or ref.startswith("#") or str(sx.val(s, "on_board", "yes")) == "no":
                continue
            unidad = int(sx.val(s, "unit", 1) or 1)
            if ref in simbolos and simbolos[ref][0] <= unidad:
                continue
            if ":" not in props.get("Footprint", ""):
                raise ValueError(f"{ref} no tiene huella asignada en el esquemático")
            campos = {k: props.get(k, "") for k in ("Datasheet", "Description")}
            simbolos[ref] = (unidad, props.get("Value", ""), props["Footprint"], str(sx.val(s, "uuid")), campos)
        self.netlist = exportar_netlist(self.esquema, self.carpeta / ".halo" / "netlist.net")
        for nombre in sorted(self.netlist):
            if self.board.FindNet(nombre) is None:
                self.board.Add(pcbnew.NETINFO_ITEM(self.board, nombre))
        pad_red = {nodo: nombre for nombre, nodos in self.netlist.items() for nodo in nodos}
        for ref in sorted(simbolos):
            _unidad, valor, huella, uid, campos = simbolos[ref]
            nick, nombre = huella.split(":", 1)
            fp = pcbnew.FootprintLoad(str(self._biblioteca(nick)), nombre)
            if fp is None:
                raise KeyError(f"no existe la huella {huella}")
            fp.SetFPID(pcbnew.LIB_ID(nick, nombre))
            fp.SetReference(ref)
            fp.SetValue(valor)
            for campo, texto in campos.items():
                fp.SetField(campo, texto)
            fp.SetPath(pcbnew.KIID_PATH("/" + uid))
            self.board.Add(fp)
            for pad in fp.Pads():
                red = pad_red.get((ref, pad.GetNumber()))
                if red:
                    pad.SetNet(self.board.FindNet(red))
            self.huellas[ref] = fp

    def _poner_en_espera(self) -> None:
        x0, y0, x1, y1 = self.contorno_mm
        x, y, fila = x0, y1 + 8, 0.0
        for ref in sorted(self.huellas):
            fp = self.huellas[ref]
            fp.SetOrientationDegrees(0)
            fp.SetPosition(V(0, 0))
            cx0, cy0, cx1, cy1 = self._patio(fp)
            if x + (cx1 - cx0) > x1 + 40:
                x, y, fila = x0, y + fila + 3, 0.0
            fp.SetPosition(V(x - cx0, y - cy0))
            x += (cx1 - cx0) + 3
            fila = max(fila, cy1 - cy0)
        self.colocadas.clear()

    # ------------------------------------------------------------ geometría de colocación
    def _patio(self, fp):
        caja = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd).BBox()
        if caja.GetWidth() == 0:
            caja = fp.GetBoundingBox(False)
        return (mm(caja.GetX()), mm(caja.GetY()), mm(caja.GetX() + caja.GetWidth()), mm(caja.GetY() + caja.GetHeight()))

    @staticmethod
    def lado(fp) -> str:
        return "B" if fp.IsFlipped() else "F"

    @staticmethod
    def es_tht(fp) -> bool:
        return bool(fp.GetAttributes() & pcbnew.FP_THROUGH_HOLE) or any(p.GetDrillSizeX() > 0 for p in fp.Pads())

    @staticmethod
    def poner_lado(fp, lado: str) -> None:
        if (lado == "B") != fp.IsFlipped():
            try:
                fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
            except (TypeError, AttributeError):
                fp.Flip(fp.GetPosition(), False)

    def _local_lados(self, fp, lados=("F",)) -> dict:
        """Patio y pads de la huella en 0° y en el origen, para cada cara pedida."""
        pos, ang, lado0 = fp.GetPosition(), fp.GetOrientationDegrees(), self.lado(fp)
        salida = {}
        for lado in lados:
            self.poner_lado(fp, lado)
            fp.SetOrientationDegrees(0)
            fp.SetPosition(V(0, 0))
            salida[lado] = {"caja": self._patio(fp),
                            "pads": [(pad.GetNumber(), mm(pad.GetPosition().x), mm(pad.GetPosition().y), pad.GetNetname())
                                     for pad in fp.Pads()]}
        self.poner_lado(fp, lado0)
        fp.SetOrientationDegrees(ang)
        fp.SetPosition(pos)
        return salida

    def _dentro(self, caja, margen: float = 0.0) -> bool:
        x0, y0, x1, y1 = self.contorno_mm
        return caja[0] >= x0 + margen and caja[1] >= y0 + margen and caja[2] <= x1 - margen and caja[3] <= y1 - margen

    def _local(self, fp) -> dict:
        pos, ang = fp.GetPosition(), fp.GetOrientationDegrees()
        fp.SetOrientationDegrees(0)
        fp.SetPosition(V(0, 0))
        caja = self._patio(fp)
        pads = [(pad.GetNumber(), mm(pad.GetPosition().x), mm(pad.GetPosition().y), pad.GetNetname()) for pad in fp.Pads()]
        fp.SetPosition(pos)
        fp.SetOrientationDegrees(ang)
        return {"caja": caja, "pads": pads}

    @staticmethod
    def _caja_girada(caja, x, y, ang):
        esquinas = [rotar_punto(px, py, ang) for px, py in ((caja[0], caja[1]), (caja[2], caja[1]), (caja[2], caja[3]), (caja[0], caja[3]))]
        xs, ys = [x + p[0] for p in esquinas], [y + p[1] for p in esquinas]
        return (min(xs), min(ys), max(xs), max(ys))

    def colocar(self, ref: str, x: float, y: float, rot: float = 0, avisar: bool = True, fuera: bool = False,
                lado: str | None = None) -> list[str]:
        """Coloca una huella en la placa, en la cara pedida (F arriba, B abajo). Con fuera=True también acepta
        dejarla en espera, del todo fuera del contorno. Si ya estaba colocada, quita sólo las ramas de pista
        que salían de sus pads. Dos piezas chocan si están en la misma cara o si alguna es de inserción."""
        fp = self.huellas[ref]
        antes = (fp.GetPosition(), fp.GetOrientationDegrees(), self.lado(fp))
        viejos = [FormaPad(ref, pad, self.pila) for pad in fp.Pads()] if ref in self.colocadas else []
        if lado:
            self.poner_lado(fp, lado)
        fp.SetOrientationDegrees(rot)
        fp.SetPosition(V(x, y))
        a = self._patio(fp)
        mi_lado, tht = self.lado(fp), self.es_tht(fp)
        en_espera = fuera and not _cruzan(a, self.contorno_mm)
        choques = []
        for o in self.huellas:
            if o == ref or (o in self.colocadas) == en_espera:
                continue
            otra = self.huellas[o]
            if (en_espera or tht or self.lado(otra) == mi_lado or self.es_tht(otra)) and _cruzan(a, self._patio(otra)):
                choques.append(o)
        if choques or not (en_espera or self._dentro(a)):
            self.poner_lado(fp, antes[2])
            fp.SetOrientationDegrees(antes[1])
            fp.SetPosition(antes[0])
            motivo = (f"se superpone con {', '.join(sorted(choques))}" if choques
                      else "quedaría montada sobre el borde de la placa")
            raise ValueError(f"{ref} {motivo}")
        movida = (antes[0] != fp.GetPosition() or abs(antes[1] - fp.GetOrientationDegrees()) > 1e-6 or antes[2] != mi_lado)
        quitadas = self._quitar_ramas(viejos) if viejos and movida else []
        if en_espera:
            self.colocadas.discard(ref)
        else:
            self.colocadas.add(ref)
        if quitadas and self.etapa in ("ruteada", "verificada"):
            self.etapa = "acomodada"
        if self.etapa == "creada" and self.colocadas == set(self.huellas):
            self.etapa = "acomodada"
        if avisar:
            extra = f"; quité sus ramas de pista en {', '.join(quitadas)}" if quitadas else ""
            cara = " en la cara inferior" if mi_lado == "B" else ""
            self._paso(f"Coloco {ref} ({fp.GetValue()}) en ({x:.2f}, {y:.2f}) a {rot:g}°{cara}{extra}", "colocar")
        return quitadas

    def _quitar_ramas(self, viejos: list[FormaPad]) -> list[str]:
        """Quita las pistas que salían de esos pads, siguiendo cada rama hasta la primera unión, pad o vía
        que siga en uso. El resto de la red se conserva."""
        tol = 0.02
        claves = {(f.ref, f.numero) for f in viejos}
        fijos = [f for f in self.formas() if (f.ref, f.numero) not in claves]
        redes = {f.red for f in viejos if f.red} - self.redes_plano()
        items = []
        for t in self.board.GetTracks():
            if t.GetNetname() not in redes:
                continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition()
                items.append({"obj": t, "via": True, "p": (mm(pos.x), mm(pos.y)), "red": t.GetNetname()})
            elif t.GetLayer() in self.pila:
                a, b = t.GetStart(), t.GetEnd()
                items.append({"obj": t, "via": False, "a": (mm(a.x), mm(a.y)), "b": (mm(b.x), mm(b.y)),
                              "capa": self.pila.index(t.GetLayer()), "red": t.GetNetname()})

        def en_pad(p, capa, lista, red) -> bool:
            return any(f.red == red and (capa is None or capa in f.capas) and f.distancia(*p) <= tol for f in lista)

        vivos = set(range(len(items)))
        for i, it in enumerate(items):
            puntos = [it["p"]] if it["via"] else [it["a"], it["b"]]
            if any(en_pad(p, None if it["via"] else it["capa"], viejos, it["red"]) for p in puntos):
                vivos.discard(i)
        cambiado = True
        while cambiado:
            cambiado = False
            for i in list(vivos):
                it = items[i]
                if it["via"]:
                    tramos = sum(1 for j in vivos if not items[j]["via"] and items[j]["red"] == it["red"]
                                 and min(math.dist(items[j]["a"], it["p"]), math.dist(items[j]["b"], it["p"])) <= tol)
                    if tramos <= 1 and not en_pad(it["p"], None, fijos, it["red"]):
                        vivos.discard(i)
                        cambiado = True
                    continue
                for p in (it["a"], it["b"]):
                    if en_pad(p, it["capa"], fijos, it["red"]):
                        continue
                    unido = False
                    for j in vivos:
                        o = items[j]
                        if j == i or o["red"] != it["red"]:
                            continue
                        if o["via"] and math.dist(o["p"], p) <= tol:
                            unido = True
                        elif not o["via"] and o["capa"] == it["capa"] and dist_segmento(p[0], p[1], *o["a"], *o["b"]) <= tol:
                            unido = True
                        if unido:
                            break
                    if not unido:
                        vivos.discard(i)
                        cambiado = True
                        break
        quitadas = set()
        for i, it in enumerate(items):
            if i not in vivos:
                self.board.Delete(it["obj"])
                quitadas.add(it["red"])
        self._leer_cobre()
        return sorted(quitadas)

    def mover(self, ref: str, dx: float, dy: float, autor: str = "IA") -> list[str]:
        fp = self.huellas[ref]
        pos = fp.GetPosition()
        quitadas = self.colocar(ref, mm(pos.x) + dx, mm(pos.y) + dy, fp.GetOrientationDegrees(), avisar=False, fuera=True)
        self._ordenar_referencias()
        donde =("en espera, fuera de la placa" if ref not in self.colocadas
                 else f"a ({mm(fp.GetPosition().x):.2f}, {mm(fp.GetPosition().y):.2f})")
        extra = f"; quité sus ramas de pista en {', '.join(quitadas)}" if quitadas else ""
        self._paso(f"{'Muevo' if autor == 'IA' else 'Moviste'} {ref} {donde}{extra}", "mover", autor=autor)
        return quitadas

    def rotar(self, ref: str, grados: float = 90, autor: str = "IA") -> list[str]:
        fp = self.huellas[ref]
        pos = fp.GetPosition()
        quitadas = self.colocar(ref, mm(pos.x), mm(pos.y), (fp.GetOrientationDegrees() + grados) % 360, avisar=False, fuera=True)
        self._ordenar_referencias()
        extra = f"; quité sus ramas de pista en {', '.join(quitadas)}" if quitadas else ""
        self._paso(f"{'Roto' if autor == 'IA' else 'Rotaste'} {ref} a {fp.GetOrientationDegrees():g}°{extra}", "rotar", autor=autor)
        return quitadas

    def voltear(self, ref: str, autor: str = "IA") -> list[str]:
        """Pasa la huella a la otra cara en el mismo lugar."""
        fp = self.huellas[ref]
        pos = fp.GetPosition()
        nuevo = "F" if self.lado(fp) == "B" else "B"
        quitadas = self.colocar(ref, mm(pos.x), mm(pos.y), fp.GetOrientationDegrees(), avisar=False, fuera=True, lado=nuevo)
        self._ordenar_referencias()
        extra = f"; quité sus ramas de pista en {', '.join(quitadas)}" if quitadas else ""
        cara = "inferior" if nuevo == "B" else "superior"
        self._paso(f"{'Paso' if autor == 'IA' else 'Pasaste'} {ref} a la cara {cara}{extra}", "voltear", autor=autor)
        return quitadas

    def fijar(self, ref: str) -> None:
        self.fijadas.add(ref)
        self._paso(f"Fijo {ref}: el acomodo automático no lo moverá", "fijar")

    def _quitar_pistas_de(self, ref: str) -> list[str]:
        redes = sorted({pad.GetNetname() for pad in self.huellas[ref].Pads() if pad.GetNetname()} - self.redes_plano())
        con_cobre = [r for r in redes if any(p["red"] == r for p in self.pistas) or any(v["red"] == r for v in self.vias)]
        for r in con_cobre:
            self.despejar(r, avisar=False)
        return con_cobre

    # ------------------------------------------------------------ etapa 2: acomodar
    def _modulos(self) -> list[tuple[str, list[str]]]:
        """Módulos del esquemático (recuadros con título) en orden de izquierda a derecha."""
        raiz = sx.loads(self.esquema.read_text(encoding="utf-8"))
        rects = []
        for r in sx.find_all(raiz, "rectangle"):
            a, b = sx.find(r, "start"), sx.find(r, "end")
            x0, y0, x1, y1 = float(a[1]), float(a[2]), float(b[1]), float(b[2])
            rects.append([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), ""])
        for t in sx.find_all(raiz, "text"):
            at = sx.find(t, "at")
            for r in rects:
                if abs(float(at[1]) - r[0]) < 0.02 and abs(float(at[2]) - (r[1] - 1.27)) < 0.02:
                    r[4] = str(t[1])
        grupos = {i: [] for i in range(len(rects))}
        sueltos = []
        for s in sx.find_all(raiz, "symbol"):
            props = {str(p[1]): str(p[2]) for p in sx.find_all(s, "property")}
            ref = props.get("Reference", "")
            if ref not in self.huellas:
                continue
            at = sx.find(s, "at")
            x, y = float(at[1]), float(at[2])
            destino = next((i for i, r in enumerate(rects) if r[0] < x < r[2] and r[1] < y < r[3]), None)
            (grupos[destino] if destino is not None else sueltos).append(ref)
        orden = sorted((i for i in grupos if grupos[i]), key=lambda i: rects[i][0])
        modulos = [(rects[i][4] or f"módulo {n + 1}", sorted(grupos[i])) for n, i in enumerate(orden)]
        if sueltos:
            modulos.append(("sin módulo", sorted(sueltos)))
        return modulos or [("placa", sorted(self.huellas))]

    def acomodar(self, refs: list[str] | None = None, separacion: float = 0.8, margen: float = 1.2,
                 doble_cara: bool = False, altura_max: float | None = None, altura_abajo: float = 3.0) -> dict:
        """Colocación automática con oficio (ver acomodo.py). Devuelve métricas del resultado."""
        from .acomodo import acomodar
        return acomodar(self, refs, separacion, margen, doble_cara, altura_max, altura_abajo)

    def textos_placa(self) -> list[dict]:
        """Rótulos sueltos de serigrafía (textos de la placa, no de las huellas), con su caja y su cara."""
        salida = []
        for d in self.board.GetDrawings():
            if d.GetClass() not in ("PCB_TEXT", "PTEXT") or d.GetLayer() not in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                continue
            b = d.GetBoundingBox()
            salida.append({"obj": d, "texto": d.GetText(), "lado": "B" if d.GetLayer() == pcbnew.B_SilkS else "F",
                           "caja": (mm(b.GetX()), mm(b.GetY()), mm(b.GetX() + b.GetWidth()), mm(b.GetY() + b.GetHeight()))})
        return salida

    def _obstaculos_serigrafia(self, rotulos: bool = True):
        """Lo que la serigrafía no debe pisar: patios de piezas (por cara), pads y, si se pide, los rótulos de la placa."""
        from .acomodo import Indice
        indice = Indice()
        for r in self.colocadas:
            fp = self.huellas[r]
            lado, tht = self.lado(fp), self.es_tht(fp)
            indice.agregar(self._patio(fp), (r, ("F", "B") if tht else (lado,)))
            for pad in fp.Pads():
                b = pad.GetBoundingBox()
                caja = (mm(b.GetX()) - 0.15, mm(b.GetY()) - 0.15, mm(b.GetX() + b.GetWidth()) + 0.15, mm(b.GetY() + b.GetHeight()) + 0.15)
                capas = ("F", "B") if pad.GetDrillSizeX() > 0 else ("B",) if pad.IsOnLayer(pcbnew.B_Cu) else ("F",)
                indice.agregar(caja, (None, capas))
        if rotulos:
            for t in self.textos_placa():
                indice.agregar(t["caja"], ("rotulo", (t["lado"],)))
        return indice

    def rotulos_encimados(self) -> list[str]:
        obstaculos = self._obstaculos_serigrafia(rotulos=False)
        return [t["texto"] for t in self.textos_placa()
                if any(t["lado"] in capas and _cruzan(t["caja"], caja) for (_d, capas), caja in obstaculos.consultar(t["caja"]))]

    def _acomodar_rotulos(self, forzar=()) -> int:
        """Rótulos de la placa que pisan pads o piezas se corren al hueco libre más cercano."""
        from .acomodo import Indice
        obstaculos = self._obstaculos_serigrafia(rotulos=False)
        x0, y0, x1, y1 = self.contorno_mm
        tomados = Indice()
        movidos = 0
        for t in sorted(self.textos_placa(), key=lambda t: (t["caja"][1], t["caja"][0])):
            c, lado = t["caja"], t["lado"]

            def libre(k, lado=lado) -> bool:
                if k[0] < x0 + 0.2 or k[1] < y0 + 0.2 or k[2] > x1 - 0.2 or k[3] > y1 - 0.2:
                    return False
                if any(lado in capas and _cruzan(k, caja) for (_d, capas), caja in obstaculos.consultar(k)):
                    return False
                return not any(l == lado and _cruzan(k, caja) for l, caja in tomados.consultar(k))

            if (t["texto"], lado) not in forzar and libre(c):
                tomados.agregar(c, lado)
                continue
            elegido = None
            for radio in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.5, 8.0, 10.0, 12.5, 15.0):
                for grados in range(0, 360, 30):
                    dx, dy = radio * math.cos(math.radians(grados)), radio * math.sin(math.radians(grados))
                    k = (c[0] + dx, c[1] + dy, c[2] + dx, c[3] + dy)
                    if libre(k):
                        elegido = (dx, dy, k)
                        break
                if elegido:
                    break
            if elegido:
                dx, dy, k = elegido
                pos = t["obj"].GetPosition()
                t["obj"].SetPosition(V(mm(pos.x) + dx, mm(pos.y) + dy))
                tomados.agregar(k, lado)
                movidos += 1
            else:
                tomados.agregar(c, lado)
        return movidos

    def _ordenar_referencias(self, forzar=()) -> int:
        """Referencias de serigrafía legibles y sin avisos de KiCad: fuera de patios, pads y rótulos, sin pisarse,
        dentro de la placa y cada una en su cara. Con forzar, esas referencias buscan otro lugar."""
        from .acomodo import Indice
        forzar = set(forzar)
        x0, y0, x1, y1 = self.contorno_mm
        obstaculos = self._obstaculos_serigrafia()
        patios = {r: (self._patio(self.huellas[r]), self.lado(self.huellas[r])) for r in self.colocadas}
        tomadas = Indice()
        movidas = 0
        for ref in sorted(patios, key=lambda r: (patios[r][0][1], patios[r][0][0])):
            campo = self.huellas[ref].Reference()
            if not campo.IsVisible():
                continue
            patio, lado = patios[ref]
            b = campo.GetBoundingBox()
            w, h = mm(b.GetWidth()), mm(b.GetHeight())
            pos = campo.GetPosition()
            px, py = mm(pos.x), mm(pos.y)
            ox, oy = mm(b.GetX()) + w / 2 - px, mm(b.GetY()) + h / 2 - py
            cx0, cy0, cx1, cy1 = patio
            xm, ym = (cx0 + cx1) / 2, (cy0 + cy1) / 2
            candidatos = [] if ref in forzar else [(px + ox, py + oy)]
            for g in (0.3, 0.9, 1.6):
                arriba, abajo = cy0 - h / 2 - g, cy1 + h / 2 + g
                izquierda, derecha = cx0 - w / 2 - g, cx1 + w / 2 + g
                candidatos += [(xm, arriba), (xm, abajo), (izquierda, ym), (derecha, ym),
                               (cx0 + w / 2, arriba), (cx1 - w / 2, arriba), (cx0 + w / 2, abajo), (cx1 - w / 2, abajo),
                               (izquierda, cy0 + h / 2), (derecha, cy1 - h / 2)]

            def libre(t, ref=ref, lado=lado) -> bool:
                if t[0] < x0 + 0.3 or t[1] < y0 + 0.3 or t[2] > x1 - 0.3 or t[3] > y1 - 0.3:
                    return False
                for (dueno, capas), caja in obstaculos.consultar(t):
                    if dueno != ref and lado in capas and _cruzan(t, caja):
                        return False
                return not any(l == lado and _cruzan(t, caja) for l, caja in tomadas.consultar(t))

            elegido = next(((cx, cy) for cx, cy in candidatos if libre((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))), None)
            cambiada = False
            if elegido is None:
                # Sin hueco a su tamaño: se prueba la referencia al 80 % (1 mm pasa a 0,8 mm, el mínimo habitual de serigrafía).
                talla, grosor = campo.GetTextSize(), campo.GetTextThickness()
                reglas = self.board.GetDesignSettings()
                alto = max(int(talla.y * 0.8), reglas.m_MinSilkTextHeight)
                factor = alto / max(talla.y, 1)
                if factor < 0.999:
                    campo.SetTextSize(pcbnew.VECTOR2I(int(talla.x * factor), alto))
                    campo.SetTextThickness(max(int(grosor * factor), reglas.m_MinSilkTextThickness))
                    b = campo.GetBoundingBox()
                    w2, h2 = mm(b.GetWidth()), mm(b.GetHeight())
                    ox2, oy2 = mm(b.GetX()) + w2 / 2 - px, mm(b.GetY()) + h2 / 2 - py
                    elegido = next(((cx, cy) for cx, cy in [(px + ox2, py + oy2)] + candidatos
                                    if libre((cx - w2 / 2, cy - h2 / 2, cx + w2 / 2, cy + h2 / 2))), None)
                if elegido is None:
                    campo.SetTextSize(talla)
                    campo.SetTextThickness(grosor)
                else:
                    w, h, ox, oy = w2, h2, ox2, oy2
                    cambiada = True
            if elegido is None:
                cx, cy = px + ox, py + oy
            else:
                cx, cy = elegido
                if abs(cx - (px + ox)) > 1e-3 or abs(cy - (py + oy)) > 1e-3:
                    campo.SetPosition(V(cx - ox, cy - oy))
                    cambiada = True
            movidas += cambiada
            tomadas.agregar((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), lado)
        return movidas

    def longitud_conexiones(self) -> float:
        """Suma de árboles de expansión mínima por red (lo que dibuja el ratsnest)."""
        total = 0.0
        for red, nodos in self.netlist.items():
            if red in self.redes_plano():
                continue
            puntos = []
            for ref, num in nodos:
                fp = self.huellas.get(ref)
                if fp is None or ref not in self.colocadas:
                    continue
                for pad in fp.Pads():
                    if pad.GetNumber() == num:
                        puntos.append((mm(pad.GetPosition().x), mm(pad.GetPosition().y)))
            if len(puntos) < 2:
                continue
            dentro, fuera = [puntos[0]], puntos[1:]
            while fuera:
                d, k = min((min(abs(p[0] - q[0]) + abs(p[1] - q[1]) for q in dentro), i) for i, p in enumerate(fuera))
                total += d
                dentro.append(fuera.pop(k))
        return total

    # ------------------------------------------------------------ reglas, capas y cobre
    def regla(self, red: str) -> Regla:
        """Ancho, holgura y vía de una red: clase de red del proyecto, o las del motor; el ancho fijado por la IA manda."""
        if red not in self._reglas:
            base = Regla("halo", self.ancho_senal, self.holgura, self.via_diametro, self.via_taladro)
            r = self.clases.regla(red, base) if self.clases.clases else base
            if red in self.anchos:
                r = replace(r, ancho=self.anchos[red])
            self._reglas[red] = r
        return self._reglas[red]

    def definir_clase(self, nombre: str, ancho=None, holgura=None, via=None, patrones=()) -> dict:
        base = Regla("halo", self.ancho_senal, self.holgura, self.via_diametro, self.via_taladro)
        clase = self.clases.definir(nombre, ancho, holgura, via, patrones, base)
        self._reglas.clear()
        redes = sorted(r for r in self.netlist if self.clases.clase_de(r) == nombre)
        self._paso(f"Clase de red «{nombre}»: pista {clase['track_width']:g} mm, holgura {clase['clearance']:g} mm, "
                   f"vía {clase['via_diameter']:g}/{clase['via_drill']:g} mm; {len(redes)} redes"
                   + (f": {', '.join(redes[:8])}{'…' if len(redes) > 8 else ''}" if redes else ""), "clase")
        return {"clase": clase, "redes": redes}

    def capa_id(self, nombre: str) -> int:
        for lid in self.pila:
            if nombre in (pcbnew.LayerName(lid), self.board.GetLayerName(lid)):
                return lid
        raise ValueError(f"la placa no tiene la capa de cobre {nombre}; tiene {', '.join(self.nombres_capas())}")

    def capas_cobre(self) -> list[str]:
        return [pcbnew.LayerName(lid) for lid in self.pila]

    def nombres_capas(self) -> list[str]:
        salida = []
        for lid in self.pila:
            propio, canon = self.board.GetLayerName(lid), pcbnew.LayerName(lid)
            salida.append(canon if propio == canon else f"{canon} ({propio})")
        return salida

    def nombrar_capa(self, capa: str, nombre: str) -> None:
        lid = self.capa_id(capa)
        self.board.SetLayerName(lid, nombre)
        self._paso(f"La capa {pcbnew.LayerName(lid)} se llama ahora «{nombre}»", "capa")

    def redes_plano(self) -> set[str]:
        return {r for redes in self.planos.values() for r in redes}

    def capas_ruteo(self) -> list[int]:
        return [i for i, lid in enumerate(self.pila) if pcbnew.LayerName(lid) not in self.planos]

    def areas_prohibidas(self) -> list[dict]:
        if self._prohibidas is None:
            from .cobre import areas_prohibidas
            self._prohibidas = areas_prohibidas(self)
        return self._prohibidas

    def cobre(self, red: str, capas, region=None, prioridad: int = 0, conexion: str = "termica",
              holgura: float | None = None, nombre: str = "", plano: bool = False) -> dict:
        from . import cobre as cu
        zona = cu.cobre(self, red, capas, region, prioridad, conexion, holgura, nombre=nombre, plano=plano)
        cu.rellenar(self)
        area = cu.area_rellena(zona)
        lugar = "toda la placa" if region in (None, "", "placa") else f"región {region}"
        texto = (f"Cobre de {red} en {', '.join(capas)} ({lugar}{f', prioridad {prioridad}' if prioridad else ''}, "
                 f"conexión {conexion}): {area:.0f} mm² rellenos")
        if plano:
            texto += "; esa capa queda como plano y no lleva pistas"
        self._paso(texto, "cobre", red=red)
        return {"red": red, "capas": list(capas), "area_mm2": round(area, 1), "planos": self.planos}

    def plano(self, red: str, capa: str, conexion: str = "termica") -> dict:
        """Capa entera de una red (interna en 4, 6 u 8 capas). Esa capa deja de usarse para pistas."""
        return self.cobre(red, [capa], plano=True, conexion=conexion)

    def zona(self, red: str, capa: str = "B.Cu") -> dict:
        """Relleno de una red en toda una capa de señal (por ejemplo GND abajo en 2 capas)."""
        return self.cobre(red, [capa])

    def prohibir(self, capas, region, sin=("pistas", "vias", "cobre"), nombre: str = "") -> dict:
        from . import cobre as cu
        cu.prohibir(self, capas, region, sin, nombre)
        self._prohibidas = None
        cu.rellenar(self)
        self._paso(f"Área prohibida en {', '.join(capas) if not isinstance(capas, str) else capas} ({region}): "
                   f"sin {', '.join(sin)}", "prohibir")
        return {"capas": capas, "sin": list(sin)}

    def quitar_cobre(self, nombre=None, red=None, capa=None) -> dict:
        from . import cobre as cu
        n = cu.quitar(self, nombre, red, capa)
        self._prohibidas = None
        cu.rellenar(self)
        self._paso(f"Quito {n} zonas" + (f" de {red}" if red else "") + (f" en {capa}" if capa else "")
                   + (f" llamadas «{nombre}»" if nombre else ""), "quitar_cobre")
        return {"quitadas": n, "planos": self.planos}

    def rellenar(self) -> list[dict]:
        from . import cobre as cu
        cu.rellenar(self)
        zonas = cu.zonas(self)
        cobre_zonas = [z for z in zonas if not z["regla"]]
        self._paso(f"KiCad rellena {len(cobre_zonas)} zonas: {sum(z['area_mm2'] for z in cobre_zonas):.0f} mm² de cobre", "rellenar")
        return zonas

    def costura(self, red: str = "GND", paso: float = 5.0) -> dict:
        from . import cobre as cu
        n = cu.costura(self, red, paso)
        self._leer_cobre()
        self._paso(f"Coso el cobre de {red} con {n} vías cada {paso:g} mm", "costura", red=red)
        return {"vias": n}

    def bajar_a_plano(self, red: str, avisar: bool = True) -> dict:
        from . import cobre as cu
        hechas, fallidas = cu.bajar_a_plano(self, red)
        self._leer_cobre()
        if hechas:
            cu.rellenar(self)
        if avisar or hechas or fallidas:
            texto = f"Bajo {hechas} pads SMD de {red} a su plano, cada uno con su vía"
            if fallidas:
                texto += f"; sin sitio para {', '.join(fallidas[:6])}{'…' if len(fallidas) > 6 else ''}"
            self._paso(texto, "bajar", red=red)
        return {"hechas": hechas, "fallidas": fallidas}

    def perfiles(self, altura_abajo: float = 3.0) -> dict:
        from . import perfil as pf
        redes_de_ref: dict = {}
        for red, nodos in self.netlist.items():
            for ref, _ in nodos:
                redes_de_ref.setdefault(ref, set()).add(red)
        return pf.perfilar(self.huellas, self.carpeta, redes_de_ref, lambda r: self.regla(r).ancho,
                           self.redes_rapidas(), altura_abajo)

    def redes_rapidas(self) -> set[str]:
        from . import perfil as pf
        return pf.redes_rapidas(self.netlist, es_potencia)

    # ------------------------------------------------------------ etapa 3: rutear (nativo)
    def formas(self) -> list[FormaPad]:
        return [FormaPad(ref, pad, self.pila) for ref, fp in self.huellas.items() if ref in self.colocadas for pad in fp.Pads()]

    def ancho(self, red: str) -> float:
        return self.regla(red).ancho

    def fijar_ancho(self, red: str, ancho: float) -> None:
        self.anchos[red] = float(ancho)
        self._reglas.pop(red, None)
        self._paso(f"Ancho de {red}: {float(ancho):g} mm", "ancho")

    def despejar(self, red: str | None = None, avisar: bool = True) -> int:
        quitadas = 0
        for t in list(self.board.GetTracks()):
            if red is None or t.GetNetname() == red:
                self.board.Delete(t)
                quitadas += 1
        self._leer_cobre()
        if avisar:
            self._paso(f"Quito {quitadas} tramos de {'todas las redes' if red is None else red}", "despejar")
        return quitadas

    def redes_a_rutear(self, redes: list[str] | None = None) -> list[str]:
        candidatas = [r for r, nodos in self.netlist.items() if len(nodos) >= 2 and (redes is None or r in redes)]

        def extension(r):
            pts = [f for f in self.formas() if f.red == r]
            if not pts:
                return 0
            return (max(p.cx for p in pts) - min(p.cx for p in pts)) + (max(p.cy for p in pts) - min(p.cy for p in pts))

        return sorted(candidatas, key=lambda r: (r in self.anchos, extension(r)))

    def rutear(self, redes: list[str] | None = None, reintentos: int = 3) -> dict:
        if self.etapa not in ("acomodada", "ruteada", "verificada"):
            raise RuntimeError("primero hay que acomodar los componentes")
        if redes is None:
            for red in sorted(self.redes_plano()):
                self.bajar_a_plano(red, avisar=False)
        candidatas = self.redes_a_rutear(redes)
        orden = [r for r in candidatas if len(self._grupos_red(r)) > 1]
        completas = len(candidatas) - len(orden)
        if not orden:
            if not self._incompletas():
                self.etapa = "ruteada"
            self._paso("Nada que rutear: esas redes ya están completas", "rutear")
            return {"fallidas": [], "pistas": len(self.pistas), "vias": len(self.vias)}
        self._decir(f"Ruteo {len(orden)} redes con el enrutador propio ({len(self.capas_ruteo())} capas de señal)"
                    + (f"; {completas} ya estaban completas y no las toco" if completas else ""), "rutear")
        fallidas = [r for r in orden if not self._rutear_red(r)]
        for intento in range(reintentos):
            if not fallidas:
                break
            self._decir(f"Arranco y reintento {len(fallidas)} redes que no pasaron (intento {intento + 1})", "rutear")
            nuevas = []
            for red in fallidas:
                # An explicit scope is a contract, including rip-up/retry operations.
                vecinas = [v for v in self._vecinas(red) if redes is None or v in redes][:2]
                for v in vecinas:
                    self.despejar(v, avisar=False)
                ok = self._rutear_red(red, conservar=False)
                for v in vecinas:
                    if not self._rutear_red(v):
                        nuevas.append(v)
                if not ok:
                    nuevas.append(red)
            fallidas = list(dict.fromkeys(nuevas))
        if any(not z.GetIsRuleArea() for z in self.board.Zones()):
            from . import cobre as cu
            cu.rellenar(self)
        if not fallidas and not self._incompletas():
            self.etapa = "ruteada"
        texto = (f"Ruteo completo: {len(self.pistas)} tramos, {len(self.vias)} vías" if not fallidas
                 else f"Ruteo incompleto: faltan {', '.join(fallidas)}")
        self._paso(texto, "rutear")
        return {"fallidas": fallidas, "pistas": len(self.pistas), "vias": len(self.vias)}

    def _vecinas(self, red: str) -> list[str]:
        pads = [f for f in self.formas() if f.red == red]
        if not pads:
            return []
        bx0, bx1 = min(p.cx for p in pads) - 3, max(p.cx for p in pads) + 3
        by0, by1 = min(p.cy for p in pads) - 3, max(p.cy for p in pads) + 3
        cuenta: dict[str, int] = {}
        for p in self.pistas:
            if p["red"] != red and any(bx0 <= x <= bx1 and by0 <= y <= by1 for x, y in (p["a"], p["b"])):
                cuenta[p["red"]] = cuenta.get(p["red"], 0) + 1
        return sorted(cuenta, key=cuenta.get, reverse=True)

    def _grupos_red(self, red: str) -> list[dict]:
        """Islas de una red: pads unidos entre sí por el cobre que ya existe (pistas y vías)."""
        tol = 0.02
        pads = [f for f in self.formas() if f.red == red]
        pistas = [i for i, p in enumerate(self.pistas) if p["red"] == red]
        vias = [i for i, v in enumerate(self.vias) if v["red"] == red]
        padre: dict = {}

        def raiz(k):
            padre.setdefault(k, k)
            while padre[k] != k:
                padre[k] = padre[padre[k]]
                k = padre[k]
            return k

        def unir(a, b):
            padre[raiz(a)] = raiz(b)

        for k in range(len(pads)):
            raiz(("pad", k))
        for i in pistas:
            p = self.pistas[i]
            raiz(("pista", i))
            for extremo in (p["a"], p["b"]):
                for k, f in enumerate(pads):
                    if p["capa"] in f.capas and f.distancia(*extremo) <= tol:
                        unir(("pista", i), ("pad", k))
                for j in pistas:
                    q = self.pistas[j]
                    if j != i and q["capa"] == p["capa"] and dist_segmento(extremo[0], extremo[1], *q["a"], *q["b"]) <= tol:
                        unir(("pista", i), ("pista", j))
                for j in vias:
                    v = self.vias[j]
                    if math.hypot(extremo[0] - v["x"], extremo[1] - v["y"]) <= v["d"] / 2:
                        unir(("pista", i), ("via", j))
        for j in vias:
            v = self.vias[j]
            raiz(("via", j))
            for k, f in enumerate(pads):
                if f.distancia(v["x"], v["y"]) <= 0:
                    unir(("via", j), ("pad", k))
        for z, (capa, raster) in enumerate(self._rellenos_de(red)):
            clave_zona = ("zona", z)
            raiz(clave_zona)
            for k, f in enumerate(pads):
                if capa in f.capas and _toca_forma(raster, f):
                    unir(clave_zona, ("pad", k))
            for j in vias:
                if raster.dentro(self.vias[j]["x"], self.vias[j]["y"]):
                    unir(clave_zona, ("via", j))
            for i in pistas:
                p = self.pistas[i]
                if p["capa"] == capa and (raster.dentro(*p["a"]) or raster.dentro(*p["b"])):
                    unir(clave_zona, ("pista", i))
        grupos: dict = {}
        for clave in list(padre):
            g = grupos.setdefault(raiz(clave), {"pads": [], "pistas": [], "vias": []})
            tipo, i = clave
            if tipo == "pad":
                g["pads"].append(pads[i])
            elif tipo in ("pista", "via"):
                g[tipo + "s"].append(i)
        return [g for g in grupos.values() if g["pads"]]

    def _rellenos_de(self, red: str) -> list:
        """Cobre relleno de la red por capa (índice de la pila), en rasters; se recalcula tras cada relleno."""
        if self._rellenos is None:
            from .cobre import _cadenas
            from .vista_pcb import _Raster
            self._rellenos = {}
            for z in self.board.Zones():
                if z.GetIsRuleArea() or not z.GetNetname():
                    continue
                for lid in z.GetLayerSet().Seq():
                    if lid in self.pila and z.HasFilledPolysForLayer(lid):
                        cadenas = _cadenas(z.GetFilledPolysList(lid))
                        if cadenas:
                            self._rellenos.setdefault(z.GetNetname(), []).append((self.pila.index(lid), _Raster(cadenas)))
        return self._rellenos.get(red, [])

    def _incompletas(self) -> list[str]:
        return [r for r in self.redes_a_rutear() if len(self._grupos_red(r)) > 1]

    def _rutear_red(self, red: str, conservar: bool = True) -> bool:
        """Completa una red: une sus islas de cobre, empezando por la mayor. Con conservar=False la re-traza entera."""
        if not conservar:
            self.despejar(red, avisar=False)
        ancho = self.ancho(red)
        grupos = self._grupos_red(red)
        if len(grupos) < 2:
            return True
        grupos.sort(key=lambda g: (len(g["pads"]), len(g["pistas"])), reverse=True)
        arbol, restantes = grupos[0], grupos[1:]

        def cercania(f):
            return min(math.hypot(f.cx - t.cx, f.cy - t.cy) for t in arbol["pads"])

        while restantes:
            restantes.sort(key=lambda g: min(cercania(f) for f in g["pads"]))
            grupo = restantes.pop(0)
            pad = min(grupo["pads"], key=cercania)
            rejilla = Rejilla(self, red, ancho)
            objetivos, pad_de = self._objetivos(arbol)
            inicios = [(round(pad.cx / G), round(pad.cy / G), c) for c in pad.capas if c in rejilla.ruteo]
            camino = self._buscar(rejilla, inicios, objetivos)
            if camino is None:
                self._decir(f"Sin ruta para {pad.ref}.{pad.numero} en {red}", "rutear", nivel="error")
                return False
            n_pistas, n_vias = len(self.pistas), len(self.vias)
            destino = pad_de.get(camino[-1])
            vias = self._dibujar(red, ancho, (pad.cx, pad.cy), camino, (destino.cx, destino.cy) if destino else None)
            arbol = {"pads": arbol["pads"] + grupo["pads"],
                     "pistas": arbol["pistas"] + grupo["pistas"] + list(range(n_pistas, len(self.pistas))),
                     "vias": arbol["vias"] + grupo["vias"] + list(range(n_vias, len(self.vias)))}
            texto = f"Pista de {pad.ref}.{pad.numero} a la red {red} ({ancho:g} mm"
            texto += f", {vias} vía{'s' if vias != 1 else ''})" if vias else ")"
            self._paso(texto, "rutear", red=red)
        return True

    def _objetivos(self, arbol: dict):
        objetivos, pad_de = set(), {}
        for f in arbol["pads"]:
            i0, j0 = round(f.cx / G), round(f.cy / G)
            for capa in f.capas:
                objetivos.add((i0, j0, capa))
                pad_de[(i0, j0, capa)] = f
        for indice in arbol["pistas"]:
            p = self.pistas[indice]
            (ax, ay), (bx, by) = p["a"], p["b"]
            pasos = max(1, round(math.hypot(bx - ax, by - ay) / G))
            for k in range(pasos + 1):
                x, y = ax + (bx - ax) * k / pasos, ay + (by - ay) * k / pasos
                i, j = round(x / G), round(y / G)
                if abs(i * G - x) < 1e-3 and abs(j * G - y) < 1e-3:
                    objetivos.add((i, j, p["capa"]))
        for indice in arbol["vias"]:
            v = self.vias[indice]
            for capa in range(len(self.pila)):
                objetivos.add((round(v["x"] / G), round(v["y"] / G), capa))
        return objetivos, pad_de

    def _buscar(self, rej: Rejilla, inicios, objetivos):
        if not objetivos or not inicios:
            return None
        oi0 = min(o[0] for o in objetivos)
        oi1 = max(o[0] for o in objetivos)
        oj0 = min(o[1] for o in objetivos)
        oj1 = max(o[1] for o in objetivos)

        def h(i, j):
            dx = max(oi0 - i, 0, i - oi1)
            dy = max(oj0 - j, 0, j - oj1)
            return max(dx, dy) + 0.4142 * min(dx, dy)

        externas = {0, len(self.pila) - 1}
        abiertos, mejor, padre = [], {}, {}
        for s in inicios:
            clave = (s, 8)
            mejor[clave] = 0.0
            padre[clave] = None
            heapq.heappush(abiertos, (h(s[0], s[1]), 0.0, s, 8))
        cerrados = set()
        while abiertos:
            _, g, celda, d = heapq.heappop(abiertos)
            if (celda, d) in cerrados:
                continue
            cerrados.add((celda, d))
            if celda in objetivos and padre[(celda, d)] is not None:
                camino, clave = [], (celda, d)
                while clave is not None:
                    camino.append(clave[0])
                    clave = padre[clave]
                return camino[::-1]
            i, j, capa = celda
            for k, (dx, dy) in enumerate(DIRS):
                if d != 8 and k == (d + 4) % 8:
                    continue
                ni, nj = i + dx, j + dy
                vecino = (ni, nj, capa)
                es_objetivo = vecino in objetivos
                if not es_objetivo and not rej.libre(ni, nj, capa):
                    continue
                if dx and dy and not es_objetivo and (not rej.libre(i + dx, j, capa) or not rej.libre(i, j + dy, capa)):
                    continue
                giro = 0.0
                if d != 8:
                    giro = {0: 0.0, 1: 0.4, 7: 0.4, 2: 2.0, 6: 2.0, 3: 8.0, 5: 8.0}[(k - d) % 8]
                factor = 1.0 if capa == 0 else 1.25 if capa in externas else 1.1
                costo = g + (1.4142 if dx and dy else 1.0) * factor + giro
                clave = (vecino, k)
                if costo < mejor.get(clave, math.inf):
                    mejor[clave] = costo
                    padre[clave] = (celda, d)
                    heapq.heappush(abiertos, (costo + h(ni, nj), costo, vecino, k))
            if (i, j) not in rej.via and d != 8:
                for otra_capa in rej.ruteo:
                    if otra_capa == capa:
                        continue
                    otra = (i, j, otra_capa)
                    if rej.libre(i, j, otra_capa) or otra in objetivos:
                        clave = (otra, 8)
                        costo = g + 14.0
                        if costo < mejor.get(clave, math.inf):
                            mejor[clave] = costo
                            padre[clave] = (celda, d)
                            heapq.heappush(abiertos, (costo + h(i, j), costo, otra, 8))
        return None

    def _via(self, x: float, y: float, red: str) -> None:
        regla = self.regla(red)
        via = pcbnew.PCB_VIA(self.board)
        via.SetPosition(V(x, y))
        try:
            via.SetWidth(pcbnew.FromMM(regla.via_d))
        except TypeError:
            via.SetWidth(pcbnew.F_Cu, pcbnew.FromMM(regla.via_d))
        via.SetDrill(pcbnew.FromMM(regla.via_t))
        via.SetNet(self.board.FindNet(red))
        self.board.Add(via)
        self.vias.append({"x": x, "y": y, "d": regla.via_d, "red": red})

    def _dibujar(self, red: str, ancho: float, inicio, camino, fin) -> int:
        net = self.board.FindNet(red)
        puntos = [(inicio[0], inicio[1], camino[0][2])]
        for i, j, capa in camino:
            puntos.append((round(i * G, 4), round(j * G, 4), capa))
        if fin:
            puntos.append((fin[0], fin[1], camino[-1][2]))
        limpio = [puntos[0]]
        for p in puntos[1:]:
            if abs(p[0] - limpio[-1][0]) < 1e-6 and abs(p[1] - limpio[-1][1]) < 1e-6 and p[2] == limpio[-1][2]:
                continue
            if len(limpio) >= 2 and limpio[-1][2] == p[2] == limpio[-2][2]:
                a, b = limpio[-2], limpio[-1]
                cruz = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
                if abs(cruz) < 1e-6 and (b[0] - a[0]) * (p[0] - b[0]) + (b[1] - a[1]) * (p[1] - b[1]) >= 0:
                    limpio[-1] = p
                    continue
            limpio.append(p)
        vias = 0
        for a, b in zip(limpio, limpio[1:]):
            if a[2] != b[2]:
                self._via(a[0], a[1], red)
                vias += 1
                continue
            if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6:
                continue
            pista = pcbnew.PCB_TRACK(self.board)
            pista.SetStart(V(a[0], a[1]))
            pista.SetEnd(V(b[0], b[1]))
            pista.SetWidth(pcbnew.FromMM(ancho))
            pista.SetLayer(self.pila[a[2]])
            pista.SetNet(net)
            self.board.Add(pista)
            self.pistas.append({"a": (a[0], a[1]), "b": (b[0], b[1]), "w": ancho, "capa": a[2], "red": red})
        return vias

    # ------------------------------------------------------------ etapa 3: rutear con Freerouting supervisado
    def rutear_freerouting(self, redes: list[str] | None = None, pasadas: int = 20,
                           hilos: int = 1, plazo: float | None = None) -> dict:
        """Freerouting como herramienta, no como jefe: exporto Specctra (sólo las redes pedidas), lo corro sin
        interfaz, importo el resultado y lo acepto sólo si KiCad confirma que mejoró sin romper reglas."""
        import time
        from . import freerouting as fr
        if self.etapa not in ("acomodada", "ruteada", "verificada"):
            raise RuntimeError("primero hay que acomodar los componentes")
        java, jar = fr.localizar()
        trabajo = self.carpeta / ".halo" / "freerouting"
        trabajo.mkdir(parents=True, exist_ok=True)
        self.guardar()
        antes = self._drc_rapido()
        respaldo = trabajo / "antes.kicad_pcb"
        shutil.copy2(self.ruta, respaldo)
        objetivo = set(redes) if redes else None
        ajenas = []
        for t in self.board.GetTracks():
            if objetivo is None or t.GetNetname() in objetivo:
                continue
            if t.GetClass() == "PCB_VIA":
                pos = t.GetPosition()
                ajenas.append(("via", (pos.x, pos.y), self._ancho_via(t), t.GetDrillValue(), t.GetNetname()))
            else:
                a, b = t.GetStart(), t.GetEnd()
                ajenas.append(("pista", (a.x, a.y), (b.x, b.y), t.GetWidth(), t.GetLayer(), t.GetNetname()))
        dsn, ses = trabajo / "placa.dsn", trabajo / "placa.ses"
        ses.unlink(missing_ok=True)
        if not pcbnew.ExportSpecctraDSN(self.board, str(dsn)):
            raise RuntimeError("KiCad no pudo exportar el Specctra DSN")
        reglas = {r: (g.ancho, g.holgura, g.via_d, g.via_t) for r in self.netlist for g in (self.regla(r),)}
        info = fr.preparar_dsn(dsn, objetivo, reglas, min((g[1] for g in reglas.values()), default=self.holgura),
                               planos=set(self.planos) | {self.board.GetLayerName(lid) for lid in self.pila
                                                           if pcbnew.LayerName(lid) in self.planos})
        alcance = ", ".join(sorted(objetivo)) if objetivo else f"todas ({len(info['enviadas'])})"
        self._decir(f"Freerouting con mis reglas: redes {alcance}; {info['clases']} grupos de ancho, holgura y vía"
                    f"{', ' + str(info['protegidas']) + ' tramos ajenos protegidos' if info['protegidas'] else ''}; "
                    f"hasta {pasadas} pasadas", "freerouting")
        if self.sesion:
            self.sesion.halo("actuando", "Freerouting rutea; yo superviso")
        inicio = time.time()
        # El plazo crece con el trabajo: en una placa grande el fan-out solo ya se lleva minutos y media hora
        # fija mataba la corrida entera sin dejar nada. Se puede fijar a mano con HALO_FREEROUTING_PLAZO.
        espera = float(plazo or os.environ.get("HALO_FREEROUTING_PLAZO") or max(3600, 1200 * max(1, pasadas)))
        try:
            r = subprocess.run(fr.comando(java, jar, dsn, ses, pasadas, hilos), capture_output=True, text=True,
                               timeout=espera, encoding="utf-8", errors="replace", env=fr.entorno())
        except subprocess.TimeoutExpired:
            self._decir(f"Freerouting pasó de {espera / 60:.0f} min y lo corto; la placa queda como estaba",
                        "freerouting", nivel="error")
            return {"aceptado": False, "motivo": f"pasó del plazo de {espera / 60:.0f} min"}
        (trabajo / "salida.txt").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
        resumen = fr.resumen_salida(r.stdout + r.stderr)
        if not ses.is_file():
            self._decir(f"Freerouting no produjo resultado (código {r.returncode}); la placa queda como estaba",
                        "freerouting", nivel="error")
            return {"aceptado": False, "motivo": "sin sesión de salida", "salida": str(trabajo / "salida.txt")}
        texto = f"Freerouting terminó en {time.time() - inicio:.0f} s"
        if resumen:
            texto += f": según él, {resumen['sin_rutear']} sin rutear y {resumen['violaciones']} violaciones; lo compruebo con KiCad"
        self._decir(texto, "freerouting")
        if not pcbnew.ImportSpecctraSES(self.board, str(ses)):
            raise RuntimeError("KiCad no pudo importar la sesión de Freerouting")
        # Una pista en una capa de plano parte el plano: nunca se acepta, venga de donde venga.
        en_plano = [t for t in self.board.GetTracks()
                    if t.GetClass() != "PCB_VIA" and pcbnew.LayerName(t.GetLayer()) in self.planos]
        for t in en_plano:
            self.board.Delete(t)
        if en_plano:
            self._decir(f"Quito {len(en_plano)} tramos que Freerouting tendió sobre capas de plano", "freerouting")
        if objetivo is not None:
            for t in list(self.board.GetTracks()):
                if t.GetNetname() not in objetivo:
                    self.board.Delete(t)
            for item in ajenas:
                self._reponer(item)
        self._leer_cobre()
        self.guardar()
        despues = self._drc_rapido()
        no_empeora = despues["sin_conectar"] <= antes["sin_conectar"] and despues["errores"] <= antes["errores"]
        if not no_empeora:
            shutil.copy2(respaldo, self.ruta)
            self.board = pcbnew.LoadBoard(str(self.ruta))
            self.huellas = {fp.GetReference(): fp for fp in self.board.GetFootprints()}
            self._leer_cobre()
            self._paso(f"Rechazo el resultado de Freerouting y dejo la placa como estaba: errores {antes['errores']}→"
                       f"{despues['errores']}, sin conectar {antes['sin_conectar']}→{despues['sin_conectar']}", "freerouting")
            return {"aceptado": False, "antes": antes, "despues": despues, "freerouting": resumen}
        if despues["sin_conectar"] == 0:
            self.etapa = "ruteada"
        self._paso(f"Acepto Freerouting: {len(self.pistas)} tramos, {len(self.vias)} vías; errores {antes['errores']}→"
                   f"{despues['errores']}, sin conectar {antes['sin_conectar']}→{despues['sin_conectar']}", "freerouting")
        return {"aceptado": True, "antes": antes, "despues": despues, "freerouting": resumen}

    @staticmethod
    def _ancho_via(via) -> int:
        try:
            return via.GetWidth(pcbnew.F_Cu)
        except TypeError:
            return via.GetWidth()

    def _reponer(self, item) -> None:
        net = self.board.FindNet(item[-1])
        if item[0] == "via":
            _, (x, y), ancho, taladro, _ = item
            via = pcbnew.PCB_VIA(self.board)
            via.SetPosition(pcbnew.VECTOR2I(x, y))
            try:
                via.SetWidth(ancho)
            except TypeError:
                via.SetWidth(pcbnew.F_Cu, ancho)
            via.SetDrill(taladro)
            via.SetNet(net)
            self.board.Add(via)
        else:
            _, (ax, ay), (bx, by), ancho, capa, _ = item
            pista = pcbnew.PCB_TRACK(self.board)
            pista.SetStart(pcbnew.VECTOR2I(ax, ay))
            pista.SetEnd(pcbnew.VECTOR2I(bx, by))
            pista.SetWidth(ancho)
            pista.SetLayer(capa)
            pista.SetNet(net)
            self.board.Add(pista)

    def _drc_rapido(self) -> dict:
        salida = self.carpeta / ".halo" / "drc-rapido.json"
        data = informe_json([str(CLI), "pcb", "drc", "--format", "json", "--severity-error", "--refill-zones", "--units", "mm",
                             "-o", str(salida), str(self.ruta)], salida,
                            lambda args: subprocess.run(args, capture_output=True, text=True, timeout=600,
                                                        encoding="utf-8", errors="replace"),
                            ("violations", "unconnected_items"))
        return {"errores": len(data.get("violations", [])), "sin_conectar": len(data.get("unconnected_items", []))}

    # ------------------------------------------------------------ serigrafía
    def _drc(self, informe: Path, opciones: tuple, campos: tuple) -> dict:
        return informe_json([str(CLI), "pcb", "drc", "--format", "json", *opciones, "--units", "mm",
                             "-o", str(informe), str(self.ruta)], informe,
                            lambda args: subprocess.run(args, capture_output=True, text=True, timeout=600,
                                                        encoding="utf-8", errors="replace"), campos)

    def _serigrafia_marcada(self, violaciones) -> tuple[set, set]:
        """Referencias de huellas y rótulos de la placa que el DRC marca encimados."""
        refs, rotulos = set(), set()
        for v in violaciones:
            if v.get("type") in ("silk_overlap", "silk_over_copper", "silk_edge_clearance"):
                for item in v.get("items", []):
                    descripcion = str(item.get("description", ""))
                    if descripcion.startswith("Reference field of "):
                        refs.add(descripcion[len("Reference field of "):].split()[0])
                    rotulo = re.match(r"PCB text '(.*)' on ([FB])\.Silkscreen", descripcion)
                    if rotulo:
                        rotulos.add((rotulo.group(1), rotulo.group(2)))
        return refs & set(self.huellas) & self.colocadas, rotulos

    def corregir_serigrafia(self) -> dict:
        """Aparta las referencias y rótulos de serigrafía que el DRC de KiCad marca encimados.
        Es una operación aparte de verificar: cambia la placa, queda en la bitácora y se puede deshacer."""
        self.guardar()
        informe = self.carpeta / ".halo" / "drc-serigrafia.json"
        data = self._drc(informe, ("--severity-all",), ("violations", "unconnected_items"))
        refs, rotulos = self._serigrafia_marcada(data.get("violations", []))
        movidos = self._acomodar_rotulos(forzar=rotulos) if rotulos else 0
        movidas = self._ordenar_referencias(forzar=refs) if refs else 0
        if not refs and not rotulos:
            texto = "KiCad no marca serigrafía encimada"
        else:
            texto = (f"KiCad marcó serigrafía encimada en {len(refs)} referencias y {len(rotulos)} rótulos: "
                     f"aparto {movidas} referencias y {movidos} rótulos")
            if movidas < len(refs) or movidos < len(rotulos):
                texto += "; para el resto no hay hueco cerca"
        self._paso(texto, "serigrafia")
        return {"marcadas": len(refs) + len(rotulos), "referencias": movidas, "rotulos": movidos}

    # ------------------------------------------------------------ etapa 4: verificar
    def verificar(self, _reintento: bool = False, corregir_serigrafia: bool = False) -> dict:
        if self.sesion:
            self.sesion.halo("verificando", "KiCad revisa la placa")
        self.guardar()
        salida = self.carpeta / ".halo"
        salida.mkdir(parents=True, exist_ok=True)
        informe = salida / "drc.json"
        data = informe_json([str(CLI), "pcb", "drc", "--format", "json", "--severity-all", "--schematic-parity",
                            "--refill-zones", "--save-board", "--units", "mm", "-o", str(informe), str(self.ruta)],
                            informe, lambda args: subprocess.run(args, capture_output=True, text=True, timeout=600,
                                                                 encoding="utf-8", errors="replace"),
                            ("violations", "unconnected_items", "schematic_parity"))
        violaciones = data.get("violations", [])
        pendientes = data.get("unconnected_items", [])
        paridad = data.get("schematic_parity", [])
        errores = sum(1 for v in violaciones if v.get("severity") == "error")
        avisos = sum(1 for v in violaciones if v.get("severity") == "warning")
        self.board = pcbnew.LoadBoard(str(self.ruta))
        self.huellas = {fp.GetReference(): fp for fp in self.board.GetFootprints()}
        self._leer_cobre()
        self._prohibidas = None
        self._rellenos = None
        refs, rotulos = self._serigrafia_marcada(violaciones)
        if corregir_serigrafia and (refs or rotulos) and not _reintento:
            movidos = self._acomodar_rotulos(forzar=rotulos) if rotulos else 0
            movidas = self._ordenar_referencias(forzar=refs) if refs else 0
            if movidas or movidos:
                self._decir(f"KiCad marcó serigrafía encimada: corro {movidos} rótulos de la placa y {movidas} referencias "
                            f"y vuelvo a verificar", "serigrafia")
                return self.verificar(_reintento=True, corregir_serigrafia=True)
        resultado = {"drc": {"errores": errores, "avisos": avisos}, "pendientes": len(pendientes),
                     "paridad": len(paridad), "pistas": len(self.pistas), "vias": len(self.vias), "capas": len(self.pila)}
        detalle = {**resultado, "violaciones": violaciones, "sin_conectar": pendientes, "paridad_detalle": paridad}
        (salida / "verificacion-pcb.json").write_text(json.dumps(detalle, indent=2, ensure_ascii=False), encoding="utf-8")
        ok = errores == 0 and not pendientes and not paridad
        if ok:
            self.etapa = "verificada"
        elif self.etapa == 'verificada':
            self.etapa = 'acomodada' if pendientes else 'ruteada'
        self.guardar()
        if self.sesion:
            self.sesion.evento(f"DRC de KiCad: {errores} errores, {avisos} avisos", "drc",
                               nivel="verificacion" if errores == 0 else "error")
            self.sesion.evento(f"Conexiones sin completar: {len(pendientes)}", "pendientes",
                               nivel="verificacion" if not pendientes else "error")
            self.sesion.evento(f"Paridad con el esquemático: {len(paridad)} diferencias", "paridad",
                               nivel="verificacion" if not paridad else "error")
            self.sesion.verificacion(resultado)
            self.sesion.halo("reposo" if ok else "error", "placa verificada" if ok else "hay que revisar")
        return detalle


def _cruzan(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


proteger_motor(Placa, (
    'guardar', 'crear', 'colocar', 'mover', 'rotar', 'voltear', 'fijar', 'acomodar',
    'definir_clase', 'nombrar_capa', 'cobre', 'plano', 'zona', 'prohibir', 'quitar_cobre',
    'rellenar', 'costura', 'bajar_a_plano', 'fijar_ancho', 'despejar', 'rutear', 'rutear_freerouting', 'verificar',
    'corregir_serigrafia',
))
