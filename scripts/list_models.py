"""Lists the Gemini models available to your API key.

Usage: python -m scripts.list_models
"""

from google import genai

from app.core.config import get_settings

if __name__ == "__main__":
    client = genai.Client(api_key=get_settings().gemini_api_key)
    for m in client.models.list():
        print(m.name)
