# Desglose por tipo de capa de los 6 GGUF (PI3)

Generado el 27 de octubre de 2026 con `bin/desglose_capas.py` a partir de las
cabeceras de los GGUF instalados. Datos completos por categoría en
`registros/desglose_capas.csv`. Cifras en GB decimales.

## Bytes por tipo de capa

| Modelo | Archivo | Capas lineales | Bits/parám. (lineales) | Embeddings, salida y normas | Leídos por token | % lineal por token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| bitnet-2b | 1.179 | 0.521 (I2_S) | 2.00 | 0.658 (F16, compartida) | 1.179 | 44 % |
| falcon3-3b-ternario | 2.217 | 0.606 (I2_S) | 2.00 | 1.611 (F16 + F16) | 1.411 | 43 % |
| bonsai2-2b-ternario | 0.536 | 0.392 (Q2_0) | 2.29 | 0.143 (Q2_0, compartida) | 0.536 | 73 % |
| falcon3-3b-q4 | 2.001 | 1.444 (Q4_K/Q6_K) | 4.77 | 0.557 (Q4_K + Q6_K) | 1.774 | 81 % |
| qwen2.5-1.5b-q4 | 1.111 | 0.788 (Q4_K/Q6_K) | 4.81 | 0.323 (Q4_K + Q6_K) | 0.980 | 80 % |
| qwen3.5-2b-q4 | 1.301 | 0.846 (Q4_K/Q6_K) | 4.93 | 0.418 (Q6_K, compartida) | 1.263 | 67 % |

- **Capas lineales:** proyecciones de atención (incluidas las capas lineales
  recurrentes de Qwen3.5) y del FFN. Son las únicas que la ternarización comprime
  en BitNet y Falcon.
- **Leídos por token:** capas lineales, capa de salida y normas. Es lo que se
  transfiere de memoria para generar cada token. La búsqueda en la tabla de
  embeddings lee una sola fila y se desprecia, salvo cuando la tabla se comparte
  con la salida. No incluye el KV-cache.
- **Qwen3.5:** el GGUF incluye una capa de predicción multitoken (`blk.24`,
  `nextn`, 0.038 GB). La generación normal no la ejecuta y Bonsai no la tiene,
  así que se excluyó de "leídos por token".

## Pares de PI3: razón real frente a teórica

Razón teórica: log2(3) / 4 = 1.585 / 4 = **0.396**.

| Par | Bits/parám. lineales | Capas lineales | Bytes por token | Archivo |
| --- | ---: | ---: | ---: | ---: |
| Falcon3 ternario / Falcon3 Q4_K_M | 0.419 | 0.419 | **0.795** | **1.108** |
| Bonsai 2 2B / Qwen3.5-2B Q4_K_M | 0.464 | 0.464 | 0.424 | 0.412 |
| BitNet 2B / Qwen2.5-1.5B (par de PI2, referencia) | 0.416 | 0.661 | 1.203 | 1.061 |

## Lectura

1. **El empaquetado casi no aleja las capas lineales de la teoría.** I2_S guarda cada
   peso ternario en 2 bits, un 26 % más que los 1.585 teóricos, y Q2_0 usa 2.25.
   Pero Q4_K_M tampoco ocupa 4 bits: con escalas y algunas capas en Q6_K, llega a
   4.8–4.9. Los dos excesos casi se compensan. En las capas lineales, la razón
   real es 0.42–0.46, cerca del 0.396 teórico.
2. **Lo que decide el resultado es qué capas se ternarizan.**
   - **Falcon3 ternario:** deja las embeddings y la capa de salida en F16, como
     hace bitnet.cpp con I2_S, también en su versión original. Esas dos capas
     suman 1.61 GB, el 73 % del archivo. Por eso el archivo ternario es **un 11 %
     más grande** que el de 4 bits. En bytes leídos por token, el ahorro es solo
     del 20 % (razón 0.795), frente al 60 % teórico.
   - **Bonsai:** cuantiza también la tabla de embeddings, compartida con la salida,
     en Q2_0. Así logra una razón de 0.41–0.42 en archivo y en bytes por token,
     prácticamente la teórica.
3. **El ahorro esperado de PI3 se materializa solo si la ternarización alcanza las
   capas de vocabulario.** Con vocabularios de 128–131 mil tokens, esas capas pesan
   tanto como el resto del modelo ternario.

## Pendiente

- **Memoria pico:** la razón de memoria pico sale de `memoria.csv` de la corrida
  completa. Del piloto solo hay datos de dos modelos: bitnet-2b 1385 MB y
  qwen2.5-1.5b-q4 1825 MB.
- **Velocidad:** en un régimen limitado por la memoria, la razón de bytes por token
  es el límite superior de la aceleración esperada. Compararla con `velocidad/`,
  teniendo en cuenta el problema de hilos de bitnet.cpp.
