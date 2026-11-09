#!/usr/bin/env python3
"""Compara Bonsai Q2_0 y PQ2_0 con prompts reales de MGSM y las condiciones de la corrida.

Usa la clase Servidor de pruebas/laboratorio.py: mismos parámetros de servidor y de petición.
Configuraciones: Q2_0 en llama.cpp, Q2_0 en el fork de PrismML y PQ2_0 en el fork de PrismML.
"""
import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "pruebas"))
import laboratorio as lab  # noqa: E402

N = 5  # problemas por idioma
MODELO = "bonsai2-2b-ternario"
CARPETA = RAIZ / "modelos" / "bonsai2-2b-ternario"
CONFIGURACIONES = [
    ("Q2_0 / llama.cpp", RAIZ / "runtimes/llama.cpp/build/bin", CARPETA / "Ternary-Bonsai-2-2B-Q2_0.gguf"),
    ("Q2_0 / PrismML", RAIZ / "runtimes/llama.cpp-prism/build/bin", CARPETA / "Ternary-Bonsai-2-2B-Q2_0.gguf"),
    ("PQ2_0 / PrismML", RAIZ / "runtimes/llama.cpp-prism/build/bin", CARPETA / "Ternary-Bonsai-2-2B-PQ2_0.gguf"),
]


def main():
    base = json.loads((RAIZ / "pruebas" / "config.json").read_text())
    salida = RAIZ / "registros" / "comparacion_bonsai"
    salida.mkdir(parents=True, exist_ok=True)
    resultados = {}
    for nombre, binarios, gguf in CONFIGURACIONES:
        cfg = dict(base, llamacpp_bin=str(binarios), modelos={**base["modelos"], MODELO: str(gguf)})
        filas = []
        for idioma in lab.IDIOMAS:
            servidor = lab.Servidor(cfg, MODELO, salida / f"servidor_{nombre.replace(' / ', '_')}_{idioma}.log")
            try:
                for pid, pregunta, correcta in lab.leer_problemas(idioma)[:N]:
                    r = servidor.preguntar(lab.PROMPTS[idioma].format(pregunta=pregunta),
                                           lab.MODELOS[MODELO]["plantilla"])
                    t = r["timings"]
                    filas.append({
                        "idioma": idioma, "id": pid, "correcta": correcta,
                        "texto": r["choices"][0]["message"].get("content") or "",
                        "tokens": r["usage"]["completion_tokens"],
                        "prompt_tps": t["prompt_per_second"], "gen_tps": t["predicted_per_second"],
                        "segundos": (t["prompt_ms"] + t["predicted_ms"]) / 1000,
                    })
                    print(f"{nombre:18} {idioma} #{pid}: {filas[-1]['tokens']:3} tokens, "
                          f"{filas[-1]['gen_tps']:5.1f} t/s, {filas[-1]['segundos']:5.1f} s", flush=True)
            finally:
                servidor.cerrar()
            filas_idioma = [f for f in filas if f["idioma"] == idioma]
            for f in filas_idioma:
                f["memoria_pico_mb"] = round(servidor.pico / 2**20, 1)
        resultados[nombre] = filas
    (salida / "resultados.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=1))

    print(f"\n{'configuración':18} {'prompt t/s':>10} {'gen t/s':>8} {'s/problema':>10} {'memoria MB':>10} {'tamaño MB':>9}")
    for nombre, _, gguf in CONFIGURACIONES:
        f = resultados[nombre]
        media = lambda k: sum(x[k] for x in f) / len(f)
        print(f"{nombre:18} {media('prompt_tps'):10.1f} {media('gen_tps'):8.1f} {media('segundos'):10.1f} "
              f"{max(x['memoria_pico_mb'] for x in f):10.1f} {gguf.stat().st_size / 1e6:9.1f}")
    referencia = resultados[CONFIGURACIONES[0][0]]
    for nombre, _, _ in CONFIGURACIONES[1:]:
        iguales = sum(a["texto"] == b["texto"] for a, b in zip(referencia, resultados[nombre]))
        print(f"Respuestas idénticas a {CONFIGURACIONES[0][0]}: {nombre}: {iguales}/{len(referencia)}")


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"Duración: {time.time() - t0:.0f} s")
