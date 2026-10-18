"""Download the small heading model (Qwen2.5-0.5B-Instruct, 4-bit, about 0.5 GB) once:
    python fetch_title_model.py [target folder, default ./models]
then set TITLE_MODEL_PATH in .env to the path it prints. Needs internet only for this download."""
import os
import sys

from huggingface_hub import hf_hub_download

folder = sys.argv[1] if len(sys.argv) > 1 else "models"
os.makedirs(folder, exist_ok=True)
path = hf_hub_download("Qwen/Qwen2.5-0.5B-Instruct-GGUF", "qwen2.5-0.5b-instruct-q4_k_m.gguf", local_dir=folder)
print("TITLE_MODEL_PATH=" + os.path.abspath(path))
