#!/usr/bin/env bash
# Instala Colab sin alterar el Python del laboratorio y consulta el saldo.
# La consulta inicia OAuth si todavía no hay una sesión autenticada.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --help ]]; then
    echo 'Uso: bash bin/iniciar_colab.sh'
    echo 'Instala google-colab-cli y consulta el saldo; no crea una máquina.'
    exit 0
fi
command -v uv >/dev/null || { echo 'Hace falta uv para instalar el CLI.' >&2; exit 1; }
export UV_CACHE_DIR="$root/runtimes/colab-cache"
export UV_TOOL_DIR="$root/runtimes/colab-tools"
export UV_TOOL_BIN_DIR="$root/runtimes/colab-bin"
mkdir -p "$root/runtimes/colab-estado"
if [[ ! -x "$UV_TOOL_BIN_DIR/colab" ]]; then
    # bin/colab_local.py usa detalles internos de esta versión.
    uv tool install google-colab-cli==0.7.5
fi
echo 'Abre el enlace que mostrará Google e inicia sesión con tu cuenta AI Pro.'
echo 'Pega el código de autorización en esta terminal.'
exec bash "$root/bin/colab" usage
