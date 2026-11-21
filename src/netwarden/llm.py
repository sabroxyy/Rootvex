"""Build the chat model from config.

We use the OpenAI-compatible client so the same code talks to Ollama locally or
vLLM / llama.cpp on a rented GPU - only base_url and model change.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from netwarden.config import LLMSettings


def build_llm(cfg: LLMSettings) -> BaseChatModel:
    return ChatOpenAI(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=cfg.api_key,
        temperature=cfg.temperature,
        timeout=cfg.request_timeout_s,
        max_retries=1,
    )
