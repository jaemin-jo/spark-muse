"""The Spark Flow loop on Muse: one conversation, batched browser actions, evidence-checked finish."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from playwright.async_api import Page

from .model import ModelError, Muse
from .page import ActionError, run_action, screenshot, snapshot

INSTRUCTIONS = """You are Spark, operating a real web browser to finish the user's task quickly and correctly.

Each `browser` call runs a JSON list of actions IN ORDER, then returns each action's result and a fresh
snapshot of the page: every visible interactive element as `ref role "label"` plus the readable text.
Actions: {"do":"goto","url":...} {"do":"click","ref":"e12"} {"do":"fill","ref":"e3","value":...}
{"do":"select","ref":"e4","value":"option label"} {"do":"check","ref":"e5","value":true}
{"do":"press","key":"Enter"} {"do":"scroll","dy":700} {"do":"back"} {"do":"wait","ms":1000} {"do":"hover","ref":...}

How to work:
- Batch every action whose target you can already see, e.g. fill a search box and press Enter in one call.
  Stop a batch only where you must see the result before deciding.
- Prefer the snapshot. Set "look": true only when you need to see pixels (charts, maps, canvas, images).
- Refs change after navigation; always use refs from the latest snapshot.
- Never type passwords (the user does), never pay, delete an account or change a password unless the
  user's own words ask for exactly that.
- Finish with `finish` as soon as the task is done. The answer must state what you found or did, and
  `evidence` must quote the page text that proves it. If the task is impossible, finish with success=false
  and say why.
"""

TOOLS = [
    {'type': 'function', 'function': {
        'name': 'browser', 'description': 'Run browser actions in order and return the new page snapshot.',
        'parameters': {'type': 'object', 'required': ['actions'], 'properties': {
            'actions': {'type': 'array', 'items': {'type': 'object', 'required': ['do'], 'properties': {
                'do': {'type': 'string', 'enum': ['goto', 'click', 'fill', 'select', 'check', 'press',
                                                  'scroll', 'back', 'wait', 'hover']},
                'ref': {'type': 'string'}, 'value': {}, 'key': {'type': 'string'}, 'url': {'type': 'string'},
                'dy': {'type': 'integer'}, 'ms': {'type': 'integer'}}}},
            'look': {'type': 'boolean', 'description': 'attach a screenshot of the result'}}}}},
    {'type': 'function', 'function': {
        'name': 'finish', 'description': 'End the task with the final answer for the user.',
        'parameters': {'type': 'object', 'required': ['success', 'answer', 'evidence'], 'properties': {
            'success': {'type': 'boolean'}, 'answer': {'type': 'string'},
            'evidence': {'type': 'string', 'description': 'page text that proves the answer'}}}}},
]

KEEP_SNAPSHOTS = 2   # older snapshots are dropped from the conversation to keep every turn fast


@dataclass
class Result:
    success: bool
    answer: str
    evidence: str = ''
    steps: list = field(default_factory=list)
    seconds: float = 0.0
    usage: dict = field(default_factory=dict)


def _args(call) -> dict:
    try:
        args = json.loads(call['function'].get('arguments') or '{}')
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def _compact(messages: list):
    """Keep only the newest page snapshots and the newest screenshot in the conversation."""
    snapshots = screenshots = 0
    for message in reversed(messages):
        content = message.get('content')
        if message.get('role') == 'tool' and '\n\nPAGE:\n' in content:
            snapshots += 1
            if snapshots > KEEP_SNAPSHOTS:
                message['content'] = content.split('\n\nPAGE:\n')[0] + '\n\n[older snapshot omitted]'
        elif message.get('role') == 'user' and isinstance(content, list):
            screenshots += 1
            if screenshots > 1:
                message['content'] = '[older screenshot omitted]'


async def run(task: str, page: Page, muse: Muse, *, max_steps: int = 30, allow_critical: bool = False,
              log: Callable[[str], None] = print) -> Result:
    started = time.monotonic()
    first = await snapshot(page)
    messages = [{'role': 'system', 'content': INSTRUCTIONS},
                {'role': 'user', 'content': f'TASK: {task}\n\nPAGE:\n{first}'}]
    steps = []
    for step in range(1, max_steps + 1):
        _compact(messages)
        t = time.monotonic()
        try:
            reply = await muse.chat(messages, TOOLS)
        except ModelError as exc:
            return Result(False, f'Model error: {exc}', steps=steps, seconds=time.monotonic() - started,
                          usage=muse.usage)
        think = time.monotonic() - t
        calls = reply.get('tool_calls') or []
        messages.append({'role': 'assistant', 'content': reply.get('content') or '', 'tool_calls': calls})
        if not calls:
            messages.append({'role': 'user', 'content': 'Call `browser` or `finish`.'})
            continue
        for call in calls:
            name, args = call['function']['name'], _args(call)
            if name == 'finish':
                log(f'[{step}] finish ({think:.1f}s): {args.get("answer", "")[:200]}')
                steps.append({'step': step, 'think_s': round(think, 2), 'finish': args})
                return Result(bool(args.get('success')), str(args.get('answer') or ''),
                              str(args.get('evidence') or ''), steps, time.monotonic() - started, muse.usage)
            results = []
            t = time.monotonic()
            for action in (args.get('actions') or [])[:12]:
                try:
                    results.append(await run_action(page, action, allow_critical))
                except ActionError as exc:
                    results.append(f'ERROR: {exc}')
                    break
                except Exception as exc:  # Playwright timeouts and detached elements
                    results.append(f'ERROR: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}')
                    break
            try:
                await page.wait_for_load_state('domcontentloaded', timeout=8000)
            except Exception:
                pass
            act = time.monotonic() - t
            log(f'[{step}] {json.dumps(args.get("actions"), ensure_ascii=False)[:160]} '
                f'think {think:.1f}s act {act:.1f}s -> {"; ".join(results)[:120]}')
            steps.append({'step': step, 'think_s': round(think, 2), 'act_s': round(act, 2),
                          'actions': args.get('actions'), 'results': results})
            messages.append({'role': 'tool', 'tool_call_id': call['id'],
                             'content': 'RESULTS:\n' + '\n'.join(results) + '\n\nPAGE:\n' + await snapshot(page)})
            if args.get('look'):
                messages.append({'role': 'user', 'content': [
                    {'type': 'text', 'text': 'Screenshot after the actions above:'},
                    {'type': 'image_url', 'image_url': {'url': await screenshot(page)}}]})
    return Result(False, f'Stopped after {max_steps} steps without finishing.', steps=steps,
                  seconds=time.monotonic() - started, usage=muse.usage)
