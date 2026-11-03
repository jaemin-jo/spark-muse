"""Talk to Meta Muse through any OpenAI-compatible Chat Completions endpoint.

Two providers serve the same Muse models:
  meta        https://api.meta.ai/v1          MODEL_API_KEY       muse-spark-1.3
  openrouter  https://openrouter.ai/api/v1    OPENROUTER_API_KEY  meta/muse-spark-1.3
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx

PROVIDERS = {
    'meta': ('https://api.meta.ai/v1', 'MODEL_API_KEY', 'muse-spark-1.3'),
    'openrouter': ('https://openrouter.ai/api/v1', 'OPENROUTER_API_KEY', 'meta/muse-spark-1.3'),
}


class ModelError(RuntimeError):
    pass


@dataclass
class Provider:
    name: str
    base_url: str
    key: str
    model: str

    @classmethod
    def pick(cls, name: str | None = None, model: str | None = None) -> 'Provider':
        """The named provider, or the first one whose key is set (Meta first)."""
        names = [name] if name else list(PROVIDERS)
        for candidate in names:
            if candidate not in PROVIDERS:
                raise ModelError(f'unknown provider {candidate!r}; use one of {", ".join(PROVIDERS)}')
            base_url, env, default_model = PROVIDERS[candidate]
            key = os.environ.get(env, '')
            if key or name:
                if not key:
                    raise ModelError(f'{env} is not set')
                return cls(candidate, base_url, key, model or default_model)
        raise ModelError('set MODEL_API_KEY (Meta Model API) or OPENROUTER_API_KEY')


class Muse:
    def __init__(self, provider: Provider, http: httpx.AsyncClient | None = None, effort: str = 'low'):
        self.provider = provider
        self.http = http or httpx.AsyncClient()
        self.effort = effort
        self.usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'calls': 0}

    async def chat(self, messages: list, tools: list, attempts: int = 3, timeout: float = 120) -> dict:
        body = {'model': self.provider.model, 'messages': messages, 'tools': tools,
                'tool_choice': 'required', 'parallel_tool_calls': False, 'reasoning_effort': self.effort}
        headers = {'Authorization': f'Bearer {self.provider.key}', 'Content-Type': 'application/json'}
        if self.provider.name == 'openrouter':
            headers |= {'HTTP-Referer': 'https://github.com/jaemin-jo/spark-muse', 'X-Title': 'Spark Muse'}
        problem = ''
        for attempt in range(attempts):
            try:
                response = await self.http.post(f'{self.provider.base_url}/chat/completions',
                                                json=body, headers=headers, timeout=timeout)
            except httpx.HTTPError as exc:
                problem = type(exc).__name__
                await asyncio.sleep(1 + attempt)
                continue
            if response.is_success:
                data = response.json()
                usage = data.get('usage') or {}
                self.usage['prompt_tokens'] += usage.get('prompt_tokens') or 0
                self.usage['completion_tokens'] += usage.get('completion_tokens') or 0
                self.usage['calls'] += 1
                choices = data.get('choices') or []
                if not choices:
                    raise ModelError(f'empty answer: {str(data)[:200]}')
                return choices[0]['message']
            try:
                error = response.json().get('error') or {}
            except ValueError:
                error = {}
            # The provider's code and message only; never the request or the key.
            problem = f"HTTP {response.status_code} {error.get('code') or ''} {str(error.get('message') or '')[:200]}"
            if response.status_code in (408, 429) or response.status_code >= 500:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            break
        raise ModelError(problem.strip() or 'model call failed')

    async def close(self):
        await self.http.aclose()
