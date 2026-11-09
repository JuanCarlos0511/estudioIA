# Ejercicio individual: clasificar reseñas con BitNet

Tiempo estimado: 2 a 3 horas. Solo necesitas CPU; no hace falta GPU.

Vas a usar **BitNet b1.58 2B** de Microsoft, un modelo de lenguaje con pesos
ternarios (-1, 0, 1) que corre en CPU con `bitnet.cpp`, para clasificar el
sentimiento de 30 reseñas de productos en español.

## Archivos

| Archivo | Contenido |
| --- | --- |
| `resenas.csv` | 30 reseñas: `id`, `texto`, `etiqueta` (`positivo`, `negativo` o `neutral`; 10 de cada una) |
| `prompt_ejemplo.txt` | Prompt de ejemplo para una reseña, ya con el formato de chat del modelo |
| `bitnet-2b-relu2.patch` | Corrección de una línea para bitnet.cpp, necesaria con este modelo (paso 1) |

Las reseñas son sintéticas, escritas para este ejercicio.

## 1. Instalar bitnet.cpp

Requisitos: Linux o macOS, `git`, `cmake` 3.22 o posterior, `clang` 18 o
posterior, `ninja` y unos 2 GB libres. En Ubuntu/Debian:
`sudo apt install git cmake clang ninja-build curl`. En Arch:
`sudo pacman -S git cmake clang ninja curl`.

```bash
git clone --recursive https://github.com/microsoft/BitNet.git
cd BitNet
git checkout 0b341e582afbf9e1011f24744b554c96a3477eb5
git submodule update --init --recursive

# Corrección necesaria para este modelo (ver nota abajo)
git -C 3rdparty/llama.cpp apply /ruta/a/ejercicio_bitnet/bitnet-2b-relu2.patch

cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DBITNET_X86_TL2=OFF -DLLAMA_CURL=OFF \
    -DLLAMA_BUILD_COMMON=ON -DLLAMA_BUILD_TOOLS=ON
cmake --build build --parallel 4 --target llama-completion llama-cli
```

La compilación tarda pocos minutos. Al terminar deben existir
`build/bin/llama-completion` y `build/bin/llama-cli`.

**Nota sobre el parche.** En este commit, el llama.cpp incluido en BitNet
aplica SiLU en la capa feed-forward de BitNet b1.58 2B-4T, pero el modelo usa
ReLU al cuadrado. Sin la corrección, el modelo carga sin errores y responde
texto sin sentido, por ejemplo `1. 1.`. `bitnet-2b-relu2.patch` cambia una línea en
`src/models/bitnet.cpp`.

## 2. Descargar el modelo (1.2 GB)

Desde la carpeta `BitNet`:

```bash
mkdir -p models/BitNet-b1.58-2B-4T
curl -L -o models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf \
    https://huggingface.co/microsoft/BitNet-b1.58-2B-4T-gguf/resolve/a1f2f1c765812aa8af3f6eda4a313707064bba15/ggml-model-i2_s.gguf
sha256sum models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf
# Debe ser: 4221b252fdd5fd25e15847adfeb5ee88886506ba50b8a34548374492884c2162
```

## 3. Comprobar que funciona

Define dos variables con rutas absolutas (en Bash o Zsh) y clasifica la reseña de ejemplo:

```bash
BITNET=/ruta/a/BitNet
EJERCICIO=/ruta/a/ejercicio_bitnet

"$BITNET/build/bin/llama-completion" \
    -m "$BITNET/models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf" \
    -f "$EJERCICIO/prompt_ejemplo.txt" \
    -n 5 -t 4 -c 512 --temp 0 -no-cnv --no-display-prompt 2>/dev/null
```

Salida esperada (unos segundos en una laptop normal):

```
Positivo [end of text]
```

Qué hace cada opción:

- `-f`: lee el prompt de un archivo. También puedes pasarlo como texto con `-p "..."`.
- `-n 5`: genera como máximo 5 tokens; basta para una palabra.
- `-t 4`: hilos de CPU.
- `-c 512`: tamaño del contexto.
- `--temp 0`: respuesta determinista (siempre la misma para el mismo prompt).
- `-no-cnv`: completa el texto y termina, en vez de abrir un chat interactivo.
- `--no-display-prompt` y `2>/dev/null`: muestran solo la respuesta, sin el prompt ni los registros de carga.

El prompt usa el formato de chat de este modelo:
`User: <instrucción><|eot_id|>Assistant: `. Si lo cambias, mantén ese formato.

Para conversar libremente con el modelo (opcional):
`"$BITNET/build/bin/llama-cli" -m <modelo.gguf> -t 4`.

## 4. El ejercicio

Escribe un script (Python recomendado, sin librerías obligatorias) que:

1. Lea `resenas.csv`.
2. Para cada reseña, construya el prompt, ejecute `llama-completion` (por
   ejemplo con `subprocess`) y convierta la salida en una de las tres
   etiquetas. Si la respuesta no es ninguna de las tres, registra `desconocido`.
3. Guarde `predicciones.csv` con las columnas `id`, `etiqueta`, `prediccion` y
   `segundos` (tiempo de inferencia de esa reseña).
4. Imprima la exactitud global y una matriz de confusión de 3x3 (real contra predicha).

Después, **intenta mejorar la exactitud cambiando solo el prompt**: por
ejemplo, define qué significa `neutral` o agrega 2 o 3 ejemplos resueltos (few-shot).
No uses reseñas de `resenas.csv` como ejemplos. Ejecuta de nuevo y compara.

## 5. Entrega

Una carpeta o repositorio con:

- El script y una línea con el comando para ejecutarlo.
- `predicciones.csv` del prompt inicial y del prompt mejorado.
- `NOTAS.md`, de media página como máximo:
  - exactitud y matriz de confusión de cada prompt;
  - qué clase falla más y por qué crees que pasa;
  - qué cambiaste en el prompt y si funcionó;
  - tiempo promedio por reseña y características de tu CPU.

## Qué se evaluará

- El script corre de principio a fin con el comando indicado.
- La interpretación de la salida es robusta (mayúsculas, puntuación,
  texto extra, `[end of text]`).
- Las métricas son correctas y se pueden reproducir.
- El análisis de errores es claro y honesto. Una exactitud baja bien explicada
  vale más que una alta sin explicación.
