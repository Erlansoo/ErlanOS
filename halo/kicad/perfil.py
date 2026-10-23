"""Perfil de cada huella, para colocar como lo haría una persona con oficio.

  altura    del modelo 3D de KiCad (STEP) con su escala, rotación y desplazamiento; si no hay
            modelo, del nombre de la huella (…_H7mm, CP_Elec_6.3x7.7) o una estimación prudente
  calor     0 frío, 1 tibio, 2 caliente: reguladores, MOSFET y transistores de potencia, puentes,
            resistencias de potencia, encapsulados con aleta (TO-220, D2PAK, SOT-223…)
  sensible  se degrada con el calor: electrolíticos, cristales, sensores, referencias de tensión
  rápida    toca redes de reloj, cristal, USB, CAN, Ethernet, SPI o pares diferenciales
  fino      paso de pads de 0,65 mm o menos: hay que poder soldarlo y retrabajarlo
  abajo     puede ir en la cara inferior: SMD, bajo, frío y no es conector, pulsador ni indicador
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .biblioteca import KICAD_DIR
from .sesion import ESTADO

MODELOS = KICAD_DIR / "share" / "kicad" / "3dmodels"
CACHE = ESTADO / "alturas.json"
_PUNTO = re.compile(r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([-\d.Ee+]+)\s*,\s*([-\d.Ee+]+)\s*,\s*([-\d.Ee+]+)\s*\)\s*\)")
_cajas: dict | None = None

CALIENTE_HUELLA = re.compile(r"TO-220|TO-247|TO-3P|TO-218|TO-264|TO-263|D2PAK|DDPAK|TO-252|DPAK|PowerPAK|LFPAK|SOT-223|"
                             r"TO-126|HSOP|PowerSO|Heatsink|R_Axial_Power|R_2512|R_Shunt|Diode_Bridge|DO-201|"
                             r"Transformer", re.I)
CALIENTE_VALOR = re.compile(r"78\d\d|79\d\d|LM317|LM337|LM2596|LM2576|LM25\d\d|AMS1117|LD1117|LM1117|NCP1117|XL40\d\d|"
                            r"MP1584|MP2307|MP2315|TPS5\d{2,3}|LT[CM]?3\d{3}|IRF|IRL|STP\d|FQP|AO[DI]?\d{4}|TIP1[0-4]\d|"
                            r"TIP3\d|BD13\d|L298|L293|DRV8\d{3}|BTS\d|VNH|VN\d{3}|ULN200\d|L78|L79|LM7\d", re.I)
PEQUENO = re.compile(r"SOT-23|SC-70|SOT-323|SOT-363|TSOT|SOD-323|SOD-523|0402|0603", re.I)
TIBIO_HUELLA = re.compile(r"SOT-89|EP\d|_EP|PowerDI|SOD-123F|D_SMA|D_SMB|D_SMC|DO-214A[ABC]|Inductor|L_", re.I)
SENSIBLE_HUELLA = re.compile(r"CP_Elec|CP_Radial|CP_Axial|Crystal|Oscillator|Resonator|Thermistor|NTC", re.I)
SENSIBLE_VALOR = re.compile(r"NTC|PTC|LM35|DS18B20|SHT\d|BME\d|BMP\d|TMP\d|MCP9\d|ADS1\d|MCP3\d|HX711|INA\d|REF\d|"
                            r"LM4040|LM385|MAX31|AD\d{4}", re.I)
RED_RAPIDA = re.compile(r"USB|D[+-]$|_DP$|_DM$|D_P$|D_N$|CLK|SCK|SCLK|XTAL|OSC|XIN|XOUT|MCO|HSE|LSE|ETH|RMII|MII|MDIO|"
                        r"MDC|TX[PN]$|RX[PN]$|TD[+-]$|RD[+-]$|CANH|CANL|CAN_?H$|CAN_?L$|HDMI|LVDS|DDR|MIPI|SDIO|SD_CLK|"
                        r"SD_CMD|QSPI|SWCLK|TCK$|PCIE|SATA", re.I)
NO_ABAJO = ("J", "P", "CN", "X", "SW", "S", "BT", "K", "F", "TP", "H", "MH", "LS", "BZ", "RV", "LED", "DS", "U?")


@dataclass
class Perfil:
    ref: str
    huella: str
    valor: str
    altura: float
    fuente_altura: str
    calor: int
    motivo_calor: str
    sensible: bool
    rapida: bool
    fino: bool
    tht: bool
    abajo: bool

    def resumen(self) -> str:
        rasgos = [f"{self.altura:.1f} mm ({self.fuente_altura})"]
        if self.calor:
            rasgos.append(("caliente" if self.calor == 2 else "tibio") + f": {self.motivo_calor}")
        if self.sensible:
            rasgos.append("sensible al calor")
        if self.rapida:
            rasgos.append("señal rápida")
        if self.fino:
            rasgos.append("paso fino")
        rasgos.append("THT" if self.tht else "SMD")
        if self.abajo:
            rasgos.append("puede ir abajo")
        return ", ".join(rasgos)

    def a_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------ altura
def expandir(ruta: str, carpeta: Path) -> Path:
    texto = ruta
    for var in re.findall(r"\$\{([^}]+)\}", ruta):
        if var == "KIPRJMOD":
            valor = str(carpeta)
        elif var.startswith("KICAD") and var.endswith("3DMODEL_DIR"):
            valor = str(MODELOS)
        else:
            valor = os.environ.get(var, "")
        texto = texto.replace("${%s}" % var, valor)
    p = Path(texto)
    return p if p.is_absolute() else Path(carpeta) / p


def _cargar_cache() -> dict:
    global _cajas
    if _cajas is None:
        try:
            _cajas = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.is_file() else {}
        except (OSError, ValueError):
            _cajas = {}
    return _cajas


def guardar_cache() -> None:
    if _cajas is not None:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_cajas), encoding="utf-8")


def caja_modelo(ruta: Path) -> list[float] | None:
    """[xmin, xmax, ymin, ymax, zmin, zmax] del modelo en sus propias coordenadas (mm)."""
    for candidato in (ruta, ruta.with_suffix(".step"), ruta.with_suffix(".stp"), ruta.with_suffix(".STEP")):
        if not candidato.is_file() or candidato.suffix.lower() not in (".step", ".stp"):
            continue
        clave = f"{candidato}|{candidato.stat().st_mtime_ns}"
        cache = _cargar_cache()
        if clave in cache:
            return cache[clave]
        xs, ys, zs = [], [], []
        texto = candidato.read_text(encoding="latin-1", errors="ignore")
        for m in _PUNTO.finditer(texto):
            xs.append(float(m.group(1)))
            ys.append(float(m.group(2)))
            zs.append(float(m.group(3)))
        caja = [min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)] if zs else None
        cache[clave] = caja
        return caja
    return None


def _altura_transformada(caja: list[float], modelo) -> float:
    """Como el visor 3D de KiCad: escala, luego rotación −X, −Y, −Z, luego desplazamiento."""
    sx, sy, sz = modelo.m_Scale.x, modelo.m_Scale.y, modelo.m_Scale.z
    ax, ay = math.radians(-modelo.m_Rotation.x), math.radians(-modelo.m_Rotation.y)
    alto = -math.inf
    for x in caja[0:2]:
        for y in caja[2:4]:
            for z in caja[4:6]:
                x1, y1, z1 = x * sx, y * sy, z * sz
                y1, z1 = y1 * math.cos(ax) - z1 * math.sin(ax), y1 * math.sin(ax) + z1 * math.cos(ax)
                x1, z1 = x1 * math.cos(ay) + z1 * math.sin(ay), -x1 * math.sin(ay) + z1 * math.cos(ay)
                alto = max(alto, z1 + modelo.m_Offset.z)
    return alto


def altura_por_nombre(nombre: str, tht: bool) -> float:
    m = re.search(r"_H(\d+(?:\.\d+)?)mm", nombre)
    if m:
        return float(m.group(1))
    m = re.search(r"CP_Elec_\d+(?:\.\d+)?x(\d+(?:\.\d+)?)", nombre)
    if m:
        return float(m.group(1))
    for patron, alto in ((r"TerminalBlock", 11.0), (r"BarrelJack", 11.0), (r"Relay", 15.7), (r"TO-220.*Vertical", 19.0),
                         (r"TO-247", 21.0), (r"PinHeader|PinSocket", 8.5), (r"USB_A", 7.0), (r"RJ45", 13.5),
                         (r"Crystal_HC49", 4.0), (r"Buzzer", 9.5), (r"Fuseholder", 12.0), (r"Transformer", 20.0)):
        if re.search(patron, nombre, re.I):
            return alto
    return 6.0 if tht else 1.5


def altura(fp, carpeta: Path) -> tuple[float, str]:
    mejor = None
    for modelo in fp.Models():
        if hasattr(modelo, "m_Show") and not modelo.m_Show:
            continue
        caja = caja_modelo(expandir(str(modelo.m_Filename), Path(carpeta)))
        if caja:
            h = _altura_transformada(caja, modelo)
            mejor = h if mejor is None else max(mejor, h)
    nombre = str(fp.GetFPID().GetUniStringLibItemName())
    if mejor is not None and mejor > 0:
        return round(mejor, 2), "modelo 3D"
    return altura_por_nombre(nombre, _es_tht(fp)), "estimada"


# ------------------------------------------------------------ calor, sensibilidad, señales
def _es_tht(fp) -> bool:
    import pcbnew
    return bool(fp.GetAttributes() & pcbnew.FP_THROUGH_HOLE) or any(p.GetDrillSizeX() > 0 for p in fp.Pads())


def prefijo(ref: str) -> str:
    return re.match(r"[A-Za-z_#]*", ref).group(0).upper()


def calor(ref: str, huella: str, valor: str, ancho_max: float) -> tuple[int, str]:
    p = prefijo(ref)
    if CALIENTE_HUELLA.search(huella):
        return 2, huella.split(":")[-1]
    if CALIENTE_VALOR.search(valor) and p in ("U", "Q", "VR", "IC", "D"):
        return (1, f"{valor} en encapsulado pequeño") if PEQUENO.search(huella) else (2, valor)
    if p == "K":
        return 1, "bobina de relé"
    if TIBIO_HUELLA.search(huella) and p in ("U", "Q", "D", "L", "R"):
        return 1, huella.split(":")[-1]
    if ancho_max >= 0.8 and p in ("Q", "D", "U", "R", "L", "F") and not PEQUENO.search(huella):
        return 1, f"en camino de potencia ({ancho_max:g} mm)"
    return 0, ""


def sensible(ref: str, huella: str, valor: str) -> bool:
    return prefijo(ref) in ("Y", "X") or bool(SENSIBLE_HUELLA.search(huella) or SENSIBLE_VALOR.search(valor))


def redes_rapidas(netlist: dict, es_potencia) -> set[str]:
    rapidas = {r for r in netlist if RED_RAPIDA.search(r.lstrip("/")) and not es_potencia(r)}
    for red, nodos in netlist.items():
        if any(prefijo(ref) in ("Y", "X") for ref, _ in nodos) and not es_potencia(red):
            rapidas.add(red)
    return rapidas


def pares_diferenciales(redes) -> list[tuple[str, str]]:
    conjunto, pares = set(redes), []
    for red in sorted(conjunto):
        for a, b in (("_P", "_N"), ("+", "-"), ("DP", "DM"), ("_H", "_L"), ("H", "L")):
            if red.endswith(a) and red[: -len(a)] + b in conjunto:
                pares.append((red, red[: -len(a)] + b))
                break
    return pares


def paso_minimo(fp) -> float:
    import pcbnew
    centros = [(pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)) for p in fp.Pads()]
    if len(centros) < 2 or len(centros) > 400:
        return math.inf
    return min(math.dist(a, b) for i, a in enumerate(centros) for b in centros[i + 1:] if math.dist(a, b) > 1e-3)


def perfilar(huellas: dict, carpeta: Path, redes_de_ref: dict, ancho_de, rapidas: set, altura_abajo: float = 3.0) -> dict:
    perfiles = {}
    for ref, fp in huellas.items():
        nombre = f"{fp.GetFPID().GetLibNickname()}:{fp.GetFPID().GetUniStringLibItemName()}"
        valor = str(fp.GetValue())
        redes = redes_de_ref.get(ref, set())
        alto, fuente = altura(fp, carpeta)
        nivel, motivo = calor(ref, nombre, valor, max((ancho_de(r) for r in redes), default=0.0))
        tht = _es_tht(fp)
        p = prefijo(ref)
        es_indicador = p == "D" and re.search(r"LED", nombre + valor, re.I)
        abajo = (not tht and alto <= altura_abajo and nivel < 2 and p not in NO_ABAJO and not es_indicador)
        perfiles[ref] = Perfil(ref, nombre, valor, alto, fuente, nivel, motivo, sensible(ref, nombre, valor),
                               bool(redes & rapidas), paso_minimo(fp) <= 0.66, tht, bool(abajo))
    guardar_cache()
    return perfiles
