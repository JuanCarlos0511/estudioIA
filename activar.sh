#!/usr/bin/env bash
# Uso: source ./activar.sh
LAB_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$LAB_ROOT/bin:$PATH"
export OLLAMA_MODELS="$LAB_ROOT/modelos/ollama"
export OLLAMA_NO_CLOUD=1
export OLLAMA_IGPU_ENABLE=false
export OLLAMA_HOST=127.0.0.1:11434
