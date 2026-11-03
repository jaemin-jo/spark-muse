"""Offline tests: a scripted model drives a real Chromium page; no API key needed."""
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.async_api import async_playwright

from spark_muse import agent
from spark_muse.model import ModelError, Provider

PAGE = """<html><head><title>Shop</title></head><body>
<form onsubmit="event.preventDefault(); document.getElementById('out').innerText = 'Results for ' + q.value">
  <input id="q" aria-label="Search" placeholder="Search"><button>Go</button></form>
<p id="out"></p>
<input type="password" aria-label="Password">
<button onclick="document.getElementById('out').innerText='PAID'">Pay now</button>
</body></html>"""


def call(name, args, cid='c1'):
    return {'tool_calls': [{'id': cid, 'type': 'function',
                            'function': {'name': name, 'arguments': json.dumps(args)}}]}


class ScriptedMuse:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []
        self.usage = {'calls': 0}

    async def chat(self, messages, tools):
        self.seen.append(json.loads(json.dumps(messages)))
        self.usage['calls'] += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch()
        self.page = await self.browser.new_page()
        await self.page.set_content(PAGE)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_a_batch_fills_and_submits_then_finishes_with_evidence(self):
        first_snapshot = await agent.snapshot(self.page)
        self.assertIn('e1 textbox "Search"', first_snapshot)
        muse = ScriptedMuse(
            call('browser', {'actions': [{'do': 'fill', 'ref': 'e1', 'value': 'shoes'}, {'do': 'press', 'key': 'Enter'}]}),
            call('finish', {'success': True, 'answer': 'Searched shoes', 'evidence': 'Results for shoes'}))
        result = await agent.run('search shoes', self.page, muse, log=lambda _: None)
        self.assertTrue(result.success)
        self.assertEqual(result.steps[0]['results'], ['filled e1', 'pressed Enter'])
        tool_message = muse.seen[1][-1]
        self.assertEqual(tool_message['role'], 'tool')
        self.assertIn('Results for shoes', tool_message['content'])

    async def test_payment_and_password_need_the_user(self):
        snap = await agent.snapshot(self.page)
        pay = next(line.split()[0] for line in snap.splitlines() if '"Pay now"' in line)
        password = next(line.split()[0] for line in snap.splitlines() if line.split()[1:2] == ['password'])
        muse = ScriptedMuse(
            call('browser', {'actions': [{'do': 'click', 'ref': pay}, {'do': 'press', 'key': 'Enter'}]}),
            call('browser', {'actions': [{'do': 'fill', 'ref': password, 'value': 'hunter2'}]}),
            call('finish', {'success': False, 'answer': 'needs the user', 'evidence': ''}))
        result = await agent.run('pay', self.page, muse, log=lambda _: None)
        self.assertIn('--allow-critical', result.steps[0]['results'][0])
        self.assertEqual(len(result.steps[0]['results']), 1)       # the batch stops at the refusal
        self.assertIn('password fields are typed by the user', result.steps[1]['results'][0])
        self.assertNotEqual(await self.page.inner_text('#out'), 'PAID')
        self.assertEqual(await self.page.input_value('input[type=password]'), '')

    async def test_old_snapshots_and_screenshots_leave_the_conversation(self):
        muse = ScriptedMuse(*[call('browser', {'actions': [{'do': 'wait', 'ms': 1}], 'look': True}, f'c{i}')
                              for i in range(4)],
                            call('finish', {'success': True, 'answer': 'ok', 'evidence': 'Shop'}))
        await agent.run('look around', self.page, muse, log=lambda _: None)
        last = muse.seen[-1]
        tools = [m for m in last if m['role'] == 'tool']
        self.assertEqual(sum('PAGE:' in m['content'] for m in tools), agent.KEEP_SNAPSHOTS)
        images = [m for m in last if m['role'] == 'user' and isinstance(m['content'], list)]
        self.assertEqual(len(images), 1)

    async def test_a_model_error_ends_the_run_cleanly(self):
        result = await agent.run('x', self.page, ScriptedMuse(ModelError('HTTP 402 billing')), log=lambda _: None)
        self.assertFalse(result.success)
        self.assertIn('402', result.answer)


class ProviderTests(unittest.TestCase):
    def test_meta_first_then_openrouter_and_named_provider_needs_its_key(self):
        with patch.dict(os.environ, {'MODEL_API_KEY': '', 'OPENROUTER_API_KEY': 'k'}):
            self.assertEqual(Provider.pick().model, 'meta/muse-spark-1.3')
            with self.assertRaises(ModelError):
                Provider.pick('meta')
        with patch.dict(os.environ, {'MODEL_API_KEY': 'm', 'OPENROUTER_API_KEY': 'k'}):
            self.assertEqual(Provider.pick().name, 'meta')
            self.assertEqual(Provider.pick('openrouter', 'meta/muse-wonder').model, 'meta/muse-wonder')


if __name__ == '__main__':
    unittest.main()
