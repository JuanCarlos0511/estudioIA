#!/usr/bin/env bash
# Instalación local e incremental; no ejecuta el laboratorio ni publica en GitHub.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
mkdir -p registros runtimes modelos
# Impide que dos reanudaciones escriban los mismos archivos simultáneamente.
exec 9>registros/instalacion.lock
flock -n 9 || { echo 'Ya hay otra instalación activa. No se iniciará una segunda.' >&2; exit 1; }
exec > >(tee -a registros/reanudar.log) 2>&1
date -Is
export UV_CACHE_DIR="$root/runtimes/uv-cache"
export UV_HTTP_TIMEOUT=120
export UV_HTTP_RETRIES=5
export PYTHONUNBUFFERED=1

# Los archivos finales se publican con rename tras una conversión exitosa.
# Comprueba además la cabecera GGUF; un .part nunca cuenta como instalado.
gguf_ready() {
    python3 - "$1" <<'PY'
import pathlib, struct, sys
p = pathlib.Path(sys.argv[1])
try:
    with p.open("rb") as f:
        h = f.read(24)
    magic, version, tensors, metadata = struct.unpack("<4sIQQ", h)
    valid = magic == b"GGUF" and version in (2, 3) and tensors > 0 and metadata > 0 and p.stat().st_size > 24
except (OSError, struct.error):
    valid = False
sys.exit(0 if valid else 1)
PY
}

intermediate_ready() {
    gguf_ready "$1" && [[ -f "$1.complete" ]] && [[ "$(cat "$1.complete")" == "$(stat -c %s "$1")" ]]
}

engine_ready() {
    local runtime="$1" revision stamp
    for tool in llama-cli llama-server llama-bench llama-quantize; do
        [[ -x "runtimes/$runtime/build/bin/$tool" ]] || return 1
    done
    revision="$(git -C "runtimes/$runtime" rev-parse HEAD)"
    if [[ "$runtime" == BitNet ]]; then
        revision="$revision $(git -C runtimes/BitNet/3rdparty/llama.cpp rev-parse HEAD) $bitnet_patch_sum"
    fi
    stamp="runtimes/$runtime/build/.laboratorio-revision"
    if [[ -f "$stamp" && "$(cat "$stamp")" == "$revision" ]]; then
        return 0
    fi
    # Una compilación anterior a este script puede no tener el sello.
    # CMake/Ninja la comprobará de forma incremental, sin recompilar lo que siga vigente.
    return 1
}

echo '1/6. Descargar: se verifican y omiten los archivos completos; se reanudan los .part.'
python3 -u bin/descargar_modelos.py

echo '2/6. Comprobar el submódulo y las compilaciones de los motores.'
expected_submodule="$(git -C runtimes/BitNet ls-tree HEAD 3rdparty/llama.cpp | awk '{print $3}')"
actual_submodule="$(git -C runtimes/BitNet/3rdparty/llama.cpp rev-parse HEAD 2>/dev/null || true)"
if [[ -f runtimes/BitNet/3rdparty/llama.cpp/CMakeLists.txt && "$expected_submodule" == "$actual_submodule" ]]; then
    echo 'OMITIDO BitNet: el submódulo exacto ya está instalado.'
else
    git -C runtimes/BitNet submodule update --init --depth 1
fi
actual_submodule="$(git -C runtimes/BitNet/3rdparty/llama.cpp rev-parse HEAD)"
[[ "$expected_submodule" == "$actual_submodule" ]] || { echo 'Submódulo BitNet incorrecto'; exit 1; }
# El llama.cpp de BitNet define el tipo I2_S en ggml, pero llama-quantize no lo
# acepta; el parche lo registra. Se aplica una sola vez y entra en el sello.
bitnet_patch="$root/bin/parches/bitnet-quantize-i2s.patch"
bitnet_patch_sum="$(sha256sum "$bitnet_patch" | cut -d' ' -f1)"
if git -C runtimes/BitNet/3rdparty/llama.cpp apply --reverse --check "$bitnet_patch" 2>/dev/null; then
    echo 'OMITIDO parche I2_S: ya aplicado.'
else
    git -C runtimes/BitNet/3rdparty/llama.cpp apply "$bitnet_patch"
fi
for runtime in BitNet llama.cpp; do
    if engine_ready "$runtime"; then
        echo "OMITIDO $runtime: los cuatro ejecutables ya están compilados para esta revisión."
        continue
    fi
    echo "COMPILANDO $runtime (CMake/Ninja reutiliza el trabajo previo)."
    cmake -S "runtimes/$runtime" -B "runtimes/$runtime/build" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
        -DGGML_NATIVE=ON -DGGML_CUDA=OFF -DGGML_VULKAN=OFF \
        -DBITNET_X86_TL2=OFF -DLLAMA_CURL=OFF \
        -DLLAMA_BUILD_COMMON=ON \
        -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TOOLS=ON \
        -DLLAMA_BUILD_EXAMPLES=ON -DLLAMA_BUILD_TESTS=OFF
    cmake --build "runtimes/$runtime/build" --parallel 4 \
        --target llama-cli llama-server llama-bench llama-quantize
    revision="$(git -C "runtimes/$runtime" rev-parse HEAD)"
    [[ "$runtime" != BitNet ]] || revision="$revision $actual_submodule $bitnet_patch_sum"
    printf '%s\n' "$revision" > "runtimes/$runtime/build/.laboratorio-revision"
