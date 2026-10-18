"""Download the heading model once (about 1.1 GB, needs internet only for this):
    python fetch_title_model.py [folder, default ./models]
Qwen2.5-1.5B-Instruct, 4-bit, Apache-2.0. Then put the printed line into .env."""
import os
import sys

from huggingface_hub import hf_hub_download

folder = sys.argv[1] if len(sys.argv) > 1 else "models"
os.makedirs(folder, exist_ok=True)
print("TITLE_MODEL_PATH=" + os.path.abspath(hf_hub_download("Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf", local_dir=folder)))
