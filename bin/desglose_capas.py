#!/usr/bin/env python3
"""Desglose de cada GGUF por tipo de capa (PI3): bytes, parámetros y bits por parámetro.

Lee solo la cabecera GGUF. El tamaño de cada tensor se obtiene de la diferencia entre
offsets consecutivos, de modo que funciona también con tipos que gguf-py no conoce
(I2_S de bitnet.cpp, Q2_0 de Bonsai).

Uso: desglose_capas.py <salida.csv> <nombre_corto>=<ruta.gguf> ...
"""
import csv
import struct
import sys
from pathlib import Path

# Tipos de valor de los metadatos GGUF y su tamaño fijo en bytes.
FIJOS = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
STRING, ARRAY = 8, 9


class Lector:
    def __init__(self, f):
        self.f = f

    def u32(self):
        return struct.unpack("<I", self.f.read(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.f.read(8))[0]

    def texto(self):
        return self.f.read(self.u64()).decode("utf-8", "replace")

    def valor(self, tipo):
        if tipo == STRING:
            return self.texto()
        if tipo == ARRAY:
            sub, n = self.u32(), self.u64()
            if sub in FIJOS:
                self.f.seek(FIJOS[sub] * n, 1)
                return None
            return [self.valor(sub) for _ in range(n)]
        datos = self.f.read(FIJOS[tipo])
        formato = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f",
                   7: "<?", 10: "<Q", 11: "<q", 12: "<d"}[tipo]
        return struct.unpack(formato, datos)[0]


def leer_gguf(ruta):
    with open(ruta, "rb") as f:
        r = Lector(f)
        if f.read(4) != b"GGUF":
            sys.exit(f"{ruta}: no es GGUF")
        version, n_tensores, n_kv = r.u32(), r.u64(), r.u64()
        meta = {}
        for _ in range(n_kv):
            clave = r.texto()
            meta[clave] = r.valor(r.u32())
        tensores = []
        for _ in range(n_tensores):
            nombre = r.texto()
            dims = [r.u64() for _ in range(r.u32())]
            tipo, offset = r.u32(), r.u64()
            tensores.append({"nombre": nombre, "dims": dims, "tipo": tipo, "offset": offset})
        alineacion = meta.get("general.alignment", 32)
        inicio = (f.tell() + alineacion - 1) // alineacion * alineacion
    fin = Path(ruta).stat().st_size - inicio
    orden = sorted(tensores, key=lambda t: t["offset"])
    for actual, siguiente in zip(orden, orden[1:] + [None]):
        limite = siguiente["offset"] if siguiente else fin
        actual["bytes"] = limite - actual["offset"]
    for t in tensores:
        n = 1
        for d in t["dims"]:
            n *= d
        t["parametros"] = n
    return meta, tensores


TIPOS = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 6: "Q5_0", 7: "Q5_1", 8: "Q8_0",
         10: "Q2_K", 11: "Q3_K", 12: "Q4_K", 13: "Q5_K", 14: "Q6_K", 15: "Q8_K",
         30: "BF16", 34: "TQ1_0", 35: "TQ2_0", 36: "I2_S"}


def categoria(nombre):
    if nombre.startswith("token_embd"):
        return "embeddings"
    if nombre.startswith("output.") or nombre == "output.weight":
        return "salida"
    if nombre.endswith("_norm.weight") or nombre.endswith("_norm.bias") or "norm" in nombre:
        return "normas"
    if ".attn_" in nombre or ".ssm_" in nombre:
        return "atencion"
    if ".ffn_" in nombre:
        return "ffn"
    return "otros"


CATEGORIAS = ("embeddings", "salida", "atencion", "ffn", "normas", "otros")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    filas = []
    for arg in sys.argv[2:]:
        modelo, ruta = arg.split("=", 1)
        meta, tensores = leer_gguf(ruta)
        nombres = {t["nombre"] for t in tensores}
        compartida = "output.weight" not in nombres
        for cat in CATEGORIAS:
            grupo = [t for t in tensores if categoria(t["nombre"]) == cat]
            if not grupo:
                continue
            b = sum(t["bytes"] for t in grupo)
            p = sum(t["parametros"] for t in grupo)
            tipos = sorted({TIPOS.get(t["tipo"], f"tipo{t['tipo']}") for t in grupo})
            filas.append({
                "modelo": modelo, "arquitectura": meta.get("general.architecture"),
                "categoria": cat, "tensores": len(grupo), "parametros": p, "bytes": b,
                "bits_por_parametro": round(8 * b / p, 3), "tipos": "+".join(tipos),
                "salida_compartida_con_embeddings": compartida,
                "archivo_bytes": Path(ruta).stat().st_size,
            })
    with open(sys.argv[1], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)


if __name__ == "__main__":
    main()
