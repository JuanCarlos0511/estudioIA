# estudioIA

Herramientas para el laboratorio de modelos ternarios y de 4 bits: instalación
local de los motores y modelos, y ejecución completa del experimento en una VM
CPU de **Google Colab**, con la cuenta de Google de quien lo lance. En Colab
se corren las 3 000 respuestas de MGSM, los benchmarks y la verificación.

## Dos repositorios

| Repositorio | Contenido | Ubicación |
| --- | --- | --- |
| [testing-modelos-terniarios](https://github.com/JuanCarlos0511/testing-modelos-terniarios) | El script común `laboratorio.py`: modelos, prompts, parámetros, versión fija de MGSM, plantillas y el parche de bitnet.cpp. Es el mismo para todos. | Se clona dentro de este, en `pruebas/` |
| **estudioIA** (este) | El lanzador de Colab, el código que corre en la VM y la instalación local | Raíz |

Este repositorio no copia ni modifica el script común: lo usa tal cual, en un
commit fijo. Funcionan así:

1. El lanzador (`bin/experimentar_colab.py`) lee de `pruebas/` la URL y el
   commit actual del repositorio común, y calcula el SHA-256 de sus archivos.
   Si `pruebas/` tiene cambios sin commit, no arranca.
2. Arma la carga de la VM con ese commit, los hashes, los commits de los tres
   motores (bitnet.cpp, llama.cpp y el fork de PrismML), el SHA-256 esperado de
   los 6 GGUF y el script `colab/ejecutar_remoto.py`. La envía con el CLI
   oficial de Colab (`colab exec`).
3. En la VM, `ejecutar_remoto.py` clona testing-modelos-terniarios en **ese
   commit exacto** y comprueba los hashes. Después compila los motores,
   descarga y convierte los modelos, y verifica que cada GGUF sea idéntico byte
   a byte al esperado. Por último corre `laboratorio.py` igual que en una
   laptop: `datos`, `preparar`, piloto, `correr`, `velocidad`, `versiones` y
   `verificar`.
4. Cada minuto la VM empaqueta un respaldo y el lanzador lo descarga a
   `registros/colab/actual/`.

Por eso el commit del repositorio común queda fijo durante toda la corrida. Si
actualizas `pruebas/` (`git pull` o un commit nuevo) a mitad de una corrida, el
lanzador se niega a reanudarla. Para usar el commit nuevo, empieza otra corrida
con `--salida` (ver más abajo).

## Requisitos

- Linux con `git`, Python 3.10 o posterior y [uv](https://docs.astral.sh/uv/).
- Una cuenta de Google con unidades de cómputo de Colab: Colab Pro, Pay As You
  Go o un plan de Google AI elegible. La VM CPU estándar gastó 0.08 unidades
  por hora en nuestra corrida.
- Opcional: `systemd-run`, para dejar el lanzador en segundo plano.

No hacen falta GPU ni los modelos en tu equipo. Si no tienes la instalación
local, la VM debe reproducir los GGUF de la laptop de referencia
(`colab/referencia.json`).

## Ejecutar el experimento en Colab con tu cuenta

### 1. Clonar los dos repositorios

```bash
git clone https://github.com/JuanCarlos0511/estudioIA.git
cd estudioIA
git clone https://github.com/JuanCarlos0511/testing-modelos-terniarios.git pruebas
cp pruebas/config.ejemplo.json pruebas/config.json
```

En `pruebas/config.json`, cambia `"apellido"` por el tuyo. Con él se nombra la
carpeta de entrega, `entrega_<apellido>_colab`. Para Colab no hace falta
ajustar las rutas.

### 2. Iniciar sesión con tu cuenta de Google

```bash
bash bin/iniciar_colab.sh
```

El script instala con uv el CLI oficial `google-colab-cli` 0.7.5 dentro de
`runtimes/` y consulta tu saldo. La primera vez imprime un enlace: ábrelo,
elige tu cuenta de Google y pega en la terminal el código que te muestre
Google. No crea ninguna VM.

El token queda en `runtimes/colab-estado/token.json`, con permisos 600 y fuera
de Git. Cada persona usa su propia cuenta y gasta sus propias unidades. No
compartas ese archivo ni el código de autorización. Para cambiar de cuenta,
borra ese archivo y `~/.config/colab-cli/token.json`, si existe, y vuelve a
ejecutar el script.

### 3. Comprobar sin gastar unidades (opcional)

```bash
bash bin/experimentar_colab.sh --preparar
python3 -m unittest discover -s colab -p 'test_*.py'
```

`--preparar` arma la carga sin conectarse ni crear una VM. Sin modelos locales
imprime `Sin pesos locales de …: se usa la referencia`.

### 4. Lanzar

```bash
systemd-run --user --unit=laboratorio-colab --same-dir bash bin/experimentar_colab.sh --estandar
journalctl --user -u laboratorio-colab -f
```

También puedes correr `bash bin/experimentar_colab.sh --estandar` en primer
plano.

- `--estandar` pide la VM CPU de 2 vCPU, que corre con 1 hilo. Sin la opción se
  pide una High-RAM, con más núcleos y RAM. Si Colab no tiene capacidad,
  responde `503 Service Unavailable`: no se crea VM ni se cobra nada.
- El tipo de VM se guarda en `registros/colab/actual/forma_vm.txt` y se repite
  en cada reanudación, porque no se mezclan mediciones de hardware distinto.

La fase actual aparece en `registros/colab/actual/estado.json`:

1. `preparando`: dependencias, descargas y conversiones. En la VM estándar
   tarda más de una hora.
2. `piloto`: 5 problemas por modelo e idioma. Imprime una estimación de la
   corrida completa.
3. `corriendo`: las 3 000 respuestas y los benchmarks, modelo por modelo.
4. `completo` o `fallido`.

### 5. Dejarlo corriendo, apagar la laptop y retomar

- La VM sigue trabajando sin tu laptop mientras ejecute código, hasta la
  duración máxima que imponga Colab. Según su FAQ, en general es de hasta
  12 horas.
- Mientras el lanzador no está conectado no se descargan respaldos. Lo
  avanzado en ese lapso solo está en la VM.
- Cerrar sesión, apagar o suspender la laptop detiene el servicio
  `laboratorio-colab`. Para que siga después de cerrar sesión, con la laptop
  encendida: `loginctl enable-linger`.
- Para retomar, ejecuta otra vez el mismo comando. Si el servicio anterior
  falló, primero: `systemctl --user reset-failed laboratorio-colab`.
  - Si la corrida sigue, el lanzador vuelve a descargar respaldos cada minuto.
  - Si ya terminó, la VM la estuvo esperando hasta 12 horas con los
    resultados; el lanzador los descarga y la libera.
  - Si la VM se perdió, crea otra y reanuda desde el último respaldo local.
- En la VM estándar, la corrida completa ocupa varias VMs y cada una vuelve a
  preparar todo. Si una VM nueva trae otro modelo de CPU, la corrida se detiene
  para no mezclar hardware. En ese caso, empieza otra con `--salida`.

### 6. Resultados

Los respaldos se extraen en `registros/colab/actual/resultados/`:

- `pruebas/resultados/entrega_<apellido>_colab/`: `respuestas/*.jsonl`,
  `velocidad/*.csv`, `memoria.csv`, `ficha_laptop.txt`, `versiones.txt` y
  `bitacora.md`. Es el mismo formato de entrega que en una laptop.
- `pruebas/resultados/piloto_colab/`: la prueba piloto.
- `hardware.json` y `procedencia.json`: la VM usada, los commits y los hashes.

### 7. Detener y costos

```bash
./bin/colab usage                     # saldo y consumo por hora
./bin/colab status -s laboratorio-cpu # estado de la VM
systemctl --user stop laboratorio-colab   # solo detiene el lanzador local
./bin/colab stop -s laboratorio-cpu   # libera la VM; se pierde lo no descargado
```

Para una corrida independiente, usa otra carpeta y otra sesión:

```bash
bash bin/experimentar_colab.sh --estandar --salida registros/colab/otra --sesion laboratorio-cpu-2
```

## GGUF de referencia

`colab/referencia.json` contiene el tamaño y el SHA-256 de los 6 GGUF y los
commits de los motores de la laptop de referencia. Falcon3 ternario y Qwen3.5
se convierten en la VM a partir de los pesos oficiales, que vienen de
revisiones fijas de Hugging Face (`registros/descargas.json`), y deben salir
idénticos. Si alguno difiere, la corrida se detiene antes de medir.

Si tienes la instalación local, el lanzador usa tus GGUF y tus commits, y la
corrida de Colab queda comparable con tu laptop. Las rutas salen de
`pruebas/config.json`, y los motores son los clones en `runtimes/BitNet`,
`runtimes/llama.cpp` y `runtimes/llama.cpp-prism`.

## Instalación local (opcional)

Para correr el laboratorio en tu propia laptop, como pide la guía:

- **Motores, parche y conversión:** las instrucciones están en
  `pruebas/README.md`.
- **Descarga de pesos:** `bin/descargar_modelos.py` descarga y verifica los
  del manifiesto `registros/descargas.json`.
- **Compilación y conversión:** `bin/reanudar_instalacion.sh` compila y
  convierte dentro de `runtimes/` y `modelos/`. Su bitácora está en
  `INSTALACION.md`.

`COLAB.md` documenta en detalle el lanzador de Colab y los fallos que se
corrigieron.

## Contenido

| Ruta | Para qué |
| --- | --- |
| `bin/experimentar_colab.py` (`.sh`) | Lanzador: arma la carga, crea o reutiliza la VM, descarga respaldos y retoma corridas |
| `colab/ejecutar_remoto.py` | Lo que corre en la VM: preparación, corrida, latido y respaldos |
| `colab/referencia.json` | GGUF y commits de la laptop de referencia |
| `colab/test_ejecucion.py` | Pruebas locales; no crean VMs |
| `bin/colab`, `bin/colab_local.py`, `bin/iniciar_colab.sh` | CLI oficial de Colab instalado en el proyecto, con su estado en `runtimes/` |
| `bin/descargar_modelos.py`, `bin/reanudar_instalacion.sh` | Descargas reanudables e instalación local |
| `bin/iniciar_corrida.sh` | Corrida completa en la laptop con `pruebas/config.json` |
| `bin/conversar`, `bin/ollama`, `activar.sh` | Conversación libre con cada modelo y Ollama local |
| `bin/desglose_capas.py`, `bin/comparar_bonsai.py` | Análisis auxiliares; sus resultados están en `registros/` |
| `ejercicio_bitnet/` | Ejercicio individual de clasificación con BitNet |

No se incluyen la guía del laboratorio (la entregan los profesores) ni la
solución del evaluador del ejercicio.
