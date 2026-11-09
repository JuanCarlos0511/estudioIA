# Laboratorio en Google Colab mediante el CLI oficial

## Ejecutar el experimento completo

Después de autenticarte, desde esta carpeta:

```bash
bash bin/experimentar_colab.sh
```

El lanzador crea o reutiliza la sesión `laboratorio-cpu`, clona las revisiones
de los tres motores y del script común que utiliza la laptop, aplica el parche
de BitNet, descarga los modelos desde revisiones fijas y realiza las dos
conversiones necesarias. Comprueba que los seis GGUF tengan el mismo SHA-256
que los locales. Usa CPU y registra el hardware y los hilos disponibles.

Primero corre un piloto de 5 problemas por modelo e idioma; después ejecuta
las 3,000 respuestas de MGSM, los benchmarks y la verificación del laboratorio.
La configuración del experimento y el script común se conservan. El tiempo
necesario depende del hardware de Colab y puede exceder la duración de una VM.

Cada minuto descarga un ZIP con los resultados parciales en
`registros/colab/actual/ultimo.zip`. También los extrae a
`registros/colab/actual/resultados/`. Al terminar descarga el respaldo final y
libera la máquina. Mantén abierta la terminal para recibir los respaldos;
interrumpir el cliente no garantiza que se detenga el trabajo remoto.

Si la máquina se pierde, vuelve a ejecutar el mismo comando: reutiliza el
respaldo local para continuar. Si cambió el tipo de CPU, RAM o número de hilos,
se detiene para evitar combinar mediciones de hardware diferente. Una corrida
independiente utiliza otra carpeta y, si corresponde, otra sesión:

```bash
bash bin/experimentar_colab.sh --salida registros/colab/otra_corrida --sesion laboratorio-cpu-2
```

Para ver la máquina o detenerla expresamente:

```bash
./bin/colab status -s laboratorio-cpu
./bin/colab url -s laboratorio-cpu
./bin/colab stop -s laboratorio-cpu
```

Preparación local sin crear una VM:

```bash
bash bin/experimentar_colab.sh --preparar
python3 -m unittest discover -s colab -p 'test_*.py' -v
```

Las pruebas locales comprueban los respaldos, la reanudación, la protección
frente a mezclar experimentos o hardware y que se descarguen los resultados
antes de liberar una VM. No sustituyen una ejecución real en Colab.

## Instalar e iniciar sesión

Desde la carpeta de este proyecto:

```bash
bash bin/iniciar_colab.sh
```

Instala `google-colab-cli` con uv dentro de `runtimes/`, separado del Python
del laboratorio. Consulta el saldo con `colab --auth=oauth2 usage`; si hace falta
autenticación, imprime un enlace de Google. Ábrelo en tu navegador, elige la
cuenta de Google AI Pro y pega el código que muestra Google en **tu terminal**.
La versión instalada (0.7.5) guarda las credenciales en un archivo local. El
lanzador `bin/colab_local.py` reutiliza el acceso y mantiene una copia privada
en `runtimes/colab-estado/token.json`, con permisos 600 y carpeta 700,
excluida de Git. Los registros y el historial también quedan dentro del proyecto,
para poder ejecutar el CLI desde una sesión con el resto del disco en solo lectura.
No se modifica el paquete oficial ni se cambia `HOME`.
Esta operación no crea una máquina ni inicia el experimento.

No pegues el código de autorización ni el contenido del archivo de credenciales
en la conversación ni en archivos que se suban a Git.

