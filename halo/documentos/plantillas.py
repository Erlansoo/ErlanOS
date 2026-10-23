"""Plantillas de la documentación: memoria en Markdown e informe en LaTeX.

Cada documento mezcla texto libre (del usuario o de la IA) con secciones generadas a partir del proyecto.
Las generadas van entre marcas con la revisión de su fuente; se regeneran sin tocar lo demás.
"""
from __future__ import annotations

import re

FUENTES = {"esquematico": "Esquemático", "simulacion": "Simulación", "pcb": "Placa de circuito impreso",
           "fabricacion": "Fabricación"}
_TEX_ESCAPES = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
                "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex(texto) -> str:
    return "".join(_TEX_ESCAPES.get(c, c) for c in str("" if texto is None else texto))


def md(texto) -> str:
    return re.sub(r"([\\`*_{}\[\]|])", r"\\\1", str("" if texto is None else texto))


def codigo(texto) -> str:
    """Un tramo en `código`: dentro no se escapa nada (Markdown lo toma literal); sólo se evita el acento grave."""
    return "`" + str("" if texto is None else texto).replace("`", "'").replace("|", "¦") + "`"


def marca_md(fuente: str, revision: str) -> tuple[str, str]:
    return f"<!-- halo:seccion {fuente} {revision} -->", f"<!-- halo:fin {fuente} -->"


def marca_tex(fuente: str, revision: str) -> tuple[str, str]:
    return f"% halo:seccion {fuente} {revision}", f"% halo:fin {fuente}"


def _estado_erc(v: dict | None) -> str:
    if not v or not v.get("erc"):
        return "sin verificar todavía"
    erc, netlist, reglas = v.get("erc") or {}, v.get("netlist") or {}, v.get("reglas") or {}
    partes = [f"ERC: {erc.get('errores', '?')} errores, {erc.get('avisos', '?')} avisos"]
    if netlist:
        partes.append("netlist idéntica a la intención" if netlist.get("coinciden") else f"netlist con {netlist.get('diferencias', '?')} diferencias")
    if reglas:
        partes.append(f"dibujo: {reglas.get('problemas', 0)} problemas")
    return "; ".join(partes)


def _estado_drc(p: dict) -> str:
    drc = p.get("drc")
    if not drc:
        return "sin verificar todavía"
    partes = [f"DRC: {drc.get('errores', '?')} errores, {drc.get('avisos', '?')} avisos"]
    if drc.get("pendientes") is not None:
        partes.append(f"{drc['pendientes']} conexiones pendientes")
    if drc.get("paridad") is not None:
        partes.append("paridad con el esquemático" + (" exacta" if not drc["paridad"] else f": {drc['paridad']} diferencias"))
    return "; ".join(partes)


