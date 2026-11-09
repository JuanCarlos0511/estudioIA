#!/usr/bin/env bash
# Lanza la corrida completa del laboratorio con config.json tal como está.
# Comprueba las condiciones de la tabla 2, impide la suspensión y muestra el avance.
# Se puede volver a lanzar: laboratorio.py continúa con las respuestas pendientes.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/pruebas"
py="$root/runtimes/python/bin/python"

hilos="$("$py" -c 'import json; print(json.load(open("config.json"))["hilos"])')"
apellido="$("$py" -c 'import json; print(json.load(open("config.json"))["apellido"])')"
entrega="resultados/entrega_$apellido"

fallo=0
if [[ "$(cat /sys/class/power_supply/ACAD/online 2>/dev/null)" != 1 ]]; then
    echo "ALTO: el cargador no está conectado (la tabla 2 exige corriente)."; fallo=1
fi
cpus="$(grep Cpus_allowed_list /proc/self/status | awk '{print $2}')"
if [[ "$cpus" != "0-15" ]]; then
    echo "ALTO: este proceso solo puede usar las CPU $cpus."
    echo "      Ejecuta: sudo systemctl stop intel_lpmd  y abre una terminal nueva."; fallo=1
fi
(( fallo == 0 )) || exit 1

echo "Hilos: $hilos | CPU disponibles: $cpus | perfil: $(cat /sys/firmware/acpi/platform_profile 2>/dev/null)"
echo "Entrega: pruebas/$entrega | registro de consola: pruebas/$entrega/registros/consola.log"
echo "No cierres esta terminal. Ctrl+C detiene; al relanzar continúa donde se quedó."
echo

mkdir -p "$entrega/registros"
"$py" laboratorio.py preparar >/dev/null

# Sin suspensión por inactividad ni por cerrar la tapa mientras dure la corrida.
systemd-inhibit --what=sleep:idle:handle-lid-switch --who="laboratorio BitNet" \
    --why="Corrida completa de modelos" \
    "$py" -u laboratorio.py correr 2>&1 | tee -a "$entrega/registros/consola.log"

"$py" laboratorio.py versiones >/dev/null
echo
echo "Corrida terminada. Siguiente paso: $py laboratorio.py velocidad"