done

falcon="$root/modelos/falcon3-3b-ternario"
qwen="$root/modelos/qwen3.5-2b-q4"
echo '3/6. Comprobar Falcon ternario y sus dependencias.'
if gguf_ready "$falcon/ggml-model-i2_s.gguf"; then
    echo 'OMITIDO Falcon ternario: GGUF final presente; no se instalan dependencias ni se convierte otra vez.'
else
    echo 'DEPENDENCIAS Falcon: uv instala solo lo que falta o no satisface la versión requerida.'
    uv pip install --python runtimes/python/bin/python 'torch==2.11.0' \
        --index-url https://download.pytorch.org/whl/cpu
    uv pip install --python runtimes/python/bin/python 'numpy<2' \
        'transformers==4.57.6' safetensors sentencepiece protobuf pyyaml requests tqdm packaging
    uv pip install --python runtimes/python/bin/python \
        -e runtimes/BitNet/3rdparty/llama.cpp/gguf-py
fi

echo '4/6. Conversión de Falcon ternario.'
if gguf_ready "$falcon/ggml-model-i2_s.gguf"; then
    echo 'OMITIDO Falcon ternario: ya convertido a i2_s.'
else
    # También se conserva el intermedio F32 si se completó antes de una interrupción.
    if intermediate_ready "$falcon/ggml-model-f32.gguf"; then
        echo 'OMITIDO F32 de Falcon: intermedio ya presente.'
    else
        # El conversor oficial elige este nombre; guardar después en .part
        # no haría atómica la conversión, por eso se pide --outfile explícito.
        (cd runtimes/BitNet && "$root/runtimes/python/bin/python" -u \
            utils/convert-hf-to-gguf-bitnet.py "$falcon" --outtype f32 \
            --outfile "$falcon/ggml-model-f32.gguf.part")
        mv "$falcon/ggml-model-f32.gguf.part" "$falcon/ggml-model-f32.gguf"
        stat -c %s "$falcon/ggml-model-f32.gguf" > "$falcon/ggml-model-f32.gguf.complete"
    fi
    runtimes/BitNet/build/bin/llama-quantize \
        "$falcon/ggml-model-f32.gguf" "$falcon/ggml-model-i2_s.gguf.part" I2_S 1
    gguf_ready "$falcon/ggml-model-i2_s.gguf.part" || { echo 'GGUF Falcon inválido'; exit 1; }
    mv "$falcon/ggml-model-i2_s.gguf.part" "$falcon/ggml-model-i2_s.gguf"
fi

echo '5/6. Comprobar y convertir Qwen3.5.'
if gguf_ready "$qwen/Qwen3.5-2B-Q4_K_M.gguf"; then
    echo 'OMITIDO Qwen3.5: GGUF final presente; no se instalan dependencias ni se convierte otra vez.'
else
    echo 'DEPENDENCIAS Qwen: uv instala solo lo que falta o no satisface la versión requerida.'
    uv pip install --python runtimes/python/bin/python \
        -r runtimes/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt
    uv pip install --python runtimes/python/bin/python -e runtimes/llama.cpp/gguf-py
    if intermediate_ready "$qwen/Qwen3.5-2B-F16.gguf"; then
        echo 'OMITIDO F16 de Qwen: intermedio ya presente.'
    else
        runtimes/python/bin/python -u runtimes/llama.cpp/convert_hf_to_gguf.py "$qwen" \
            --outfile "$qwen/Qwen3.5-2B-F16.gguf.part" --outtype f16
        mv "$qwen/Qwen3.5-2B-F16.gguf.part" "$qwen/Qwen3.5-2B-F16.gguf"
        stat -c %s "$qwen/Qwen3.5-2B-F16.gguf" > "$qwen/Qwen3.5-2B-F16.gguf.complete"
    fi
    runtimes/llama.cpp/build/bin/llama-quantize \
        "$qwen/Qwen3.5-2B-F16.gguf" "$qwen/Qwen3.5-2B-Q4_K_M.gguf.part" Q4_K_M
    gguf_ready "$qwen/Qwen3.5-2B-Q4_K_M.gguf.part" || { echo 'GGUF Qwen inválido'; exit 1; }
    mv "$qwen/Qwen3.5-2B-Q4_K_M.gguf.part" "$qwen/Qwen3.5-2B-Q4_K_M.gguf"
fi

echo '6/6. Registrar versiones y tamaños. No se ejecutan pruebas experimentales.'
{
    date -Is
    git -C runtimes/BitNet rev-parse HEAD
    git -C runtimes/BitNet submodule status
    git -C runtimes/llama.cpp rev-parse HEAD
    find modelos -name '*.gguf' -type f -printf '%p\t%s bytes\n'
} > registros/versiones_instaladas.txt
echo 'Instalación terminada. Uso: ./bin/conversar <nombre_corto>'