# ------------------------------------------------------------------ Markdown
def seccion_md(fuente: str, d: dict) -> str:
    lineas = [f"## {FUENTES[fuente]}", ""]
    if fuente == "esquematico":
        e = d.get("esquematico")
        if not e:
            lineas.append("_El proyecto todavía no tiene esquemático._")
        else:
            lineas.append(f"Archivo {codigo(e['archivo'])} · {len(e['componentes'])} componentes · {e.get('cables') or 0} cables · "
                          f"{len(e['bloques'])} bloques. Verificación de KiCad: {md(_estado_erc(e.get('verificacion')))}.")
            lineas += ["", "![Esquemático](figuras/esquematico.svg)", ""]
            if e["bloques"]:
                lineas += ["### Bloques del circuito", ""]
                for b in e["bloques"]:
                    refs = ", ".join(b["referencias"]) or "sin componentes dentro"
                    lineas.append(f"- **{md(b['titulo'])}** ({md(refs)})" + (f": {md(b['funcion'])}" if b.get("funcion") else ""))
                    for dec in b.get("decisiones", []):
                        lineas.append(f"  - decisión: {md(dec)}")
                    for pen in b.get("pendientes", []):
                        lineas.append(f"  - pendiente: {md(pen)}")
                lineas.append("")
            lineas += ["### Componentes", "", "| Referencia | Valor | Símbolo |", "|---|---|---|"]
            lineas += [f"| {md(c['ref'])} | {md(c['valor'])} | {codigo(c['simbolo'])} |" for c in e["componentes"]]
            if e["etiquetas"]:
                lineas += ["", "Señales entre bloques: " + ", ".join(codigo(t) for t in e["etiquetas"]) + "."]
    elif fuente == "simulacion":
        sims = d.get("simulacion") or []
        if not sims:
            lineas.append("_Sin simulaciones registradas para este proyecto._")
        else:
            lineas += ["| Simulación | Análisis | Estado | Criterios | Fecha |", "|---|---|---|---|---|"]
            lineas += [f"| {md(s.get('nombre') or s['id'])} | {md(s.get('analisis'))} | {md(s.get('estado'))}"
                       f" ({md(s.get('vigencia'))}) | {md(s.get('criterios') or '—')} | {md(s.get('creado') or '')} |" for s in sims]
            medidas = [(s, m) for s in sims for m in s.get("medidas", [])]
            if medidas:
                lineas += ["", "### Medidas", "", "| Simulación | Medida | Valor | Estado |", "|---|---|---|---|"]
                for s, m in medidas:
                    valor = f"{m['valor']:.6g} {m.get('unidad') or ''}".strip() if isinstance(m.get("valor"), (int, float)) else "—"
                    lineas.append(f"| {md(s.get('nombre') or s['id'])} | {md(m['id'])} ({md(m.get('metrica'))} de {md(m.get('sonda'))}) | {md(valor)} | {md(m.get('estado'))} |")
    elif fuente == "pcb":
        p = d.get("pcb")
        if not p:
            lineas.append("_El proyecto todavía no tiene placa._")
        else:
            tam = f"{p['tamano'][0]} × {p['tamano'][1]} mm" if p.get("tamano") else "tamaño sin definir"
            lineas.append(f"Archivo {codigo(p['archivo'])} · {tam} · {p.get('capas')} capas · etapa: {md(p.get('etapa') or 'sin etapa')}. "
                          f"{p['huellas']} huellas ({p['arriba']} arriba, {p['abajo']} abajo), {p['pistas']} tramos de pista, {p['vias']} vías. "
                          f"Verificación: {md(_estado_drc(p))}.")
            lineas += ["", "![Placa](figuras/placa.svg)", ""]
            if p["zonas"]:
                lineas.append("Zonas de cobre: " + ", ".join(f"{md(z['red'] or 'sin red')} en {md(z['capa'])}" for z in p["zonas"]) + ".")
            if p["planos"]:
                lineas.append("Planos: " + ", ".join(f"{md(capa)} → {md(', '.join(redes) if isinstance(redes, list) else redes)}" for capa, redes in p["planos"].items()) + ".")
            if p["anchos"]:
                lineas.append("Anchos de pista por red: " + ", ".join(f"{md(red)} {ancho} mm" for red, ancho in p["anchos"].items()) + ".")
            for r in d.get("renders", []):
                lineas += ["", f"![Vista 3D]({md('figuras/' + r)})"]
    elif fuente == "fabricacion":
        fabs = d.get("fabricacion") or []
        if not fabs:
            lineas.append("_Todavía no se han preparado archivos de fabricación._")
        else:
            lineas += ["| Entrega | Fecha | Archivos | Veredicto |", "|---|---|---|---|"]
            lineas += [f"| {md(f['id'])} | {md(f.get('fecha'))} | {f.get('archivos')} | {md(f.get('veredicto'))} |" for f in fabs]
    return "\n".join(lineas).rstrip() + "\n"


def memoria_md(d: dict) -> str:
    partes = [f"# {md(d['nombre'])}", "", f"Memoria del proyecto · {d['fecha']} · generada en el Halo y ampliada a mano.", "",
              "## Propósito", "", "_Describe aquí qué hace el circuito y para quién. Este texto es tuyo: las secciones generadas "
              "se actualizan solas y no lo tocan._", ""]
    for fuente in FUENTES:
        inicio, fin = marca_md(fuente, d["revisiones"].get(fuente, "ausente"))
        partes += [inicio, seccion_md(fuente, d), fin, ""]
    partes += ["## Conclusiones y siguientes pasos", "", "_Pendiente._", ""]
    return "\n".join(partes)


