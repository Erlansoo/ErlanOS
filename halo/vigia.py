"""Aviso de cambios en carpetas, sin sondeo.

En Windows usa ReadDirectoryChangesW a través de ctypes, sin dependencias: un hilo por carpeta
duerme dentro del sistema hasta que algo cambia y entonces despierta al vigilante del servidor.
En otros sistemas, o si una carpeta no se puede observar, el vigilante vuelve a un sondeo lento.
"""
from __future__ import annotations

import ctypes
import os
import threading
from pathlib import Path

# nombres de archivo y de carpeta, tamaño, última escritura y creación
_FILTRO = 0x1 | 0x2 | 0x8 | 0x10 | 0x40
_TAMANO_BUFER = 64 * 1024

if os.name == "nt":
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.BOOL,
                                           wintypes.DWORD, wintypes.LPDWORD, wintypes.LPVOID, wintypes.LPVOID]
    _k32.ReadDirectoryChangesW.restype = wintypes.BOOL
    _k32.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    _k32.CancelIoEx.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _INVALIDO = wintypes.HANDLE(-1).value
    _FILE_LIST_DIRECTORY = 0x1
    _COMPARTIR_TODO = 0x7
    _OPEN_EXISTING = 3
    _FLAG_BACKUP_SEMANTICS = 0x02000000


class Observador:
    """Observa carpetas enteras (con sus subcarpetas) y llama a `despertar` en cada cambio."""

    def __init__(self, despertar):
        self._despertar = despertar
        self._vigilias: dict[str, tuple[threading.Thread, int]] = {}
        self._mutex = threading.Lock()
        self.activo = os.name == "nt"

    @property
    def espera(self) -> float:
        """Cuánto puede dormir el vigilante entre vueltas: mucho si el sistema avisa, poco si no."""
        return 5.0 if self.activo and self._vigilias else 0.5

    def observar(self, carpetas) -> None:
        """Deja observadas exactamente estas carpetas; las que sobran se sueltan."""
        if not self.activo:
            return
        deseadas = {}
        for c in carpetas:
            ruta = Path(c)
            if ruta.is_dir():
                ruta = ruta.resolve()
                deseadas[os.path.normcase(str(ruta))] = ruta
        with self._mutex:
            for clave in list(self._vigilias):
                if clave not in deseadas:
                    self._detener(clave)
            for clave, carpeta in deseadas.items():
                if clave not in self._vigilias:
                    self._iniciar(clave, carpeta)

    def cerrar(self) -> None:
        with self._mutex:
            for clave in list(self._vigilias):
                self._detener(clave)

    def _iniciar(self, clave: str, carpeta: Path) -> None:
        handle = _k32.CreateFileW(str(carpeta), _FILE_LIST_DIRECTORY, _COMPARTIR_TODO, None, _OPEN_EXISTING,
                                  _FLAG_BACKUP_SEMANTICS, None)
        if handle is None or handle == _INVALIDO:
            return  # sin aviso para esta carpeta: el vigilante sondea despacio
        hilo = threading.Thread(target=self._bucle, args=(clave, handle), daemon=True, name=f"halo-vigia:{carpeta.name}")
        self._vigilias[clave] = (hilo, handle)
        hilo.start()

    def _detener(self, clave: str) -> None:
        _hilo, handle = self._vigilias.pop(clave)
        _k32.CancelIoEx(handle, None)
        _k32.CloseHandle(handle)

    def _bucle(self, clave: str, handle: int) -> None:
        bufer = ctypes.create_string_buffer(_TAMANO_BUFER)
        recibidos = wintypes.DWORD()
        while True:
            ok = _k32.ReadDirectoryChangesW(handle, bufer, _TAMANO_BUFER, True, _FILTRO, ctypes.byref(recibidos), None, None)
            if not ok:
                break  # cancelado, o la carpeta desapareció
            self._despertar()  # también cuando recibidos == 0: hubo más cambios de los que caben; basta con mirar
        with self._mutex:
            if self._vigilias.get(clave, (None, None))[1] == handle:
                self._vigilias.pop(clave, None)
