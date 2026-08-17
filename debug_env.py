# backend/debug_env.py
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

print("Current working directory:", os.getcwd())
print("find_dotenv() result:", find_dotenv())

env_path = find_dotenv()
if env_path:
    load_dotenv(env_path)
    print("\n--- After loading:", env_path, "---")
else:
    print("\n⚠️ No .env file found by find_dotenv()!")

print("AZURE_OPENAI_ENDPOINT:", os.getenv("AZURE_OPENAI_ENDPOINT"))
print("AZURE_OPENAI_API_KEY present:", bool(os.getenv("AZURE_OPENAI_API_KEY")))
print("MODEL_DEPLOYMENT:", os.getenv("MODEL_DEPLOYMENT"))

# also check exact .env file content directly, bypassing dotenv entirely
backend_env = Path("backend/.env")
local_env = Path(".env")
for p in [backend_env, local_env]:
    if p.exists():
        print(f"\n--- Raw content of {p.resolve()} ---")
        print(p.read_text())