# ------------------------------------------------------------------ LaTeX
def seccion_tex(fuente: str, d: dict) -> str:
    lineas = [f"\\section{{{tex(FUENTES[fuente])}}}", ""]
    if fuente == "esquematico":
        e = d.get("esquematico")
        if not e:
            lineas.append("El proyecto todavía no tiene esquemático.")
        else:
            lineas.append(f"El esquemático \\texttt{{{tex(e['archivo'])}}} tiene {len(e['componentes'])} componentes, {e.get('cables') or 0} cables y "
                          f"{len(e['bloques'])} bloques. Verificación de KiCad: {tex(_estado_erc(e.get('verificacion')))}.")
            lineas += ["", "\\begin{figure}[H]\\centering", "\\includegraphics[width=\\textwidth]{figuras/esquematico.pdf}",
                       f"\\caption{{Esquemático de {tex(d['nombre'])}.}}\\end{{figure}}", ""]
            if e["bloques"]:
                lineas += ["\\subsection{Bloques del circuito}", "\\begin{itemize}"]
                for b in e["bloques"]:
                    refs = ", ".join(b["referencias"]) or "sin componentes dentro"
                    lineas.append(f"  \\item \\textbf{{{tex(b['titulo'])}}} ({tex(refs)})" + (f": {tex(b['funcion'])}" if b.get("funcion") else ""))
                lineas += ["\\end{itemize}", ""]
            lineas += ["\\subsection{Componentes}", "\\begin{longtable}{lll}\\toprule", "Referencia & Valor & Símbolo \\\\ \\midrule", "\\endhead"]
            lineas += [f"{tex(c['ref'])} & {tex(c['valor'])} & \\texttt{{{tex(c['simbolo'])}}} \\\\" for c in e["componentes"]]
            lineas += ["\\bottomrule\\end{longtable}"]
    elif fuente == "simulacion":
        sims = d.get("simulacion") or []
        if not sims:
            lineas.append("Sin simulaciones registradas para este proyecto.")
        else:
            # el nombre de la simulación es texto largo: columna que parte, el resto estrecho
            lineas += ["\\begin{longtable}{p{0.55\\textwidth}lll}\\toprule", "Simulación & Análisis & Estado & Criterios \\\\ \\midrule", "\\endhead"]
            lineas += [f"{tex(s.get('nombre') or s['id'])} & {tex(s.get('analisis'))} & {tex(s.get('estado'))} & {tex(s.get('criterios') or '—')} \\\\" for s in sims]
            lineas += ["\\bottomrule\\end{longtable}"]
            medidas = [(s, m) for s in sims for m in s.get("medidas", [])]
            if medidas:
                lineas += ["", "\\subsection{Medidas}", "\\begin{longtable}{p{0.40\\textwidth}p{0.28\\textwidth}ll}\\toprule", "Simulación & Medida & Valor & Estado \\\\ \\midrule", "\\endhead"]
                for s, m in medidas:
                    valor = f"{m['valor']:.6g} {m.get('unidad') or ''}".strip() if isinstance(m.get("valor"), (int, float)) else "—"
                    lineas.append(f"{tex(s.get('nombre') or s['id'])} & {tex(m['id'])} & {tex(valor)} & {tex(m.get('estado'))} \\\\")
                lineas += ["\\bottomrule\\end{longtable}"]
    elif fuente == "pcb":
        p = d.get("pcb")
        if not p:
            lineas.append("El proyecto todavía no tiene placa.")
        else:
            tam = f"{p['tamano'][0]} $\\times$ {p['tamano'][1]} mm" if p.get("tamano") else "tamaño sin definir"
            lineas.append(f"La placa \\texttt{{{tex(p['archivo'])}}} mide {tam}, tiene {p.get('capas')} capas y está en etapa \\emph{{{tex(p.get('etapa') or 'sin etapa')}}}. "
                          f"Lleva {p['huellas']} huellas ({p['arriba']} arriba, {p['abajo']} abajo), {p['pistas']} tramos de pista y {p['vias']} vías. "
                          f"Verificación: {tex(_estado_drc(p))}.")
            lineas += ["", "\\begin{figure}[H]\\centering", "\\includegraphics[width=0.8\\textwidth]{figuras/placa.pdf}",
                       "\\caption{Cobre, contorno y serigrafía de la placa.}\\end{figure}"]
            if p["zonas"]:
                lineas.append("Zonas de cobre: " + ", ".join(f"{tex(z['red'] or 'sin red')} en {tex(z['capa'])}" for z in p["zonas"]) + ".")
            for r in d.get("renders", []):
                lineas += ["", "\\begin{figure}[H]\\centering", f"\\includegraphics[width=0.7\\textwidth]{{figuras/{r}}}",
                           f"\\caption{{Vista 3D ({tex(r.split('-')[-1].rsplit('.', 1)[0])}).}}\\end{{figure}}"]
    elif fuente == "fabricacion":
        fabs = d.get("fabricacion") or []
        if not fabs:
            lineas.append("Todavía no se han preparado archivos de fabricación.")
        else:
            lineas += ["\\begin{longtable}{llll}\\toprule", "Entrega & Fecha & Archivos & Veredicto \\\\ \\midrule", "\\endhead"]
            lineas += [f"{tex(f['id'])} & {tex(f.get('fecha'))} & {f.get('archivos')} & {tex(f.get('veredicto'))} \\\\" for f in fabs]
            lineas += ["\\bottomrule\\end{longtable}"]
    return "\n".join(lineas).rstrip() + "\n"


def informe_tex(d: dict) -> str:
    partes = [r"\documentclass[a4paper,11pt]{article}", r"\usepackage[utf8]{inputenc}", r"\usepackage[T1]{fontenc}",
              r"\usepackage[spanish,es-noindentfirst]{babel}", r"\usepackage{graphicx,booktabs,longtable,float,geometry}",
              r"\usepackage[hidelinks]{hyperref}", r"\geometry{margin=2.5cm}", "",
              f"\\title{{{tex(d['nombre'])}}}", "\\author{Halo}", f"\\date{{{d['fecha']}}}", "", r"\begin{document}", r"\maketitle", "",
              r"\section{Resumen}", "", "% Texto libre: este bloque es tuyo; las secciones generadas se actualizan sin tocarlo.",
              "Este informe recoge el estado del proyecto tal como lo registra el Halo.", ""]
    for fuente in FUENTES:
        inicio, fin = marca_tex(fuente, d["revisiones"].get(fuente, "ausente"))
        partes += [inicio, seccion_tex(fuente, d), fin, ""]
    partes += [r"\section{Conclusiones}", "", "Pendiente.", "", r"\end{document}", ""]
    return "\n".join(partes)
