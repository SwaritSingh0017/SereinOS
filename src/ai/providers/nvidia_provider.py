import os
import requests

from dotenv import load_dotenv

load_dotenv()


class NvidiaProvider:

    def __init__(self, model=None):

        self.api_key = os.getenv("NVIDIA_API_KEY")

        self.url = "https://integrate.api.nvidia.com/v1/chat/completions"

        self.model = model or "meta/llama-3.3-70b-instruct"

    def generate_response(self, prompt):

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7,
            "max_tokens": 300
        }

        response = requests.post(
            self.url,
            headers=headers,
            json=payload,
            timeout=60
        )

        response.raise_for_status()

        data = response.json()

        return data["choices"][0]["message"]["content"]