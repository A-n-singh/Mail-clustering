"""Download the heading models once (needs internet only for this):
    python fetch_title_model.py            main model: Qwen2.5-1.5B-Instruct, 4-bit, about 1.1 GB   (Apache-2.0)
    python fetch_title_model.py --large    larger fallback: Qwen3-4B-Instruct-2507, 4-bit, about 2.5 GB (Apache-2.0)
Optional folder as the last argument (default ./models). Then put the printed line into .env."""
import os
import sys

from huggingface_hub import hf_hub_download

args = [a for a in sys.argv[1:] if not a.startswith("--")]
folder = args[0] if args else "models"
os.makedirs(folder, exist_ok=True)
if "--large" in sys.argv:
    repo, file, var = "unsloth/Qwen3-4B-Instruct-2507-GGUF", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "TITLE_MODEL_LARGE_PATH"
else:
    repo, file, var = "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf", "TITLE_MODEL_PATH"
print(f"{var}=" + os.path.abspath(hf_hub_download(repo, file, local_dir=folder)))
