"""ABI sharedspice de ngspice. Se ejecuta SOLO en un proceso descartable.

No expone una consola SPICE: el padre entrega un deck validado y un análisis
estructurado. No carga .spiceinit, bibliotecas externas ni comandos de usuario.
"""
from __future__ import annotations

import ctypes as C
import json
import math
import os
from pathlib import Path
import sys


class Complex(C.Structure):
    _fields_ = [('real', C.c_double), ('imag', C.c_double)]


class Vector(C.Structure):
    _fields_ = [('name', C.c_char_p), ('type', C.c_int), ('flags', C.c_short),
                ('real', C.POINTER(C.c_double)), ('complex', C.POINTER(Complex)), ('length', C.c_int)]


def ejecutar(peticion):
    dll = Path(peticion['dll'])
    # Keep the handle alive: dependent DLLs in the KiCad bin directory.
    directorio_dll = os.add_dll_directory(str(dll.parent)) if os.name == 'nt' else None
    lib = C.CDLL(str(dll))
    mensajes, salidas, errores = [], [], []
    Texto = C.CFUNCTYPE(C.c_int, C.c_char_p, C.c_int, C.c_void_p)
    Salida = C.CFUNCTYPE(C.c_int, C.c_int, C.c_bool, C.c_bool, C.c_int, C.c_void_p)

    @Texto
    def texto(s, ident, user):
        mensaje = s.decode('utf-8', 'replace')
        if any(x in mensaje.lower() for x in ('error', 'fatal', 'timestep too small', 'simulation interrupted', 'run simulation(s) aborted')):
            if len(errores) < 200:
                errores.append(mensaje)
        if len(mensajes) < 500:
            mensajes.append(mensaje)
        return 0

    @Texto
    def estado(s, ident, user):
        return 0

    @Salida
    def salida(status, immediate, quit_, ident, user):
        salidas.append(status)
        return 0

    lib.ngSpice_Init.argtypes = [Texto, Texto, Salida, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]
    lib.ngSpice_Init.restype = C.c_int
    lib.ngSpice_Command.argtypes = [C.c_char_p]
    lib.ngSpice_Command.restype = C.c_int
    lib.ngSpice_Circ.argtypes = [C.POINTER(C.c_char_p)]
    lib.ngSpice_Circ.restype = C.c_int
    lib.ngSpice_CurPlot.restype = C.c_char_p
    lib.ngGet_Vec_Info.argtypes = [C.c_char_p]
    lib.ngGet_Vec_Info.restype = C.POINTER(Vector)
    if lib.ngSpice_Init(texto, estado, salida, None, None, None, None):
        raise RuntimeError('ngspice no pudo inicializarse')
    lib.ngSpice_Command(b'version')
    version = '\n'.join(mensajes)
    mensajes.clear()
    lineas = [s.encode('utf-8') for s in peticion['deck'].splitlines()]
    lineas.append(None)
    circ = (C.c_char_p * len(lineas))(*lineas)
    codigo = lib.ngSpice_Circ(circ)
    if codigo or salidas:
        raise RuntimeError('ngspice rechazó el circuito: ' + '\n'.join(mensajes[-20:]))
    codigo = lib.ngSpice_Command(peticion['comando'].encode('ascii'))
    if codigo or salidas or errores:
        raise RuntimeError('Simulación no completada: ' + '\n'.join((errores or mensajes)[-20:]))
    plot = lib.ngSpice_CurPlot()
    if not plot or plot == b'const':
        raise RuntimeError('ngspice no produjo resultados')

    def vector(nombre):
        ptr = lib.ngGet_Vec_Info(plot + b'.' + nombre.encode('ascii'))
        if not ptr or not 0 < ptr.contents.length <= 100000:
            raise RuntimeError(f'Sonda ausente o demasiados puntos: {nombre}')
        v = ptr.contents
        if v.complex:
            real = [v.complex[i].real for i in range(v.length)]
            imag = [v.complex[i].imag for i in range(v.length)]
        elif v.real:
            real, imag = list(v.real[:v.length]), None
        else:
            raise RuntimeError(f'Sonda sin datos: {nombre}')
        if not all(math.isfinite(x) for x in real) or (imag is not None and not all(math.isfinite(x) for x in imag)):
            raise RuntimeError(f'Datos no finitos: {nombre}')
        return {'real': real, 'imag': imag}

    datos = {s: vector(s) for s in dict.fromkeys(peticion['vectores'])}
    eje = vector(peticion['eje'])['real'] if peticion['eje'] else [0.0]
    if any(len(v['real']) != len(eje) for v in datos.values()):
        raise RuntimeError('Vectores incompletos')
    fin = peticion.get('fin')
    if fin and (not eje or eje[-1] < fin * (1 - 1e-6)):
        raise RuntimeError('El análisis terminó antes del punto final solicitado')
    return {'eje': eje, 'vectores': datos, 'motor': version.strip(), 'log': mensajes}


if __name__ == '__main__':
    entrada, destino = map(Path, sys.argv[1:3])
    try:
        resultado = {'ok': True, **ejecutar(json.loads(entrada.read_text(encoding='utf-8')))}
    except Exception as exc:
        resultado = {'ok': False, 'error': str(exc)}
    destino.write_text(json.dumps(resultado, ensure_ascii=False, allow_nan=False), encoding='utf-8')
