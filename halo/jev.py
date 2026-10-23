"""Segunda opinión de Jev (TypeSafe AI) para el Halo y para su IA.

Jev no escribe texto: dado un «estado» (lo que se sabe) y una pregunta tipada, devuelve la elección con
una probabilidad calibrada y una confianza, en menos de un segundo y por centavos de centavo. En las
sondas del 21/09/2026 acertó las decisiones descritas en el estado (arranque en frío A/B, triage de
verificación) pero no sabe datasheets y se inventó un dato de una pieza inexistente con confianza 0,43:
por eso aquí toda respuesta con confianza bajo el umbral, o que elige «no_se», se marca como DUDOSA, y
la pieza sólo se usa para juzgar lo que va en el estado, nunca como fuente de datos.

La clave vive sólo en la variable de entorno del usuario TYPESAFE_API_KEY (en Windows se lee también del
registro, para no depender de la consola que la puso). Nunca va en archivos ni en la línea de órdenes.

    python -m halo.jev "estado…" -p "¿pregunta?" -o A="texto" -o B="texto"      elegir
    python -m halo.jev "estado…" -p "¿pregunta?" --si-no                          sí / no
    python -m halo.jev "estado…" -p "¿pregunta?" --escala "nivel 0" "nivel 1" "nivel 2"
    (--estado-archivo ruta toma el estado de un archivo; --json imprime la respuesta completa)
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("TYPESAFE_URL", "https://api.typesafe.ai")
MODELO = "jev-1.13.0"          # versión fija: la documentación desaconseja jev-latest en producción
UMBRAL = 0.6                   # confianza mínima para no marcar la respuesta como dudosa
NO_SE = ("no_se", "No hay información suficiente en el estado para decidir.")
PRECIO_POR_MILLON = 0.042      # US$ por millón de tokens de entrada; la salida es gratis


def clave() -> str | None:
    c = os.environ.get("TYPESAFE_API_KEY")
    if c or sys.platform != "win32":
        return c or None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, "TYPESAFE_API_KEY")[0] or None
    except OSError:
        return None


def disponible() -> bool:
    return clave() is not None


def decidir(state, questions: dict, modelo: str = MODELO, timeout: int = 30, abrir=None) -> dict:
    """Llamada cruda: devuelve la respuesta de Jev tal cual (answers, usage, model)."""
    abrir = abrir or urllib.request.urlopen
    c = clave()
    if not c:
        raise RuntimeError("Falta TYPESAFE_API_KEY (variable de entorno del usuario): Jev no está disponible.")
    cuerpo = json.dumps({"model": modelo, "state": state, "questions": questions}, ensure_ascii=False).encode()
    req = urllib.request.Request(URL.rstrip("/") + "/v1/systemone", data=cuerpo,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {c}"})
    try:
        with abrir(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        motivo = {401: "clave inválida", 422: "petición mal formada", 429: "límite de ritmo", 529: "servicio saturado"}
        raise RuntimeError(f"Jev respondió HTTP {exc.code} ({motivo.get(exc.code, '')}): "
                           f"{exc.read()[:200].decode(errors='replace')}") from None


def _base(d: dict, r: dict) -> dict:
    conf = r.get("confidence")
    if conf is None and r.get("type") == "noul":       # el sí/no no trae confianza: qué tan lejos está del 50 %
        conf = abs(float(r.get("noul") or 0.5) - 0.5) * 2
    conf = float(conf or 0)
    return {"confianza": conf, "modelo": d.get("model"), "tokens": (d.get("usage") or {}).get("input_tokens")}


def elegir(state, pregunta: str, opciones: dict, umbral: float = UMBRAL, con_no_se: bool = True, **kw) -> dict:
    """Elige entre opciones {clave: descripción}. Se añade «no_se» salvo que se pida lo contrario:
    con esa salida a mano Jev se abstiene en vez de inventar."""
    criterios = dict(opciones)
    if con_no_se and NO_SE[0] not in criterios:
        criterios[NO_SE[0]] = NO_SE[1]
    d = decidir(state, {"q": {"type": "choice", "instructions": pregunta, "criteria": criterios}}, **kw)
    r = d["answers"]["q"]
    out = {"eleccion": r.get("choice"), "probabilidades": r.get("probabilities") or {}, **_base(d, r)}
    out["dudosa"] = out["confianza"] < umbral or out["eleccion"] == NO_SE[0]
    return out


def si_no(state, pregunta: str, umbral: float = UMBRAL, **kw) -> dict:
    d = decidir(state, {"q": {"type": "noul", "instructions": pregunta}}, **kw)
    r = d["answers"]["q"]
    p = float(r.get("noul") or 0)
    out = {"si": p >= 0.5, "probabilidad": p, **_base(d, r)}
    out["dudosa"] = out["confianza"] < umbral or 0.35 < p < 0.65
    return out


def puntuar(state, pregunta: str, niveles: list, umbral: float = UMBRAL, **kw) -> dict:
    d = decidir(state, {"q": {"type": "score", "instructions": pregunta, "criteria": list(niveles)}}, **kw)
    r = d["answers"]["q"]
    s = float(r.get("score") or 0)
    i = min(max(round(s), 0), len(niveles) - 1)
    out = {"puntaje": s, "nivel": niveles[i], "indice": i, **_base(d, r)}
    out["dudosa"] = out["confianza"] < umbral
    return out


def _texto(res: dict) -> str:
    if "eleccion" in res:
        probs = ", ".join(f"{k} {v:.2f}" for k, v in sorted(res["probabilidades"].items(), key=lambda kv: -kv[1]))
        linea = f"Jev elige «{res['eleccion']}» · {probs}"
    elif "si" in res:
        linea = f"Jev dice {'SÍ' if res['si'] else 'NO'} (p = {res['probabilidad']:.2f})"
    else:
        linea = f"Jev puntúa {res['puntaje']:.2f} → «{res['nivel']}»"
    linea += f" · confianza {res['confianza']:.2f} · {res['modelo']} · {res['tokens']} tokens"
    if res["dudosa"]:
        linea += "\nDUDOSA: confianza baja o «no sé»; no la tomes como respuesta, falta información en el estado."
    return linea


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Segunda opinión de Jev (TypeSafe): una decisión tipada con su confianza.")
    p.add_argument("estado", nargs="?", help="lo que se sabe; o usa --estado-archivo")
    p.add_argument("--estado-archivo", help="archivo de texto con el estado")
    p.add_argument("-p", "--pregunta", required=True)
    p.add_argument("-o", "--opcion", action="append", default=[], metavar="CLAVE=texto", help="una opción por bandera")
    p.add_argument("--sin-no-se", action="store_true", help="no añadir la opción «no_se»")
    p.add_argument("--si-no", action="store_true")
    p.add_argument("--escala", nargs="+", metavar="NIVEL", help="de 2 a 10 niveles ordenados")
    p.add_argument("--umbral", type=float, default=UMBRAL)
    p.add_argument("--modelo", default=MODELO)
    p.add_argument("--json", action="store_true", help="imprime el resultado como JSON")
    a = p.parse_args(argv)
    if a.estado_archivo:
        with open(a.estado_archivo, encoding="utf-8") as fh:
            estado = fh.read()
    elif a.estado:
        estado = a.estado
    else:
        p.error("falta el estado (argumento o --estado-archivo)")
    try:
        if a.si_no:
            res = si_no(estado, a.pregunta, umbral=a.umbral, modelo=a.modelo)
        elif a.escala:
            res = puntuar(estado, a.pregunta, a.escala, umbral=a.umbral, modelo=a.modelo)
        else:
            opciones = {}
            for o in a.opcion:
                k, _, v = o.partition("=")
                opciones[k.strip()] = v.strip() or k.strip()
            if len(opciones) < 2:
                p.error("hacen falta al menos dos --opcion CLAVE=texto (o --si-no / --escala)")
            res = elegir(estado, a.pregunta, opciones, umbral=a.umbral, con_no_se=not a.sin_no_se, modelo=a.modelo)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=2) if a.json else _texto(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
