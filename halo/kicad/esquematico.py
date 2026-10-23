"""Constructor y editor nativo de esquemáticos de KiCad 10.

La IA decide el diseño (qué componentes, dónde, qué se conecta, cómo se organiza en
bloques); este motor lo vuelve exacto: símbolos oficiales en rejilla, cables ortogonales
reales, uniones donde hacen falta, potencia, etiquetas locales y globales, bloques con
título. También abre un esquemático existente (incluidos los cambios que el usuario hizo
a mano en el Halo), reconstruye sus redes y permite mover, rotar y reubicar textos con
cables elásticos. Cada operación deja un recibo y KiCad (ERC y netlist) juzga el resultado.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import sexpr as sx
from .biblioteca import Biblioteca
from .geometria import place, transform
from .ruteo import Mapa, celda, celdas_segmento, esquinas
from .sesion import Sesion, escribir_atomico
from .identidad import Simbolos
from .proyecto import revision as revision_proyecto, proteger_motor

GRID = 1.27
VERSION = 20250114
PAPEL = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A5": (210.0, 148.0),
         "A2": (594.0, 420.0), "A1": (841.0, 594.0), "A0": (1189.0, 841.0)}
DIRECCION = {"arriba": (0, -1), "abajo": (0, 1), "izquierda": (-1, 0), "derecha": (1, 0)}
FORMAS = ("input", "output", "bidirectional", "tri_state", "passive")
IGNORAR_AL_ABRIR = ("version", "generator", "generator_version", "uuid", "paper", "title_block", "lib_symbols",
                    "junction", "sheet_instances", "embedded_fonts")
S = sx.Sym


def nuevo_uuid() -> str:
    return str(uuid.uuid4())


def rejilla(v: float, paso: float = GRID) -> float:
    return round(round(v / paso) * paso, 4)


def en_rejilla(v: float) -> bool:
    return abs(v / GRID - round(v / GRID)) < 1e-6


@dataclass
class Simbolo:
    lib_id: str
    ref: str
    valor: str
    at: tuple[float, float]
    rot: int
    unidad: int
    huella: str
    uuid: str
    potencia: bool = False
    espejo: str | None = None
    campos: dict = field(default_factory=dict)
    extra: list = field(default_factory=list)
    datasheet: str | None = None
    descripcion: str | None = None
    banderas: dict = field(default_factory=dict)
    pines_uuid: dict = field(default_factory=dict)
    instancias: list | None = None
    atributos: list = field(default_factory=list)


class Esquematico:
    def __init__(self, carpeta: Path, nombre: str, titulo: str = "", papel: str = "A4",
                 sesion: Sesion | None = None, revision: str = "1", empresa: str = "Erlan"):
        self.carpeta = Path(carpeta)
        self.nombre = nombre
        self.ruta = self.carpeta / f"{nombre}.kicad_sch"
        self.titulo, self.papel, self.revision, self.empresa = titulo, papel, revision, empresa
        self.uuid = nuevo_uuid()
        self.lib = Biblioteca()
        self.simbolos = Simbolos()
        self._uniones_uuid = {}
        self._hoja_instancias = None
        self.solo_lectura = None
        self.cables: list[dict] = []
        self.etiquetas: list[dict] = []
        self.sin_conexion: list[dict] = []
        self.bloques: list[dict] = []
        self.notas: list[dict] = []
        self.extra: list = []
        self.redes: dict[str, set] = {}
        self.pin_red: dict[tuple[str, str], str] = {}
        self.intencion: dict[str, set] | None = None
        self.sesion = sesion
        self._contadores = {"#PWR": 0, "#FLG": 0}
        self._historial: list[dict] = []
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self._revision_proyecto = revision_proyecto(self.carpeta)

    # ------------------------------------------------------------ abrir un archivo existente
    @classmethod
    def abrir(cls, ruta: Path, sesion: Sesion | None = None) -> "Esquematico":
        ruta = Path(ruta)
        raiz = sx.loads(ruta.read_text(encoding="utf-8"))
        tb = sx.find(raiz, "title_block")
        e = cls(ruta.parent, ruta.stem, titulo=str(sx.val(tb, "title", "")) if tb else "",
                papel=str(sx.val(raiz, "paper", "A4")), sesion=sesion,
                revision=str(sx.val(tb, "rev", "1")) if tb else "1",
                empresa=str(sx.val(tb, "company", "")) if tb else "")
        e.uuid = str(sx.val(raiz, "uuid", e.uuid))
        e._hoja_instancias = copy.deepcopy(sx.find(raiz, "sheet_instances"))
        if any(sx.find(raiz, k) is not None for k in ("sheet", "bus", "bus_entry", "hierarchical_label")):
            e.solo_lectura = "Edición de jerarquía/buses pendiente; este esquema sólo se puede visualizar"
        for n in sx.find_all(raiz, "junction"):
            at = sx.find(n, "at")
            e._uniones_uuid[(float(at[1]), float(at[2]))] = copy.deepcopy(n)
        for s in sx.find_all(sx.find(raiz, "lib_symbols"), "symbol"):
            e.lib.registrar(str(s[1]), s)
        rectangulos, textos = [], []
        for nodo in raiz[1:]:
            if not isinstance(nodo, list) or not nodo:
                continue
            clase = nodo[0]
            if clase in IGNORAR_AL_ABRIR:
                continue
            if clase == "symbol":
                s = e._leer_simbolo(nodo)
                e.simbolos[s.ref] = s
                for prefijo in e._contadores:
                    if s.ref.startswith(prefijo) and s.ref[len(prefijo):].isdigit():
                        e._contadores[prefijo] = max(e._contadores[prefijo], int(s.ref[len(prefijo):]))
            elif clase == "wire":
                pts = [(float(p[1]), float(p[2])) for p in sx.find_all(sx.find(nodo, "pts"), "xy")]
                for a, b in zip(pts, pts[1:]):
                    e.cables.append({"a": (rejilla(a[0]), rejilla(a[1])), "b": (rejilla(b[0]), rejilla(b[1])),
                                     "red": "", "uuid": str(sx.val(nodo, "uuid", nuevo_uuid()))})
            elif clase in ("label", "global_label"):
                at = sx.find(nodo, "at")
                e.etiquetas.append({"texto": str(nodo[1]), "at": (rejilla(float(at[1])), rejilla(float(at[2]))),
                                    "ang": int(float(at[3])) if len(at) > 3 else 0, "red": str(nodo[1]),
                                    "uuid": str(sx.val(nodo, "uuid", nuevo_uuid())),
                                    "tipo": "global" if clase == "global_label" else "local",
                                    "forma": str(sx.val(nodo, "shape", "passive"))})
            elif clase == "no_connect":
                at = sx.find(nodo, "at")
                e.sin_conexion.append({"at": (float(at[1]), float(at[2])), "uuid": str(sx.val(nodo, "uuid", nuevo_uuid()))})
            elif clase == "rectangle":
                rectangulos.append(nodo)
            elif clase == "text":
                textos.append(nodo)
            else:
                e.extra.append(nodo)
        e._leer_bloques_y_notas(rectangulos, textos)
        guardada = e.carpeta / ".halo" / "intencion.json"
        if guardada.is_file():
            datos = json.loads(guardada.read_text(encoding="utf-8"))
            e.intencion = {red: {tuple(p) for p in pines} for red, pines in datos.items()}
        e._reconstruir_redes()
        return e

    def _leer_simbolo(self, nodo) -> Simbolo:
        at = sx.find(nodo, "at")
        props, campos, extra = {}, {}, []
        for p in sx.find_all(nodo, "property"):
            clave, valor = str(p[1]), str(p[2])
            props[clave] = valor
            if clave in ("Reference", "Value"):
                pat = sx.find(p, "at")
                efectos = sx.find(p, "effects")
                just = sx.find(efectos, "justify") if efectos else None
                jh = next((str(j) for j in (just[1:] if just else []) if j in ("left", "right")), "center")
                campos[clave] = {"at": (float(pat[1]), float(pat[2])), "ang": float(pat[3]) if len(pat) > 3 else 0.0,
                                 "jh": jh, "oculto": sx.is_yes(p, "hide") or (efectos is not None and sx.is_yes(efectos, "hide"))}
            elif clave not in ("Footprint", "Datasheet", "Description"):
                extra.append(p)
        ref = props.get("Reference", "?")
        espejo = sx.val(nodo, "mirror")
        lib_id = str(sx.val(nodo, "lib_id", ""))
        return Simbolo(lib_id, ref, props.get("Value", ""), (float(at[1]), float(at[2])),
                       int(float(at[3])) % 360 if len(at) > 3 else 0, int(sx.val(nodo, "unit", 1)),
                       props.get("Footprint", ""), str(sx.val(nodo, "uuid", nuevo_uuid())),
                       potencia=lib_id.startswith("power:") or ref.startswith("#"),
                       espejo=str(espejo) if espejo else None, campos=campos, extra=extra,
                       datasheet=props.get("Datasheet"), descripcion=props.get("Description"),
                       banderas={k: str(sx.val(nodo, k)) for k in ("exclude_from_sim", "in_bom", "on_board", "dnp") if sx.find(nodo, k) is not None},
                       pines_uuid={str(p[1]): str(sx.val(p, "uuid")) for p in sx.find_all(nodo, "pin")},
                       instancias=copy.deepcopy(sx.find(nodo, "instances")),
                       atributos=[copy.deepcopy(n) for n in nodo[1:] if isinstance(n, list) and n and n[0] not in
                                  ("lib_id", "at", "mirror", "unit", "exclude_from_sim", "in_bom", "on_board", "dnp", "uuid", "property", "pin", "instances")])

    def _leer_bloques_y_notas(self, rectangulos, textos) -> None:
        usados = set()
        for r in rectangulos:
            a, b = sx.find(r, "start"), sx.find(r, "end")
            x0, y0, x1, y1 = float(a[1]), float(a[2]), float(b[1]), float(b[2])
            bloque = {"titulo": "", "a": (min(x0, x1), min(y0, y1)), "b": (max(x0, x1), max(y0, y1)),
                      "uuid": str(sx.val(r, "uuid", nuevo_uuid())), "texto_uuid": nuevo_uuid()}
            for i, t in enumerate(textos):
                tat = sx.find(t, "at")
                if i not in usados and abs(float(tat[1]) - bloque["a"][0]) < 0.01 and abs(float(tat[2]) - (bloque["a"][1] - 1.27)) < 0.01:
                    bloque["titulo"], bloque["texto_uuid"] = str(t[1]), str(sx.val(t, "uuid", nuevo_uuid()))
                    usados.add(i)
                    break
            self.bloques.append(bloque)
        for i, t in enumerate(textos):
            if i in usados:
                continue
            tat = sx.find(t, "at")
            efectos = sx.find(t, "effects")
            fuente = sx.find(efectos, "font") if efectos else None
            tam = float(sx.find(fuente, "size")[1]) if fuente and sx.find(fuente, "size") else 1.27
            self.notas.append({"texto": str(t[1]), "at": (float(tat[1]), float(tat[2])), "size": tam,
                               "negrita": bool(fuente and sx.is_yes(fuente, "bold")), "uuid": str(sx.val(t, "uuid", nuevo_uuid()))})

    def _reconstruir_redes(self) -> None:
        """Redes por geometría, como las ve KiCad: pines, cables, etiquetas y potencia."""
        padre: dict = {}

        def raiz(k):
            padre.setdefault(k, k)
            while padre[k] != k:
                padre[k] = padre[padre[k]]
                k = padre[k]
            return k

        def unir(a, b):
            padre[raiz(a)] = raiz(b)

        tips: dict[tuple, list] = {}
        for s in self.simbolos.values():
            for p in self.pines_hoja(s):
                tips.setdefault(p["tip"], []).append(("pin", s.ref, p["numero"]))
        for claves in tips.values():
            for k in claves[1:]:
                unir(claves[0], k)
        for i, w in enumerate(self.cables):
            raiz(("cable", i))
            for q, claves in tips.items():
                if q in (w["a"], w["b"]) or _en_interior(q, w["a"], w["b"]):
                    for k in claves:
                        unir(("cable", i), k)
            for j in range(i + 1, len(self.cables)):
                if _tocan(w, self.cables[j]):
                    unir(("cable", i), ("cable", j))
        nombres: dict[str, tuple] = {}
        for i, e in enumerate(self.etiquetas):
            clave = ("etiqueta", i)
            raiz(clave)
            for j, w in enumerate(self.cables):
                if e["at"] in (w["a"], w["b"]) or _en_interior(e["at"], w["a"], w["b"]):
                    unir(clave, ("cable", j))
            for k in tips.get(e["at"], []):
                unir(clave, k)
            if e["texto"] in nombres:
                unir(clave, nombres[e["texto"]])
            else:
                nombres[e["texto"]] = clave
        for s in self.simbolos.values():
            if s.potencia and not s.lib_id.endswith(":PWR_FLAG"):
                pines = self.lib.pines(s.lib_id, s.unidad)
                if not pines:
                    continue
                clave = ("pin", s.ref, pines[0]["numero"])
                raiz(clave)
                if s.valor in nombres:
                    unir(clave, nombres[s.valor])
                else:
                    nombres[s.valor] = clave
        grupos: dict = {}
        for k in list(padre):
            grupos.setdefault(raiz(k), []).append(k)
        self.redes, self.pin_red = {}, {}
        anonimas = 0
        for claves in grupos.values():
            pines = [(k[1], k[2]) for k in claves if k[0] == "pin"]
            nombre = None
            if self.intencion:
                votos: dict[str, int] = {}
                for red, conjunto in self.intencion.items():
                    n = sum(1 for p in pines if p in conjunto)
                    if n:
                        votos[red] = n
                if votos:
                    nombre = max(votos, key=votos.get)
            if not nombre:
                etiquetas = [self.etiquetas[k[1]]["texto"] for k in claves if k[0] == "etiqueta"]
                potencias = [s.valor for k in claves if k[0] == "pin" for s in self.simbolos.candidatos(k[1])
                             if s.potencia and not s.lib_id.endswith(":PWR_FLAG")]
                nombre = (etiquetas or potencias or [None])[0]
            if not nombre:
                anonimas += 1
                nombre = f"N{anonimas}"
            for p in pines:
                self.pin_red[p] = nombre
                self.redes.setdefault(nombre, set()).add(p)
            for k in claves:
                if k[0] == "cable":
                    self.cables[k[1]]["red"] = nombre
                elif k[0] == "etiqueta":
                    self.etiquetas[k[1]]["red"] = nombre

    # ------------------------------------------------------------ geometría
    def area_util(self) -> tuple[float, float, float, float]:
        w, h = PAPEL.get(self.papel, PAPEL["A4"])
        return (7.62, 7.62, w - 7.62, h - 7.62)

    def _t(self, s: Simbolo):
        return transform(s.rot, s.espejo)

    def pines_hoja(self, s: Simbolo) -> list[dict]:
        t = self._t(s)
        return [{**p, "tip": place(s.at, t, p["at"]), "fin": place(s.at, t, p["fin"])}
                for p in self.lib.pines(s.lib_id, s.unidad)]

    def caja(self, s: Simbolo) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = self.lib.cuerpo(s.lib_id, s.unidad)
        t = self._t(s)
        pts = [place(s.at, t, p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def _pin(self, ref: str, numero: str) -> dict:
        if ref not in self.simbolos:
            raise KeyError(f"no existe el símbolo {ref}")
        encontrados = [dict(p, _simbolo=s) for s in self.simbolos.candidatos(ref)
                       for p in self.pines_hoja(s) if p["numero"] == str(numero)]
        if len(encontrados) == 1:
            return encontrados[0]
        if len(encontrados) > 1:
            raise ValueError(f"{ref}.{numero} aparece en varias unidades; usa el UUID de la unidad")
        raise KeyError(f"{ref} no tiene el pin {numero}")

    def pin(self, ref: str, numero) -> tuple[float, float]:
        return self._pin(ref, str(numero))["tip"]

    def salida(self, ref: str, numero) -> tuple[int, int]:
        """Dirección hacia afuera del cuerpo en la punta del pin (en la hoja)."""
        p = self._pin(ref, str(numero))
        dx, dy = p["tip"][0] - p["fin"][0], p["tip"][1] - p["fin"][1]
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            x0, y0, x1, y1 = self.caja(p["_simbolo"])
            dx, dy = p["tip"][0] - (x0 + x1) / 2, p["tip"][1] - (y0 + y1) / 2
        if abs(dx) >= abs(dy):
            return (1 if dx > 0 else -1, 0)
        return (0, 1 if dy > 0 else -1)

    # ------------------------------------------------------------ bitácora e historial
    def _instantanea(self) -> dict:
        return copy.deepcopy({"simbolos": self.simbolos, "cables": self.cables, "etiquetas": self.etiquetas,
                              "sin_conexion": self.sin_conexion, "bloques": self.bloques, "notas": self.notas,
                              "redes": self.redes, "pin_red": self.pin_red, "contadores": self._contadores})

    def _antes(self) -> None:
        if self.solo_lectura:
            raise ValueError(self.solo_lectura)
        self._historial.append(self._instantanea())

    def _paso(self, texto: str, op: str, **extra) -> int:
        if not self.sesion:
            return 0
        self.guardar()
        recibo = self.sesion.evento(texto, op, **extra)
        self.sesion.esperar()
        return recibo

    def _restaurar_memoria(self, estado: dict) -> None:
        for clave in ("simbolos", "cables", "etiquetas", "sin_conexion", "bloques", "notas", "redes", "pin_red"):
            setattr(self, clave, estado[clave])
        self._contadores = estado["contadores"]

    def deshacer(self) -> None:
        if not self._historial:
            raise RuntimeError("no hay nada que deshacer")
        self._restaurar_memoria(self._historial.pop())
        self._paso("Deshago la última operación", "deshacer")

    def _marca_memoria(self) -> int:
        return len(self._historial)

    def _volver_memoria(self, marca: int, cambiados: bool) -> None:
        """Tras un rechazo o un fallo a medias: la memoria vuelve a como estaba antes de la operación."""
        if len(self._historial) > marca:
            estado = self._historial[marca]
            del self._historial[marca:]
            self._restaurar_memoria(estado)

    # ------------------------------------------------------------ organización
    def bloque(self, titulo: str, x0: float, y0: float, x1: float, y1: float) -> dict:
        """Recuadro de un módulo del circuito con su título arriba. Los cables no cruzan su borde."""
        self._antes()
        b = {"titulo": titulo, "a": (rejilla(x0), rejilla(y0)), "b": (rejilla(x1), rejilla(y1)),
             "uuid": nuevo_uuid(), "texto_uuid": nuevo_uuid()}
        self.bloques.append(b)
        self._paso(f"Bloque «{titulo}»", "bloque")
        return b

    def nota(self, texto: str, x: float, y: float, size: float = 1.27, negrita: bool = False) -> None:
        self._antes()
        self.notas.append({"texto": texto, "at": (x, y), "size": size, "negrita": negrita, "uuid": nuevo_uuid()})
        self._paso(f"Nota: {texto}", "nota")

    # ------------------------------------------------------------ operaciones de construcción
    def colocar(self, lib_id: str, ref: str, valor: str, x: float, y: float, rot: int = 0,
                huella: str = "", unidad: int = 1, espejo: str | None = None) -> Simbolo:
        if any(s.unidad == unidad for s in self.simbolos.candidatos(ref)):
            raise ValueError(f"la referencia {ref}, unidad {unidad}, ya existe")
        if any(s.lib_id != lib_id or s.valor != valor or s.huella != huella for s in self.simbolos.candidatos(ref)):
            raise ValueError(f"las unidades de {ref} deben compartir símbolo, valor y huella")
        if not self.lib.existe(lib_id):
            raise KeyError(f"el símbolo {lib_id} no existe en las bibliotecas de KiCad")
        self._antes()
        s = Simbolo(lib_id, ref, valor, (rejilla(x), rejilla(y)), int(rot) % 360, unidad, huella, nuevo_uuid(),
                    potencia=self.lib.bandera(lib_id, "power", "") != "" or ref.startswith("#"), espejo=espejo)
        for p in self.pines_hoja(s):
            if not (en_rejilla(p["tip"][0]) and en_rejilla(p["tip"][1])):
                self._historial.pop()
                raise ValueError(f"{ref} pin {p['numero']} queda fuera de la rejilla en {p['tip']}")
        choques = [o.ref for o in self.simbolos.values() if _cruzan(self.caja(o), self.caja(s))]
        if choques:
            self._historial.pop()
            raise ValueError(f"{ref} se superpone con {', '.join(choques)}")
        self.simbolos[ref] = s
        s.campos = self._campos(s)
        self._paso(f"Coloco {ref} {valor} ({lib_id}) en ({s.at[0]:.2f}, {s.at[1]:.2f})", "colocar", uuid=s.uuid)
        return s

    def _campos(self, s: Simbolo) -> dict:
        t = self._t(s)
        ang = 90 if t.swaps_axes else 0
        campos = {}
        usar_biblioteca = s.potencia or (len(self.lib.pines(s.lib_id, s.unidad)) > 2 and s.rot == 0 and not s.espejo)
        for clave in ("Reference", "Value"):
            if usar_biblioteca:
                prop = self.lib.propiedad(s.lib_id, clave)
                at = place(s.at, t, prop["at"])
                jh = "center" if s.potencia else next((j for j in prop["justify"] if j in ("left", "right")), "center")
                oculto = prop["oculto"] or (s.potencia and clave == "Reference")
                campos[clave] = {"at": at, "ang": ang if t.swaps_axes else prop["ang"], "jh": jh, "oculto": oculto}
            else:
                campos[clave] = {"at": None, "ang": ang, "jh": "center", "oculto": False}
        if not usar_biblioteca:
            self._ubicar_textos(s, campos)
        return campos

    def _ubicar_textos(self, s: Simbolo, campos: dict) -> None:
        """Referencia y valor del lado del símbolo que no tiene pines, para no pisar cables."""
        pines = self.pines_hoja(s)
        x0, y0, x1, y1 = self.caja(s)
        for p in pines:
            x0, y0 = min(x0, p["tip"][0]), min(y0, p["tip"][1])
            x1, y1 = max(x1, p["tip"][0]), max(y1, p["tip"][1])
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        lados = set()
        for p in pines:
            dx, dy = p["tip"][0] - p["fin"][0], p["tip"][1] - p["fin"][1]
            if abs(dx) > abs(dy):
                lados.add("derecha" if dx > 0 else "izquierda")
            elif abs(dy) > 0:
                lados.add("abajo" if dy > 0 else "arriba")
        ancho = max(len(s.ref), len(s.valor)) * 1.27 * 0.8
        if lados == {"izquierda", "derecha"}:
            campos["Reference"]["at"] = (round(cx, 4), round(y0 - 1.905, 4))
            campos["Value"]["at"] = (round(cx, 4), round(y1 + 1.905, 4))
            return
        lado = next((l for l in ("derecha", "izquierda", "arriba", "abajo") if l not in lados), "derecha")
        if lado == "derecha":
            x = x1 + 1.27 + ancho / 2
            posiciones = ((x, cy - 1.27), (x, cy + 1.27))
        elif lado == "izquierda":
            x = x0 - 1.27 - ancho / 2
            posiciones = ((x, cy - 1.27), (x, cy + 1.27))
        elif lado == "arriba":
            posiciones = ((cx, y0 - 4.445), (cx, y0 - 1.905))
        else:
            posiciones = ((cx, y1 + 1.905), (cx, y1 + 4.445))
        campos["Reference"]["at"] = (round(posiciones[0][0], 4), round(posiciones[0][1], 4))
        campos["Value"]["at"] = (round(posiciones[1][0], 4), round(posiciones[1][1], 4))

    def _asignar(self, red: str, extremo: tuple[str, str]) -> None:
        clave = (extremo[0], str(extremo[1]))
        self._pin(*clave)
        previa = self.pin_red.get(clave)
        if previa and previa != red:
            raise ValueError(f"{clave[0]}.{clave[1]} ya pertenece a la red {previa}, no puede ir a {red}")
        self.pin_red[clave] = red
        self.redes.setdefault(red, set()).add(clave)

    def _cable(self, a, b, red: str) -> None:
        a, b = (rejilla(a[0]), rejilla(a[1])), (rejilla(b[0]), rejilla(b[1]))
        if a[0] != b[0] and a[1] != b[1]:
            raise ValueError(f"cable no ortogonal {a} -> {b}")
        cambiado = True
        while cambiado and a != b:
            cambiado = False
            for w in self.cables:
                if w["red"] != red:
                    continue
                for inicio, fin in ((w["a"], w["b"]), (w["b"], w["a"])):
                    if inicio == a and _mismo_sentido(a, fin, b):
                        if _dist(a, fin) >= _dist(a, b):
                            return
                        a, cambiado = fin, True
                        break
                if cambiado:
                    break
        if a != b:
            self.cables.append({"a": a, "b": b, "red": red, "uuid": nuevo_uuid()})

    def trazo(self, nombre: str, *puntos: tuple[float, float]) -> None:
        """Cable dibujado punto por punto; los pines en sus vértices se unen a la red."""
        self._antes()
        puntos = [(rejilla(p[0]), rejilla(p[1])) for p in puntos]
        tips = {p["tip"]: (s.ref, p["numero"]) for s in self.simbolos.values() for p in self.pines_hoja(s)}
        for p in puntos:
            if p in tips:
                self._asignar(nombre, tips[p])
        for a, b in zip(puntos, puntos[1:]):
            self._cable(a, b, nombre)
        self._paso(f"Trazo la red {nombre} ({len(puntos) - 1} tramos)", "trazo", red=nombre)

    def red(self, nombre: str, *extremos: tuple[str, str]) -> None:
        """Une pines con cables reales, como árbol ortogonal que crece desde los ya cableados."""
        if not extremos:
            raise ValueError("una red necesita al menos un pin")
        self._antes()
        extremos = [(e[0], str(e[1])) for e in extremos]
        for e in extremos:
            self._asignar(nombre, e)
        arbol = [e for e in self.redes[nombre] if self._pin_cableado(e)]
        pendientes = [e for e in extremos if e not in arbol]
        if not arbol and not any(w["red"] == nombre for w in self.cables):
            arbol.append(pendientes.pop(0))
        while pendientes:
            objetivos = self._objetivos(nombre, arbol)
            pendientes.sort(key=lambda e: min(_manhattan(celda(self.pin(*e)), c) for c in objetivos))
            e = pendientes.pop(0)
            camino = Mapa(self).ruta(celda(self.pin(*e)), self.salida(*e), objetivos, nombre)
            if camino is None:
                raise RuntimeError(f"no encuentro ruta limpia para {e[0]}.{e[1]} en la red {nombre}; "
                                   "hay que mover componentes")
            puntos = esquinas(camino)
            for a, b in zip(puntos, puntos[1:]):
                self._cable(a, b, nombre)
            arbol.append(e)
            self._paso(f"Cableo {e[0]}.{e[1]} a la red {nombre} ({len(puntos) - 1} tramos)", "cablear", red=nombre)

    def _pin_cableado(self, e) -> bool:
        p = self.pin(*e)
        return any(p in (w["a"], w["b"]) or _en_interior(p, w["a"], w["b"]) for w in self.cables)

    def _objetivos(self, nombre: str, pines: list) -> dict:
        objetivos = {}
        for w in self.cables:
            if w["red"] == nombre:
                for c in celdas_segmento(celda(w["a"]), celda(w["b"])):
                    objetivos[c] = None
        for e in pines:
            d = self.salida(*e)
            objetivos[celda(self.pin(*e))] = (-d[0], -d[1])
        return objetivos

    def potencia(self, lib_id: str, en: tuple[str, str], distancia: float = 2.54, hacia: str | None = None) -> Simbolo:
        """Símbolo de potencia (GND, +5V, PWR_FLAG...) unido al pin por un cable, recto o con un codo."""
        en = (en[0], str(en[1]))
        self._antes()
        pos, d = self.pin(*en), self.salida(*en)
        codo = (rejilla(pos[0] + d[0] * distancia), rejilla(pos[1] + d[1] * distancia))
        destino, sentido = codo, d
        if hacia and DIRECCION[hacia] != d:
            sentido = DIRECCION[hacia]
            destino = (rejilla(codo[0] + sentido[0] * 2.54), rejilla(codo[1] + sentido[1] * 2.54))
        bandera = lib_id.endswith(":PWR_FLAG")
        prefijo = "#FLG" if bandera else "#PWR"
        self._contadores[prefijo] += 1
        ref = f"{prefijo}{self._contadores[prefijo]:02d}"
        valor = self.lib.propiedad(lib_id, "Value")["valor"]
        red = self.pin_red.get(en) if bandera else valor
        if not red:
            raise ValueError(f"PWR_FLAG necesita que {en[0]}.{en[1]} ya tenga red")
        s = Simbolo(lib_id, ref, valor, destino, self._rotacion_hacia(lib_id, sentido), 1, "", nuevo_uuid(),
                    potencia=True)
        self.simbolos[ref] = s
        s.campos = self._campos(s)
        pin1 = self.pines_hoja(s)[0]
        if pin1["tip"] != destino:
            raise AssertionError(f"el pin de {lib_id} no quedó en {destino} sino en {pin1['tip']}")
        self._asignar(red, en)
        self._asignar(red, (ref, pin1["numero"]))
        self._cable(pos, codo, red)
        self._cable(codo, destino, red)
        self._paso(f"{valor} en {en[0]}.{en[1]}" if not bandera else f"Bandera de potencia en la red {red}",
                   "potencia", uuid=s.uuid)
        return s

    def _rotacion_hacia(self, lib_id: str, d: tuple[int, int]) -> int:
        x0, y0, x1, y1 = self.lib.cuerpo(lib_id)
        centro = ((x0 + x1) / 2, (y0 + y1) / 2)
        for rot in (0, 90, 180, 270):
            vx, vy = transform(rot).apply(*centro)
            v = (1 if vx > 0 else -1, 0) if abs(vx) >= abs(vy) else (0, 1 if vy > 0 else -1)
            if v == d:
                return rot
        return 0

    def etiqueta(self, nombre: str, en: tuple[str, str], distancia: float = 2.54, tipo: str = "local",
                 forma: str = "passive") -> None:
        """Etiqueta de red al final de un cable corto. tipo global = puerto entre bloques."""
        if forma not in FORMAS:
            raise ValueError(f"forma de etiqueta desconocida: {forma}")
        en = (en[0], str(en[1]))
        self._antes()
        self._asignar(nombre, en)
        pos, d = self.pin(*en), self.salida(*en)
        fin = (rejilla(pos[0] + d[0] * distancia), rejilla(pos[1] + d[1] * distancia))
        self._cable(pos, fin, nombre)
        ang = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}[d]
        self.etiquetas.append({"texto": nombre, "at": fin, "ang": ang, "red": nombre, "uuid": nuevo_uuid(),
                               "tipo": tipo, "forma": forma})
        self._paso(f"Etiqueta {'global ' if tipo == 'global' else ''}{nombre} en {en[0]}.{en[1]}", "etiqueta")

    def no_conectar(self, en: tuple[str, str]) -> None:
        en = (en[0], str(en[1]))
        self._antes()
        self.sin_conexion.append({"at": self.pin(*en), "uuid": nuevo_uuid()})
        self._paso(f"Marco {en[0]}.{en[1]} sin conexión", "sin_conexion")

    # ------------------------------------------------------------ edición con cables elásticos
    def _buscar(self, clave: str) -> Simbolo:
        if clave in self.simbolos:
            return self.simbolos[clave]
        for s in self.simbolos.values():
            if s.uuid == clave:
                return s
        raise KeyError(f"no existe el símbolo {clave}")

    def _arrastrar(self, viejos: dict, nuevos: dict) -> None:
        """Mueve extremos de cable que tocaban puntos viejos y añade codos para seguir ortogonal."""
        afectados = [(w, ext, otro, viejos[k], nuevos[k]) for w in self.cables
                     for ext, otro in (("a", "b"), ("b", "a")) for k in viejos if w[ext] == viejos[k]]
        agregados = []
        for w, ext, otro, P, Q in afectados:
            if P == Q:
                continue
            R = w[otro]
            if R[1] == P[1] and R[0] != P[0]:
                codo = (Q[0], R[1])
            elif R[0] == P[0] and R[1] != P[1]:
                codo = (R[0], Q[1])
            else:
                codo = Q
            w[ext] = codo
            if codo != Q:
                agregados.append({"a": codo, "b": Q, "red": w["red"], "uuid": nuevo_uuid()})
        self.cables.extend(agregados)
        for k, P in viejos.items():
            for n in self.sin_conexion:
                if n["at"] == P:
                    n["at"] = nuevos[k]
        self._limpiar_cables()

    def _limpiar_cables(self) -> None:
        vistos, limpio = set(), []
        for w in self.cables:
            if w["a"] == w["b"]:
                continue
            clave = tuple(sorted((w["a"], w["b"])))
            if clave in vistos:
                continue
            vistos.add(clave)
            limpio.append(w)
        self.cables = limpio

    def _cadena(self, inicio, paradas: set):
        """Tramos de cable desde `inicio` hasta la primera unión, pin o etiqueta. None si no es una cadena simple."""
        salientes = [w for w in self.cables if inicio in (w["a"], w["b"])]
        if len(salientes) != 1 or any(_en_interior(inicio, w["a"], w["b"]) for w in self.cables):
            return None, None
        tramo, actual, previo = [], inicio, None
        while True:
            siguientes = [w for w in self.cables if w is not previo and actual in (w["a"], w["b"])]
            if len(siguientes) != 1:
                break
            w = siguientes[0]
            fin = w["b"] if w["a"] == actual else w["a"]
            tramo.append(w)
            previo, actual = w, fin
            if fin in paradas:
                break
            toques = [v for v in self.cables if v is not w and (fin in (v["a"], v["b"]) or _en_interior(fin, v["a"], v["b"]))]
            if len(toques) != 1 or _en_interior(fin, toques[0]["a"], toques[0]["b"]):
                break
        return tramo, actual

    def _reconectar(self, s: Simbolo, viejos: dict) -> list[str]:
        """Tras mover o rotar: retira el último tramo de cada pin y lo vuelve a trazar limpio con el
        enrutador (sin cruzar cuerpos ni otras redes). Si no hay ruta, estira el cable como respaldo."""
        otros = {p["tip"]: (o.uuid, p["numero"]) for o in self.simbolos.values() if o is not s for p in self.pines_hoja(o)}
        paradas = set(otros) | set(viejos.values()) | {e["at"] for e in self.etiquetas}
        nuevos = {p["numero"]: p["tip"] for p in self.pines_hoja(s)}
        propios = {v: k for k, v in viejos.items()}
        planes = []
        for num, P in viejos.items():
            red = self.pin_red.get((s.ref, num))
            tramo, fin = self._cadena(P, paradas) if red else (None, None)
            if tramo:
                for w in tramo:
                    self.cables.remove(w)
            planes.append((num, red, tramo, fin))
        estirados = []
        for num, red, tramo, fin in planes:
            nuevo = nuevos[num]
            if not tramo:
                self._arrastrar({num: viejos[num]}, {num: nuevo})
                continue
            objetivos = {}
            for w in self.cables:
                if w["red"] == red:
                    for c in celdas_segmento(celda(w["a"]), celda(w["b"])):
                        objetivos[c] = None
            if fin in propios:
                destino = propios[fin]
                d = self.salida(s.uuid, destino)
                objetivos[celda(nuevos[destino])] = (-d[0], -d[1])
            elif fin in otros:
                d = self.salida(*otros[fin])
                objetivos[celda(fin)] = (-d[0], -d[1])
            else:
                objetivos[celda(fin)] = None
            camino = Mapa(self).ruta(celda(nuevo), self.salida(s.uuid, num), objetivos, red)
            if camino is None:
                self.cables.extend(tramo)
                self._arrastrar({num: viejos[num]}, {num: nuevo})
                estirados.append(f"{s.ref}.{num}")
            else:
                puntos = esquinas(camino)
                for a, b in zip(puntos, puntos[1:]):
                    self._cable(a, b, red)
        self._limpiar_cables()
        return estirados

    def _mover_anexos(self, viejos: dict, nuevos: dict) -> None:
        inverso = {v: k for k, v in viejos.items()}
        for e in self.etiquetas:
            if e["at"] in inverso:
                e["at"] = nuevos[inverso[e["at"]]]
        for n in self.sin_conexion:
            if n["at"] in inverso:
                n["at"] = nuevos[inverso[n["at"]]]

    def mover(self, clave: str, dx: float, dy: float, autor: str = "IA") -> None:
        s = self._buscar(clave)
        dx, dy = rejilla(dx), rejilla(dy)
        if dx == 0 and dy == 0:
            return
        self._antes()
        viejos = {p["numero"]: p["tip"] for p in self.pines_hoja(s)}
        s.at = (rejilla(s.at[0] + dx), rejilla(s.at[1] + dy))
        for c in s.campos.values():
            if c.get("at"):
                c["at"] = (round(c["at"][0] + dx, 4), round(c["at"][1] + dy, 4))
        nuevos = {p["numero"]: p["tip"] for p in self.pines_hoja(s)}
        self._mover_anexos(viejos, nuevos)
        estirados = self._reconectar(s, viejos)
        aviso = f" (cables estirados en {', '.join(estirados)})" if estirados else ""
        self._paso(f"{'Muevo' if autor == 'IA' else 'Moviste'} {s.ref} a ({s.at[0]:.2f}, {s.at[1]:.2f}){aviso}", "mover", autor=autor)

    def _pivotar(self, s, viejos: dict) -> None:
        """Tras girar o voltear: si el símbolo tenía un solo pin conectado, ese pin no se mueve (es el pivote);
        con varios, gira sobre su centro y los cables se vuelven a trazar."""
        extremos = {q for w in self.cables for q in (w["a"], w["b"])}
        conectados = [n for n, tip in viejos.items() if tip in extremos or self.pin_red.get((s.ref, n))]
        if len(conectados) != 1:
            return
        n = conectados[0]
        nuevo = next(p["tip"] for p in self.pines_hoja(s) if p["numero"] == n)
        dx, dy = viejos[n][0] - nuevo[0], viejos[n][1] - nuevo[1]
        s.at = (rejilla(s.at[0] + dx), rejilla(s.at[1] + dy))

    def rotar(self, clave: str, grados: int = 90, autor: str = "IA") -> None:
        s = self._buscar(clave)
        self._antes()
        viejos = {p["numero"]: p["tip"] for p in self.pines_hoja(s)}
        s.rot = (s.rot + grados) % 360
        self._pivotar(s, viejos)
        s.campos = self._campos(s)
        nuevos = {p["numero"]: p["tip"] for p in self.pines_hoja(s)}
        self._mover_anexos(viejos, nuevos)
        estirados = self._reconectar(s, viejos)
        aviso = f" (cables estirados en {', '.join(estirados)})" if estirados else ""
        self._paso(f"{'Roto' if autor == 'IA' else 'Rotaste'} {s.ref} a {s.rot}°{aviso}", "rotar", autor=autor)

    def _componente(self, punto, red: str) -> list[dict]:
        """Cables de `red` unidos físicamente a `punto` (sin contar uniones por nombre de etiqueta)."""
        grupo = [w for w in self.cables if w["red"] == red and (punto in (w["a"], w["b"]) or _en_interior(punto, w["a"], w["b"]))]
        vistos = {id(w) for w in grupo}
        cambiado = True
        while cambiado:
            cambiado = False
            for w in self.cables:
                if w["red"] == red and id(w) not in vistos and any(_tocan(w, v) for v in grupo):
                    grupo.append(w)
                    vistos.add(id(w))
                    cambiado = True
        return grupo

    def _ruta_desde_etiqueta(self, e: dict, Q, objetivos: dict):
        """La ruta más limpia desde el ancla de una etiqueta, con preferencia por el lado natural de su texto."""
        preferido = {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}.get(e["ang"] % 360, (-1, 0))
        mapa = Mapa(self)
        mejor = None
        for d in ((-1, 0), (1, 0), (0, 1), (0, -1)):
            camino = mapa.ruta(celda(Q), d, objetivos, e["red"])
            if camino:
                costo = len(camino) + 3 * (len(esquinas(camino)) - 2) + (0 if d == preferido else 1.5)
                if mejor is None or costo < mejor[0]:
                    mejor = (costo, camino, d)
        return (mejor[1], mejor[2]) if mejor else (None, None)

    def mover_etiqueta(self, clave: str, dx: float, dy: float, autor: str = "IA") -> None:
        """Mueve una etiqueta y vuelve a trazar su cable limpio; el texto queda del lado contrario al cable."""
        e = next((x for x in self.etiquetas if x["uuid"] == clave or x["texto"] == clave), None)
        if e is None:
            raise KeyError(f"no existe la etiqueta {clave}")
        dx, dy = rejilla(dx), rejilla(dy)
        if dx == 0 and dy == 0:
            return
        P = e["at"]
        Q = (rejilla(P[0] + dx), rejilla(P[1] + dy))
        red = e["red"]
        tips = {p["tip"]: (s.ref, p["numero"]) for s in self.simbolos.values() for p in self.pines_hoja(s)}
        if Q in tips and self.pin_red.get(tips[Q]) not in (None, red):
            raise ValueError(f"la etiqueta {e['texto']} quedaría sobre un pin de la red {self.pin_red[tips[Q]]}")
        fundir = set()
        for w in self.cables:
            if w["red"] != red and (Q in (w["a"], w["b"]) or _en_interior(Q, w["a"], w["b"])):
                if self._anonima(w["red"]):
                    fundir.add(w["red"])          # un cable sin nombre toma el nombre de la etiqueta
                else:
                    raise ValueError(f"la etiqueta {e['texto']} quedaría sobre la red {w['red']}")
        self._antes()
        for vieja in fundir:
            self._renombrar(vieja, red)
        paradas = set(tips) | {x["at"] for x in self.etiquetas if x is not e}
        tramo, fin = self._cadena(P, paradas)
        if tramo:
            for w in tramo:
                self.cables.remove(w)
        destino = fin if tramo else P
        e["at"] = Q
        grupo = self._componente(destino, red)
        conectada = Q == destino or any(Q in (w["a"], w["b"]) or _en_interior(Q, w["a"], w["b"]) for w in grupo)
        estirado = False
        if not conectada:
            objetivos = {}
            for w in grupo:
                for c in celdas_segmento(celda(w["a"]), celda(w["b"])):
                    objetivos[c] = None
            if destino in tips:
                d = self.salida(*tips[destino])
                objetivos[celda(destino)] = (-d[0], -d[1])
            else:
                objetivos.setdefault(celda(destino), None)
            camino, sentido = self._ruta_desde_etiqueta(e, Q, objetivos)
            if camino:
                puntos = esquinas(camino)
                for a, b in zip(puntos, puntos[1:]):
                    self._cable(a, b, red)
                e["ang"] = {(-1, 0): 0, (1, 0): 180, (0, 1): 90, (0, -1): 270}[sentido]
            else:
                estirado = True
                if tramo:
                    self.cables.extend(tramo)
                codo = (Q[0], P[1])
                for a, b in ((P, codo), (codo, Q)):
                    if a != b:
                        self.cables.append({"a": a, "b": b, "red": red, "uuid": nuevo_uuid()})
        self._limpiar_cables()
        aviso = " (cable estirado: no hubo ruta limpia)" if estirado else ""
        self._paso(f"{'Muevo' if autor == 'IA' else 'Moviste'} la etiqueta {e['texto']}{aviso}", "mover_etiqueta", autor=autor)

    def espejar(self, clave: str, eje: str = "y", autor: str = "IA") -> None:
        """Voltea un símbolo como KiCad: eje «y» lo refleja en horizontal (izquierda-derecha, tecla X),
        eje «x» en vertical (tecla Y). Volver a voltear en el mismo eje lo deja como estaba; voltear en el
        otro eje equivale a girar 180°, que es lo que KiCad guarda. Los cables se reconectan como al rotar."""
        if eje not in ("x", "y"):
            raise ValueError("el eje de espejo es «x» o «y»")
        s = self._buscar(clave)
        self._antes()
        viejos = {p["numero"]: p["tip"] for p in self.pines_hoja(s)}
        if s.espejo == eje:
            s.espejo = None
        elif s.espejo:
            s.espejo = None
            s.rot = (s.rot + 180) % 360
        else:
            s.espejo = eje
        self._pivotar(s, viejos)
        s.campos = self._campos(s)
        nuevos = {p["numero"]: p["tip"] for p in self.pines_hoja(s)}
        self._mover_anexos(viejos, nuevos)
        estirados = self._reconectar(s, viejos)
        aviso = f" (cables estirados en {', '.join(estirados)})" if estirados else ""
        sentido = "horizontal" if eje == "y" else "vertical"
        self._paso(f"{'Volteo' if autor == 'IA' else 'Volteaste'} {s.ref} en {sentido}{aviso}", "espejar", autor=autor)

    def rotar_etiqueta(self, clave: str, grados: int = 90, autor: str = "IA") -> None:
        """Gira una etiqueta sobre su punto de conexión: el cable no se toca, sólo cambia hacia dónde sale el texto."""
        e = next((x for x in self.etiquetas if x["uuid"] == clave or x["texto"] == clave), None)
        if e is None:
            raise KeyError(f"no existe la etiqueta {clave}")
        self._antes()
        e["ang"] = (int(e.get("ang", 0)) + int(grados)) % 360
        self._paso(f"{'Roto' if autor == 'IA' else 'Rotaste'} la etiqueta {e['texto']}", "rotar_etiqueta", autor=autor)

    def mover_bloque(self, clave: str, dx: float, dy: float, autor: str = "IA") -> None:
        """Mueve un módulo entero: recuadro, título, símbolos, cables, etiquetas, marcas y notas."""
        b = next((x for x in self.bloques if x["uuid"] == clave or x["titulo"] == clave), None)
        if b is None:
            raise KeyError(f"no existe el bloque {clave}")
        dx, dy = rejilla(dx), rejilla(dy)
        if dx == 0 and dy == 0:
            return
        (x0, y0), (x1, y1) = b["a"], b["b"]
        nombre = b["titulo"] or "sin título"

        def dentro(p) -> bool:
            return x0 < p[0] < x1 and y0 < p[1] < y1

        def mover_punto(p):
            return (rejilla(p[0] + dx), rejilla(p[1] + dy))

        nuevo = (round(x0 + dx, 4), round(y0 + dy, 4), round(x1 + dx, 4), round(y1 + dy, 4))
        ax0, ay0, ax1, ay1 = self.area_util()
        if nuevo[0] < ax0 or nuevo[1] - 3.81 < ay0 or nuevo[2] > ax1 or nuevo[3] > ay1:
            raise ValueError(f"el bloque «{nombre}» saldría de la hoja")
        holgado = (nuevo[0] - 2.54, nuevo[1] - 3.81, nuevo[2] + 2.54, nuevo[3] + 2.54)
        for otro in self.bloques:
            if otro is not b and _cruzan(holgado, (*otro["a"], *otro["b"])):
                raise ValueError(f"el bloque «{nombre}» chocaría con «{otro['titulo'] or 'sin título'}»")
        miembros = []
        for s in self.simbolos.values():
            cx0, cy0, cx1, cy1 = self.caja(s)
            if dentro(((cx0 + cx1) / 2, (cy0 + cy1) / 2)):
                miembros.append(s)
            elif _cruzan(nuevo, (cx0, cy0, cx1, cy1)):
                raise ValueError(f"el bloque «{nombre}» taparía a {s.ref}")
        for w in self.cables:
            if dentro(w["a"]) or dentro(w["b"]):
                continue
            caja_w = (min(w["a"][0], w["b"][0]) - 0.01, min(w["a"][1], w["b"][1]) - 0.01,
                      max(w["a"][0], w["b"][0]) + 0.01, max(w["a"][1], w["b"][1]) + 0.01)
            if _cruzan(caja_w, nuevo):
                raise ValueError(f"el bloque «{nombre}» quedaría encima de un cable de la red {w['red']}")
        for e in self.etiquetas:
            if not dentro(e["at"]) and nuevo[0] <= e["at"][0] <= nuevo[2] and nuevo[1] <= e["at"][1] <= nuevo[3]:
                raise ValueError(f"el bloque «{nombre}» quedaría encima de la etiqueta {e['texto']}")
        self._antes()
        cruzan = {}
        for w in self.cables:
            da, db = dentro(w["a"]), dentro(w["b"])
            if da and db:
                w["a"], w["b"] = mover_punto(w["a"]), mover_punto(w["b"])
            elif da or db:
                P = w["a"] if da else w["b"]
                cruzan[str(P)] = P
        for s in miembros:
            s.at = mover_punto(s.at)
            for c in s.campos.values():
                if c.get("at"):
                    c["at"] = (round(c["at"][0] + dx, 4), round(c["at"][1] + dy, 4))
        for item in [*self.etiquetas, *self.sin_conexion]:
            if dentro(item["at"]):
                item["at"] = mover_punto(item["at"])
        for n in self.notas:
            if dentro(n["at"]):
                n["at"] = (round(n["at"][0] + dx, 4), round(n["at"][1] + dy, 4))
        b["a"], b["b"] = (nuevo[0], nuevo[1]), (nuevo[2], nuevo[3])
        if cruzan:
            self._arrastrar(cruzan, {k: mover_punto(v) for k, v in cruzan.items()})
        self._limpiar_cables()
        self._paso(f"{'Muevo' if autor == 'IA' else 'Moviste'} el bloque «{nombre}» con {len(miembros)} símbolos",
                   "mover_bloque", autor=autor)

    def mover_campo(self, clave: str, campo: str, dx: float, dy: float, autor: str = "IA") -> None:
        s = self._buscar(clave)
        c = s.campos.get(campo)
        if not c:
            raise KeyError(f"{s.ref} no tiene el campo {campo}")
        self._antes()
        c["at"] = (round(c["at"][0] + rejilla(dx, 0.635), 4), round(c["at"][1] + rejilla(dy, 0.635), 4))
        nombre = "referencia" if campo == "Reference" else "valor"
        self._paso(f"{'Muevo' if autor == 'IA' else 'Moviste'} el texto de {nombre} de {s.ref}", "mover_campo", autor=autor)

    # ------------------------------------------------------------ uniones
    # ------------------------------------------------------------ cables a mano, como en KiCad
    def _cable_por_uuid(self, clave: str) -> dict:
        w = next((x for x in self.cables if x["uuid"] == clave), None)
        if w is None:
            raise KeyError(f"no existe el cable {clave}")
        return w

    def _tips(self) -> dict:
        firma = tuple((id(s), s.at, s.rot, s.espejo) for s in self.simbolos.values())
        if getattr(self, "_tips_firma", None) != firma:
            self._tips_cache = {p["tip"]: (s.ref, p["numero"]) for s in self.simbolos.values() for p in self.pines_hoja(s)}
            self._tips_firma = firma
        return self._tips_cache

    def _choque(self, a, b, red) -> str | None:
        """Qué pin o cable de otra red tocaría el tramo a-b (sería un cortocircuito)."""
        for tip, pin in self._tips().items():
            if tip in (a, b) or _en_interior(tip, a, b):
                otra = self.pin_red.get(pin)
                if otra and red and otra != red:
                    return f"el pin {pin[0]}.{pin[1]} (red {otra})"
        for w in self.cables:
            if w["red"] and red and w["red"] != red and _tocan(w, {"a": a, "b": b}):
                return f"la red {w['red']}"
        return None

    def _ocupado(self, p, salvo: dict) -> bool:
        """¿Hay algo en p aparte del cable `salvo`: un pin, una etiqueta, un no-conectar o el interior de otro cable?"""
        if p in self._tips() or any(e["at"] == p for e in self.etiquetas) or any(n["at"] == p for n in self.sin_conexion):
            return True
        return any(v is not salvo and _en_interior(p, v["a"], v["b"]) for v in self.cables)

    def mover_cable(self, clave: str, dx: float, dy: float, autor: str = "IA") -> None:
        """Arrastra un tramo como en KiCad: se desplaza perpendicular a sí mismo; el vecino en escuadra se
        estira o se encoge, y donde el extremo tocaba un pin, una unión u otro cable se añade un codo para
        no soltarlo. Se rechaza si quedaría sobre un pin o un cable de otra red."""
        w = self._cable_por_uuid(clave)
        a, b = w["a"], w["b"]
        horizontal = a[1] == b[1]
        d = rejilla(dy) if horizontal else rejilla(dx)
        if d == 0:
            return
        mover = (lambda p: (p[0], rejilla(p[1] + d))) if horizontal else (lambda p: (rejilla(p[0] + d), p[1]))
        na, nb = mover(a), mover(b)
        choque = self._choque(na, nb, w["red"])
        if choque:
            raise ValueError(f"el cable quedaría sobre {choque}")
        self._antes()
        codos = []
        for viejo, nuevo in ((a, na), (b, nb)):
            vecinos = [v for v in self.cables if v is not w and viejo in (v["a"], v["b"])]
            escuadra = [v for v in vecinos if (v["a"][1] == v["b"][1]) != horizontal]
            if len(vecinos) == 1 and escuadra and not self._ocupado(viejo, w):
                v = escuadra[0]                      # el vecino en escuadra sigue al extremo
                if v["a"] == viejo:
                    v["a"] = nuevo
                else:
                    v["b"] = nuevo
            elif vecinos or self._ocupado(viejo, w):
                codos.append({"a": nuevo, "b": viejo, "red": w["red"], "uuid": nuevo_uuid()})
        for e in self.etiquetas:                 # una etiqueta apoyada en medio del tramo viaja con él
            if _en_interior(e["at"], a, b):
                e["at"] = mover(e["at"])
        w["a"], w["b"] = na, nb
        self.cables.extend(codos)
        self._limpiar_cables()
        self._paso(f"{'Muevo' if autor == 'IA' else 'Moviste'} un cable de la red {w['red']}", "mover_cable", autor=autor)

    def trazar_cable(self, puntos, autor: str = "IA") -> str:
        """Cable dibujado a mano, punto a punto y en escuadra. Toma la red de lo que toca (pin, cable o
        etiqueta); si no toca nada, estrena una red; si tocara dos redes distintas, se rechaza."""
        pts: list = []
        for p in puntos:
            q = (rejilla(float(p[0])), rejilla(float(p[1])))
            if not pts or q != pts[-1]:
                pts.append(q)
        if len(pts) < 2:
            raise ValueError("un cable necesita al menos dos puntos distintos")
        for a, b in zip(pts, pts[1:]):
            if a[0] != b[0] and a[1] != b[1]:
                raise ValueError("los tramos deben ir en horizontal o en vertical")
        tips = self._tips()
        redes, pines = set(), set()
        for a, b in zip(pts, pts[1:]):
            for tip, pin in tips.items():
                if tip in (a, b) or _en_interior(tip, a, b):
                    pines.add(pin)
                    if self.pin_red.get(pin):
                        redes.add(self.pin_red[pin])
            for w in self.cables:
                if _tocan(w, {"a": a, "b": b}) and w["red"]:
                    redes.add(w["red"])
            for e in self.etiquetas:
                if (e["at"] in (a, b) or _en_interior(e["at"], a, b)) and e.get("red"):
                    redes.add(e["red"])
        nombradas = {r for r in redes if not self._anonima(r)}
        if len(nombradas) > 1:
            raise ValueError(f"ese cable uniría las redes {', '.join(sorted(nombradas))}")
        red = nombradas.pop() if nombradas else (sorted(redes)[0] if redes else self._red_libre())
        self._antes()
        for vieja in redes - {red}:               # las redes sin nombre que toca pasan a llamarse como esta
            self._renombrar(vieja, red)
        for pin in pines:
            self._asignar(red, pin)
        for a, b in zip(pts, pts[1:]):
            self._cable(a, b, red)
        self._paso(f"{'Trazo' if autor == 'IA' else 'Trazaste'} un cable de la red {red} ({len(pts) - 1} tramos)",
                   "trazar_cable", autor=autor, red=red)
        return red

    def _anonima(self, red: str) -> bool:
        return bool(re.fullmatch(r"N\d+", red or "")) and not any(e.get("red") == red for e in self.etiquetas)

    def _renombrar(self, vieja: str, nueva: str) -> None:
        for w in self.cables:
            if w["red"] == vieja:
                w["red"] = nueva
        for pin, r in list(self.pin_red.items()):
            if r == vieja:
                self.pin_red[pin] = nueva
        if vieja in self.redes:
            self.redes.setdefault(nueva, set()).update(self.redes.pop(vieja))

    def _red_libre(self) -> str:
        n = 1
        while f"N{n}" in self.redes or any(w["red"] == f"N{n}" for w in self.cables):
            n += 1
        return f"N{n}"

    def borrar_cable(self, clave: str, autor: str = "IA") -> None:
        w = self._cable_por_uuid(clave)
        self._antes()
        self.cables.remove(w)
        self._paso(f"{'Borro' if autor == 'IA' else 'Borraste'} un cable de la red {w['red']}", "borrar_cable", autor=autor)

    def uniones(self) -> list[tuple[float, float]]:
        tips = {p["tip"] for s in self.simbolos.values() for p in self.pines_hoja(s)}
        segmentos = [(w["a"], w["b"]) for w in self.cables]
        salida = []
        for p in {q for seg in segmentos for q in seg}:
            n = sum(1 if p in (a, b) else 2 if _en_interior(p, a, b) else 0 for a, b in segmentos)
            if p in tips:
                n += 1
            if n >= 3:
                salida.append(p)
        return sorted(salida)

    # ------------------------------------------------------------ archivo
    def a_sexpr(self) -> list:
        raiz = [S("kicad_sch"), [S("version"), VERSION], [S("generator"), "halo"], [S("generator_version"), "0.2"],
                [S("uuid"), self.uuid], [S("paper"), self.papel],
                [S("title_block"), [S("title"), self.titulo], [S("date"), date.today().isoformat()],
                 [S("rev"), self.revision], [S("company"), self.empresa]]]
        libs = [S("lib_symbols")]
        for lib_id in sorted({s.lib_id for s in self.simbolos.values()}):
            libs.append(self.lib.plano(lib_id))
        raiz.append(libs)
        for p in self.uniones():
            if p not in self._uniones_uuid:
                self._uniones_uuid[p] = [S("junction"), [S("at"), p[0], p[1]], [S("diameter"), 0], [S("color"), 0, 0, 0, 0],
                                         [S("uuid"), nuevo_uuid()]]
            raiz.append(copy.deepcopy(self._uniones_uuid[p]))
        for n in self.sin_conexion:
            raiz.append([S("no_connect"), [S("at"), n["at"][0], n["at"][1]], [S("uuid"), n["uuid"]]])
        for w in self.cables:
            raiz.append([S("wire"), [S("pts"), [S("xy"), *w["a"]], [S("xy"), *w["b"]]],
                         [S("stroke"), [S("width"), 0], [S("type"), S("default")]], [S("uuid"), w["uuid"]]])
        for b in self.bloques:
            raiz.append([S("rectangle"), [S("start"), *b["a"]], [S("end"), *b["b"]],
                         [S("stroke"), [S("width"), 0.254], [S("type"), S("dash")]], [S("fill"), [S("type"), S("none")]],
                         [S("uuid"), b["uuid"]]])
            if b["titulo"]:
                raiz.append([S("text"), b["titulo"], [S("exclude_from_sim"), S("no")],
                             [S("at"), b["a"][0], round(b["a"][1] - 1.27, 4), 0],
                             [S("effects"), [S("font"), [S("size"), 2.0, 2.0], [S("thickness"), 0.4], [S("bold"), S("yes")]],
                              [S("justify"), S("left"), S("bottom")]], [S("uuid"), b["texto_uuid"]]])
        for n in self.notas:
            fuente = [S("font"), [S("size"), n["size"], n["size"]]]
            if n["negrita"]:
                fuente.append([S("bold"), S("yes")])
            raiz.append([S("text"), n["texto"], [S("exclude_from_sim"), S("no")], [S("at"), n["at"][0], n["at"][1], 0],
                         [S("effects"), fuente, [S("justify"), S("left"), S("bottom")]], [S("uuid"), n["uuid"]]])
        for e in self.etiquetas:
            x, y = e["at"]
            if e.get("tipo") == "global":
                just = "left" if e["ang"] in (0, 90) else "right"
                raiz.append([S("global_label"), e["texto"], [S("shape"), S(e.get("forma", "passive"))],
                             [S("at"), x, y, e["ang"]], [S("fields_autoplaced"), S("yes")],
                             [S("effects"), [S("font"), [S("size"), 1.27, 1.27]], [S("justify"), S(just)]],
                             [S("uuid"), e["uuid"]],
                             [S("property"), "Intersheetrefs", "${INTERSHEET_REFS}", [S("at"), x, y, 0], [S("hide"), S("yes")],
                              [S("effects"), [S("font"), [S("size"), 1.27, 1.27]], [S("justify"), S("left")]]]])
            else:
                just = ["left", "bottom"] if e["ang"] in (0, 90) else ["right", "bottom"]
                raiz.append([S("label"), e["texto"], [S("at"), x, y, e["ang"]],
                             [S("effects"), [S("font"), [S("size"), 1.27, 1.27]], [S("justify"), *map(S, just)]],
                             [S("uuid"), e["uuid"]]])
        for nodo in self.extra:
            raiz.append(nodo)
        for s in self.simbolos.values():
            raiz.append(self._nodo_simbolo(s))
        raiz.append(copy.deepcopy(self._hoja_instancias) or [S("sheet_instances"), [S("path"), "/", [S("page"), "1"]]])
        raiz.append([S("embedded_fonts"), S("no")])
        return raiz

    def _nodo_simbolo(self, s: Simbolo) -> list:
        lib = self.lib
        nodo = [S("symbol"), [S("lib_id"), s.lib_id], [S("at"), s.at[0], s.at[1], s.rot]]
        if s.espejo:
            nodo.append([S("mirror"), S(s.espejo)])
        nodo += [[S("unit"), s.unidad],
                 [S("exclude_from_sim"), S(s.banderas.get("exclude_from_sim", lib.bandera(s.lib_id, "exclude_from_sim", "no")))],
                 [S("in_bom"), S(s.banderas.get("in_bom", lib.bandera(s.lib_id, "in_bom", "yes")))],
                 [S("on_board"), S(s.banderas.get("on_board", lib.bandera(s.lib_id, "on_board", "yes")))],
                 [S("dnp"), S(s.banderas.get("dnp", "no"))], [S("uuid"), s.uuid]]
        nodo.extend(copy.deepcopy(s.atributos))

        def prop(clave, valor, at, ang=0, jh="center", oculto=False):
            efectos = [S("effects"), [S("font"), [S("size"), 1.27, 1.27]]]
            if jh != "center":
                efectos.append([S("justify"), S(jh)])
            p = [S("property"), clave, valor, [S("at"), at[0], at[1], ang]]
            if oculto:
                p.append([S("hide"), S("yes")])
            p.append(efectos)
            return p

        for clave, valor in (("Reference", s.ref), ("Value", s.valor)):
            c = s.campos[clave]
            nodo.append(prop(clave, valor, c["at"], c["ang"], c["jh"], c["oculto"]))
        nodo.append(prop("Footprint", s.huella, s.at, oculto=True))
        datasheet = s.datasheet if s.datasheet is not None else lib.propiedad(s.lib_id, "Datasheet")["valor"]
        descripcion = s.descripcion if s.descripcion is not None else lib.propiedad(s.lib_id, "Description")["valor"]
        nodo.append(prop("Datasheet", datasheet, s.at, oculto=True))
        nodo.append(prop("Description", descripcion, s.at, oculto=True))
        nodo.extend(s.extra)
        for p in lib.pines(s.lib_id, s.unidad):
            if p["numero"] not in s.pines_uuid:
                s.pines_uuid[p["numero"]] = nuevo_uuid()
        for numero, ident in s.pines_uuid.items():
            nodo.append([S("pin"), numero, [S("uuid"), ident]])
        nodo.append(copy.deepcopy(s.instancias) or [S("instances"), [S("project"), self.nombre,
                                      [S("path"), f"/{self.uuid}", [S("reference"), s.ref], [S("unit"), s.unidad]]]])
        return nodo

    def guardar(self) -> Path:
        if self.solo_lectura:
            raise ValueError(self.solo_lectura)
        escribir_atomico(self.ruta, sx.dumps(self.a_sexpr()) + "\n")
        pro = self.carpeta / f"{self.nombre}.kicad_pro"
        if not pro.is_file():
            escribir_atomico(pro, json.dumps({"meta": {"filename": pro.name, "version": 3},
                                              "sheets": [[self.uuid, "Root"]]}, indent=2))
        return self.ruta

    # ------------------------------------------------------------ verificación
    def verificar(self, actualizar_intencion: bool = True) -> dict:
        """ERC + netlist frente a la intención + revisión de dibujo.

        actualizar_intencion=True (diseño de la IA): lo conectado ahora pasa a ser la intención.
        False (cambios a mano): se compara contra la última intención verificada.
        """
        from . import oraculo, reglas
        if self.sesion:
            self.sesion.halo("verificando", "KiCad revisa el esquemático")
        self.guardar()
        salida = self.carpeta / ".halo"
        salida.mkdir(parents=True, exist_ok=True)
        intencion = self.redes if (actualizar_intencion or not self.intencion) else self.intencion
        dibujo = reglas.revisar(self)
        informe = oraculo.erc(self.ruta, salida / "erc.json")
        obtenidas = oraculo.netlist(self.ruta, salida / "netlist.net")
        comparacion = oraculo.comparar(intencion, obtenidas)
        if actualizar_intencion and comparacion["coinciden"]:
            (salida / "intencion.json").write_text(json.dumps({r: sorted(map(list, p)) for r, p in self.redes.items()},
                                                              indent=1, ensure_ascii=False), encoding="utf-8")
            self.intencion = {r: set(p) for r, p in self.redes.items()}
        resultado = {"erc": {"errores": informe["errores"], "avisos": informe["avisos"]},
                     "netlist": {k: comparacion[k] for k in ("coinciden", "redes", "diferencias")},
                     "reglas": {"problemas": len(dibujo["problemas"]), "cruces": dibujo["cruces"]},
                     "erc_detalle": informe["violaciones"]}
        detalle = {**resultado, "netlist_detalle": comparacion, "erc_detalle": informe["violaciones"],
                   "reglas_detalle": dibujo}
        (salida / "verificacion.json").write_text(json.dumps(detalle, indent=2, ensure_ascii=False, default=list),
                                                  encoding="utf-8")
        if self.sesion:
            self.sesion.evento(f"ERC de KiCad: {informe['errores']} errores, {informe['avisos']} avisos", "erc",
                               nivel="verificacion" if informe["errores"] == 0 else "error")
            texto = (f"Netlist de KiCad idéntica a la intención: {comparacion['redes']} redes"
                     if comparacion["coinciden"] else f"Netlist distinta: {comparacion['diferencias']} diferencias")
            self.sesion.evento(texto, "netlist", nivel="verificacion" if comparacion["coinciden"] else "error")
            self.sesion.evento(f"Revisión de dibujo: {len(dibujo['problemas'])} problemas, {dibujo['cruces']} cruces",
                               "reglas", nivel="verificacion" if not dibujo["problemas"] else "error")
            self.sesion.verificacion(resultado)
            ok = informe["errores"] == 0 and comparacion["coinciden"] and not dibujo["problemas"]
            self.sesion.halo("reposo" if ok else "error", "verificado" if ok else "hay que revisar")
        return detalle


def _cruzan(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _manhattan(a, b) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _dist(a, b) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _mismo_sentido(origen, p, q) -> bool:
    """p y q están sobre la misma semirrecta que parte de origen."""
    if origen[0] == p[0] == q[0]:
        return (p[1] - origen[1]) * (q[1] - origen[1]) > 0
    if origen[1] == p[1] == q[1]:
        return (p[0] - origen[0]) * (q[0] - origen[0]) > 0
    return False


def _en_interior(p, a, b) -> bool:
    if a[0] == b[0] == p[0]:
        return min(a[1], b[1]) < p[1] < max(a[1], b[1])
    if a[1] == b[1] == p[1]:
        return min(a[0], b[0]) < p[0] < max(a[0], b[0])
    return False


def _tocan(w, v) -> bool:
    for p in (w["a"], w["b"]):
        if p in (v["a"], v["b"]) or _en_interior(p, v["a"], v["b"]):
            return True
    for p in (v["a"], v["b"]):
        if _en_interior(p, w["a"], w["b"]):
            return True
    return False


proteger_motor(Esquematico, (
    'bloque', 'nota', 'colocar', 'trazo', 'red', 'potencia', 'etiqueta', 'no_conectar',
    'mover', 'rotar', 'mover_etiqueta', 'mover_bloque', 'mover_campo', 'mover_cable', 'trazar_cable', 'borrar_cable', 'rotar_etiqueta', 'espejar',
    'deshacer', 'guardar', 'verificar',
))
