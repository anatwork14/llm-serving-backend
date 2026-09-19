#!/usr/bin/env bash
set -Eeuo pipefail

export LD_LIBRARY_PATH="/opt/llama:${LD_LIBRARY_PATH:-}"

HF_MODEL_REPO="${HF_MODEL_REPO:-prism-ml/Ternary-Bonsai-2-27B-gguf}"
MODEL_FILE="${MODEL_FILE:-Ternary-Bonsai-2-27B-PQ2_0.gguf}"
MMPROJ_FILE="${MMPROJ_FILE:-Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf}"
LLM_CONTEXT_SIZE="${LLM_CONTEXT_SIZE:-16384}"
LLM_PARALLEL_SLOTS="${LLM_PARALLEL_SLOTS:-2}"
LLM_GPU_LAYERS="${LLM_GPU_LAYERS:-99}"
LLM_IMAGE_MAX_TOKENS="${LLM_IMAGE_MAX_TOKENS:-1024}"
LLM_ENABLE_VISION="${LLM_ENABLE_VISION:-true}"
LLAMA_API_KEY="${LLAMA_API_KEY:-local-llama-key}"

download_hf_file() {
    local filename="$1"
    local dest="/models/$filename"
    local part="${dest}.part"
    local url="https://huggingface.co/${HF_MODEL_REPO}/resolve/main/${filename}?download=true"

    if [[ -s "$dest" ]]; then
        echo "[llm] Found $filename"
        return
    fi

    echo "[llm] Downloading $filename from $HF_MODEL_REPO"
    echo "[llm] First startup can take a while; partial downloads resume automatically."

    auth_args=()
    if [[ -n "${HF_TOKEN:-}" ]]; then
        auth_args=(-H "Authorization: Bearer ${HF_TOKEN}")
    fi

    curl \
        -fL \
        --retry 10 \
        --retry-all-errors \
        --retry-delay 3 \
        --connect-timeout 30 \
        --continue-at - \
        --progress-bar \
        "${auth_args[@]}" \
        -o "$part" \
        "$url"

    mv "$part" "$dest"
    echo "[llm] Download complete: $filename"
}

download_hf_file "$MODEL_FILE"

args=(
    -m "/models/$MODEL_FILE"
    --host "0.0.0.0"
    --port "8080"
    -ngl "$LLM_GPU_LAYERS"
    -fa "on"
    -c "$LLM_CONTEXT_SIZE"
    -np "$LLM_PARALLEL_SLOTS"
    -cb
    --cache-prompt
    --temp "0.7"
    --top-p "0.95"
    --top-k "20"
    --min-p "0"
    --jinja
    --api-key "$LLAMA_API_KEY"
)

if [[ "${LLM_ENABLE_VISION,,}" == "true" || "$LLM_ENABLE_VISION" == "1" ]]; then
    download_hf_file "$MMPROJ_FILE"
    args+=(--mmproj "/models/$MMPROJ_FILE")
    if [[ "$LLM_IMAGE_MAX_TOKENS" != "0" ]]; then
        args+=(--image-max-tokens "$LLM_IMAGE_MAX_TOKENS")
    fi
fi

echo "[llm] Starting Prism llama-server"
echo "[llm] Model: $MODEL_FILE"
echo "[llm] Context pool: $LLM_CONTEXT_SIZE"
echo "[llm] Parallel slots: $LLM_PARALLEL_SLOTS"
echo "[llm] GPU layers: $LLM_GPU_LAYERS"
echo "[llm] Vision: $LLM_ENABLE_VISION"

exec /opt/llama/llama-server "${args[@]}"