Google anuncia 200 unidades de cómputo de Colab al mes con AI Pro, sujetas a
elegibilidad. Comprueba lo que realmente aparece en `usage` antes de ejecutar.
El beneficio de Colab corresponde únicamente al administrador del plan de
Google One: no se comparte con los demás miembros de la familia ni es una
bolsa familiar de 200 unidades. El usuario indicó que recibe AI Pro mediante
el grupo familiar; por ello no debe asumirse que esa cuenta recibe las 200
unidades de Colab. El origen del saldo observado de 84.89 sigue sin verificarse.
Referencia: [beneficios familiares de Google One](https://support.google.com/googleone/answer/9004015?hl=es-MX).

Después de iniciar sesión:

```bash
./bin/colab usage
./bin/colab new -s laboratorio-cpu
./bin/colab status -s laboratorio-cpu
```

Se empieza por CPU porque `pruebas/laboratorio.py` utiliza `-ngl 0`. Las
mediciones de Colab deben quedar separadas de las de la laptop, registrando
CPU, RAM e hilos asignados; el hardware y los tiempos de sesión varían.
El código común del laboratorio, los prompts y los parámetros se conservan.

Comandos para ejecutar código y recuperar archivos, una vez preparado el
entorno remoto y sus modelos:

```bash
./bin/colab exec -s laboratorio-cpu -f ruta/al/script.py
./bin/colab download -s laboratorio-cpu /content/resultados.zip registros/resultados_colab.zip
./bin/colab log -s laboratorio-cpu -o registros/sesion_colab.ipynb
```

Los dos primeros comandos son ejemplos de rutas: todavía hace falta preparar
los motores, modelos y la carpeta de resultados en la máquina remota.
Descarga los resultados antes de detenerla, ya que su disco es temporal:

```bash
./bin/colab stop -s laboratorio-cpu
```

`colab auth` autentica servicios de Google Cloud **dentro** de una máquina;
el inicio de sesión del CLI se hace con el flujo OAuth anterior.

Estado de preparación (4 de noviembre de 2026): CLI 0.7.5 instalado y comando
`version` verificado. El usuario confirmó acceso desde su terminal mediante
`usage`: saldo 84.89 unidades, consumo actual 0.00/h y 0 asignaciones activas.
La diferencia respecto de las 200 unidades mensuales anunciadas por AI Pro
todavía no se ha explicado; esa salida informa saldo y consumo actual, sin
desglosar abonos ni consumo previo. La consulta desde la sesión del agente
sigue fallando al resolver `colab.research.google.com`. No se creó una máquina
ni se ejecutó el experimento en Colab. El lanzador completo está preparado;
pasaron las 13 pruebas locales y la validación de sintaxis de la carga remota.
Se intentó arrancar con el lanzador, pero falló la consulta de sesiones por
DNS antes de asignar una VM. El registro está en
`registros/colab/actual/intento_lanzamiento.log`.

El usuario después logró crear la VM y ejecutar la carga desde su terminal.
La preparación falló porque el directorio predeterminado de los subprocesos
quedaba fijado antes de asignar el id de corrida. Se corrigió para resolver la
carpeta al ejecutar cada comando, con una prueba que lanza un subproceso real.
La VM de esa corrida fue liberada por el lanzador. También se fijó Python 3.12
para los conversores, compatible con `numpy<2`, aunque el kernel de Colab use
Python 3.13.

En el siguiente intento se detectó otro fallo al reconocer un repositorio Git
recién inicializado: `rev-parse HEAD` podía imprimir `HEAD` aunque terminara con
error. Se cambió a verificar el commit y el código de salida. Cuatro pruebas
con repositorios Git reales comprueban la clonación desde cero, la reanudación
de un repositorio vacío, la repetición y el rechazo de una revisión diferente.
El lanzador liberó la VM tras el fallo. La corrida corregida necesita relanzarse.

Fuentes oficiales:

- [CLI de Google Colab](https://github.com/googlecolab/google-colab-cli).
- [Autenticación del CLI](https://github.com/googlecolab/google-colab-cli/blob/main/docs/04_automation_and_utility.md).
- [Beneficios de Google AI Pro](https://support.google.com/googleone/answer/14534406?hl=es).
- [Recursos y límites de Colab](https://research.google.com/colaboratory/intl/es/faq.html).
