# Instalación local del laboratorio

Estado al 26 de octubre de 2026: instalación de modelos pausada a petición del
usuario después de fallos repetidos de DNS y conexión.

## Estado comprobado

- Ollama **0.40.11** instalado en `runtimes/ollama/`, incluido el motor de CPU.
  Paquete de Arch Linux descargado por HTTPS; firma verificada con el llavero
  de Arch instalado en el equipo. El servicio respondió a `/api/version` y
  `ollama list` mostró una lista vacía. Se cerró después de la comprobación.
- Código de BitNet: `0b341e582afbf9e1011f24744b554c96a3477eb5`.
  Su submódulo todavía debe completarse y fijarse al commit requerido
  `390c307752ab78fd8189f359d6954c9ba1be74af`.
- Código de llama.cpp: `63b4b303bd87456adc2f09d56c7e1f74e9e26673`, del
  26 de octubre de 2026, posterior a la versión mínima b11555 de la guía.
  Todavía no se compiló.
- Entorno de Python 3.12 en `runtimes/python/`. Hay dependencias instaladas,
  incluidas Transformers 4.57.6 y NumPy; **falta PyTorch**.
- **Ninguno de los seis modelos está instalado completamente ni se ha probado
  su inferencia.** Se conservaron los archivos auxiliares y las partes recibidas.
- `pruebas/` está preparada como carpeta local. El usuario creará el repositorio
  compartido en GitHub. No se creó un remoto, no se publicó nada y no se
  desarrollaron ni ejecutaron las pruebas del laboratorio.

Las revisiones, los archivos, los tamaños esperados y los hashes oficiales de
los modelos están en `registros/descargas.json`. El registro de los fallos está
en `registros/descargas.log`. La descarga total de los pesos y archivos de origen
es de aproximadamente 11.7 GB decimales; las conversiones requieren espacio
adicional. Los originales se conservan para reproducibilidad.

## Reanudar con conexión estable

Desde esta carpeta:

```bash
./bin/reanudar_instalacion.sh
```

El script reanuda y verifica descargas, completa el submódulo de BitNet, compila
los dos motores en CPU y convierte Falcon ternario a `i2_s` y Qwen3.5 a
`Q4_K_M`. No ejecuta el piloto, las 3 000 respuestas ni los benchmarks. Su
sintaxis fue revisada; el recorrido completo todavía no pudo validarse debido
a la red. Al terminar guarda versiones y tamaños en
`registros/versiones_instaladas.txt`.

Para reanudar únicamente las descargas:

```bash
python3 bin/descargar_modelos.py
```

## Conversación por texto libre con cada modelo

**Estos comandos estarán disponibles cuando termine la instalación.** Por
ahora detectan los componentes faltantes y muestran un mensaje de pendiente.
Ejecuta uno y escribe tus preguntas en la terminal; `Ctrl+C` termina la sesión.

| Modelo y formato de la guía | Comando |
| --- | --- |
| BitNet 2B, i2_s | `./bin/conversar bitnet-2b` |
| Falcon3 3B ternario, i2_s | `./bin/conversar falcon3-3b-ternario` |
| Ternary Bonsai 2 2B, Q2_0 | `./bin/conversar bonsai2-2b-ternario` |
| Falcon3 3B Instruct, Q4_K_M | `./bin/conversar falcon3-3b-q4` |
| Qwen2.5 1.5B Instruct, Q4_K_M | `./bin/conversar qwen2.5-1.5b-q4` |
| Qwen3.5 2B, Q4_K_M | `./bin/conversar qwen3.5-2b-q4` |

El lanzador usa bitnet.cpp para los dos modelos i2_s y llama.cpp para los otros
cuatro. Configura CPU, 16 hilos físicos, contexto 2048, máximo 512 tokens por
respuesta, temperatura 0, top_k 1 y semilla 42. Solicita desactivar el pensamiento
extendido en las plantillas de Bonsai y Qwen3.5. No añade un system prompt.

## Acceder a Ollama

No hace falta instalarlo globalmente. En una terminal de esta carpeta:

```bash
./bin/ollama serve
```

En otra terminal:

```bash
./bin/ollama list
```

Para usar el nombre `ollama` sin escribir `./bin/`, en Bash:

```bash
source ./activar.sh
ollama list
```

El servicio escucha en `127.0.0.1:11434`, usa `modelos/ollama/` y tiene
desactivadas las funciones de nube. Todavía no hay modelos registrados en
Ollama. Los comandos `conversar` acceden directamente a los motores indicados
por la guía, incluidos los modelos i2_s de BitNet.

## Fuentes y repositorio de pruebas

- Guía local: `guia_laboratorio.pdf`, tabla 1 y sección 3.
- [Ollama para Linux](https://docs.ollama.com/linux).
- [Paquete de Ollama en Arch Linux](https://archlinux.org/packages/extra/x86_64/ollama/).
- [BitNet oficial](https://github.com/microsoft/BitNet).
- [llama.cpp oficial](https://github.com/ggml-org/llama.cpp).
- [llama.cpp b11555](https://github.com/ggml-org/llama.cpp/releases/tag/b11555).

El repositorio oficial de Qwen3.5 contiene los pesos de origen, no el GGUF
Q4_K_M de esta instalación. El script lo convierte localmente, manteniendo la
procedencia oficial. Bonsai usa únicamente Q2_0, sin el proyector de visión.

La carpeta `pruebas/` incluye instrucciones mínimas. Cuando se cree el
repositorio compartido, registrar allí su URL y usar un único commit del script
común en los tres equipos, según la guía.

## Progreso y reanudación incremental

El descargador muestra cada 5 segundos los MiB recibidos, porcentaje, velocidad
y tiempo restante por archivo activo. También informa cuando espera al servidor,
cuando no llegan datos y cuando verifica SHA-256. `OMITIDO` significa que el
archivo ya estaba completo y fue verificado; no se descarga otra vez.

Los `.part` se reanudan, y un parcial completo se verifica sin pedirlo de nuevo.
Los motores ya compilados para la misma revisión se omiten. CMake/Ninja comprueba
las compilaciones previas sin sello de manera incremental. Las conversiones GGUF
finales se reutilizan si tienen cabecera válida. Para reutilizar un intermedio se
exige además su marca de conversión terminada y que conserve el tamaño registrado;
así se evita aceptar un intermedio truncado. Estas comprobaciones no reemplazan
una prueba de inferencia. Las dependencias solo se comprueban si falta
la conversión que las necesita; uv reutiliza las versiones que ya cumplen.

El registro visible también queda en `registros/reanudar.log`. No se permite
ejecutar dos reanudaciones simultáneas. Ctrl+C conserva los parciales; el cierre
de una conexión puede tardar hasta 30 segundos.

Comprobación de esta actualización: Bonsai Q2_0 está descargado y su tamaño y
SHA-256 coinciden con el manifiesto; se omitirá. Se verificaron con archivos y
respuestas simuladas la reanudación, los archivos completos, los hashes incorrectos,
los rangos incorrectos y dos ejecuciones del instalador sin repetir componentes.
No se reinició la descarga ni se ejecutó inferencia durante esta actualización.
