# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Unified LLM Client supporting OpenAI, Ollama, and OpenAI-compatible providers."""

import os
import random
import time
from typing import Optional, Tuple

import openai


class LLMClient:
    """Client for querying LLMs for CRT policy refinement prompts."""

    def __init__(
        self,
        provider: str = "openai",
        model_name: str = "gpt-4o-mini",
        system_prompt: str = "You are a hardware verification assistant. Return code only.",
        temperature: float = 0.2,
        max_gen_tokens: int = 3000,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.provider = provider.lower()
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_gen_tokens = max_gen_tokens

        if self.provider == "ollama":
            self.base_url = base_url or os.getenv("OLLAMA_HOST") or os.getenv("OPENAI_BASE_URL") or "http://localhost:11434/v1"
            self.api_key = api_key or os.getenv("OPENAI_API_KEY") or "ollama"
            self._ensure_ollama_running()
        elif self.provider == "openai":
            self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")
            if not self.api_key:
                raise RuntimeError("OPENAI_API_KEY is not set.")
        elif self.provider == "openrouter":
            self.base_url = base_url or "https://openrouter.ai/api/v1"
            self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
            if not self.api_key:
                raise RuntimeError("OPENROUTER_API_KEY is not set.")
        else:
            self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
            self.api_key = api_key or os.getenv("OPENAI_API_KEY") or "dummy"

        if hasattr(openai, "OpenAI"):
            self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            openai.api_key = self.api_key
            if self.base_url:
                openai.base_url = self.base_url
            self.client = None

    def _ensure_ollama_running(self):
        import shutil
        import subprocess
        import urllib.request

        health_url = self.base_url.rstrip("/").removesuffix("/v1") + "/api/tags"
        try:
            with urllib.request.urlopen(health_url, timeout=2):
                return
        except Exception:
            pass

        print(f"Ollama server is not responding at {self.base_url}")
        ollama_bin = shutil.which("ollama")
        if ollama_bin:
            print(f"Found Ollama binary at {ollama_bin}. Attempting to start 'ollama serve' in background...")
            subprocess.Popen([ollama_bin, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(10):
                time.sleep(0.5)
                try:
                    with urllib.request.urlopen(health_url, timeout=2):
                        print("Successfully connected to Ollama server!")
                        return
                except Exception:
                    continue

        raise RuntimeError(
            f"Could not connect to Ollama server at {self.base_url}.\n"
            "Please start Ollama by opening the Ollama application or running 'ollama serve' in terminal."
        )

    def query(self, prompt: str) -> Tuple[str, Tuple[int, int, int]]:
        """Query LLM with prompt and return (response_text, (input_tokens, output_tokens, total_tokens))."""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": prompt})

        request = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_gen_tokens,
        }

        for delay in [2**x for x in range(0, 5)]:
            try:
                if self.client is not None:
                    response = self.client.chat.completions.create(**request)
                else:
                    response = openai.ChatCompletion.create(**request)

                content = response.choices[0].message.content or ""
                usage = getattr(response, "usage", None)
                if usage:
                    in_tok = getattr(usage, "prompt_tokens", 0) or 0
                    out_tok = getattr(usage, "completion_tokens", 0) or 0
                    tot_tok = getattr(usage, "total_tokens", 0) or (in_tok + out_tok)
                else:
                    in_tok, out_tok, tot_tok = 0, 0, 0

                return content, (in_tok, out_tok, tot_tok)

            except Exception as e:
                err_type = type(e).__name__
                if err_type == "RateLimitError":
                    sleep_dur = delay + random.randint(1000, 3000) / 1000.0
                    print(f"Rate limit hit ({e}). Retrying in {sleep_dur:.2f}s...")
                    time.sleep(sleep_dur)
                    continue
                elif err_type in ("APIConnectionError", "APITimeoutError", "Timeout"):
                    sleep_dur = delay + random.randint(500, 1500) / 1000.0
                    print(f"Connection error ({e}). Retrying in {sleep_dur:.2f}s...")
                    time.sleep(sleep_dur)
                    continue
                else:
                    raise

        raise RuntimeError(f"LLM query failed for model {self.model_name} after retries.")

    def __call__(self, prompt: str) -> Tuple[str, Tuple[int, int, int]]:
        return self.query(prompt)
