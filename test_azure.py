# backend/test_azure.py
from openai import AzureOpenAI
from app.core.config import (
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_KEY,
    MODEL_DEPLOYMENT,
    AZURE_OPENAI_API_VERSION,
)

print("Endpoint:", AZURE_OPENAI_ENDPOINT)
print("Deployment:", MODEL_DEPLOYMENT)
print("API Version:", AZURE_OPENAI_API_VERSION)
print("API Key present:", bool(AZURE_OPENAI_API_KEY))

client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
)

try:
    response = client.chat.completions.create(
    model=MODEL_DEPLOYMENT,
    messages=[{"role": "user", "content": "Say 'connection successful' and nothing else."}],
    max_completion_tokens=20,  # renamed from max_tokens
)
    print("\n✅ SUCCESS")
    print("Response:", response.choices[0].message.content)
except Exception as e:
    print("\n❌ FAILED")
    print("Error type:", type(e).__name__)
    print("Error:", e)