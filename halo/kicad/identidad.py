"""Symbols are identified by UUID; a reference is only a convenience index."""
from collections.abc import MutableMapping


class Simbolos(MutableMapping):
    def __init__(self):
        self._uuid = {}

    def candidatos(self, clave):
        if clave in self._uuid:
            return [self._uuid[clave]]
        return [s for s in self._uuid.values() if s.ref == clave]

    def __getitem__(self, clave):
        candidatos = self.candidatos(clave)
        if len(candidatos) != 1:
            if candidatos:
                raise ValueError(f"{clave} tiene varias unidades; selecciona su UUID")
            raise KeyError(clave)
        return candidatos[0]

    def __setitem__(self, clave, simbolo):
        if any(s.ref == simbolo.ref and s.unidad == simbolo.unidad and s.uuid != simbolo.uuid
               for s in self._uuid.values()):
            raise ValueError(f"Referencia/unidad duplicada: {simbolo.ref}/{simbolo.unidad}")
        self._uuid[simbolo.uuid] = simbolo

    def __delitem__(self, clave):
        del self._uuid[self[clave].uuid]

    def __contains__(self, clave):
        return bool(self.candidatos(clave))

    def __iter__(self):
        return iter(self._uuid)

    def __len__(self):
        return len(self._uuid)
