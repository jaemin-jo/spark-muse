"""python -m spark_muse "find the cheapest ..." --url https://... [--headed] [--provider meta|openrouter]"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

from .agent import run
from .model import ModelError, Muse, Provider


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='spark_muse', description='Spark browser agent on Meta Muse')
    parser.add_argument('task')
    parser.add_argument('--url', default='https://www.google.com/', help='start page')
    parser.add_argument('--provider', choices=['meta', 'openrouter'], help='default: whichever key is set')
    parser.add_argument('--model', help='e.g. muse-spark-1.3, meta/muse-wonder')
    parser.add_argument('--effort', default='low', choices=['low', 'medium', 'high'])
    parser.add_argument('--headed', action='store_true', help='show the browser window')
    parser.add_argument('--max-steps', type=int, default=30)
    parser.add_argument('--allow-critical', action='store_true', help='allow pay/delete/password-change clicks')
    parser.add_argument('--out', default='runs', help='where traces and the final screenshot go')
    args = parser.parse_args(argv)

    try:
        provider = Provider.pick(args.provider, args.model)
    except ModelError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    print(f'Spark Muse · {provider.name} · {provider.model}')
    muse = Muse(provider, effort=args.effort)
    out = Path(args.out) / time.strftime('%Y%m%d-%H%M%S')
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed)
        page = await browser.new_page(viewport={'width': 1280, 'height': 860}, locale='en-US')
        await page.goto(args.url, wait_until='domcontentloaded', timeout=45000)
        try:
            result = await run(args.task, page, muse, max_steps=args.max_steps, allow_critical=args.allow_critical)
        finally:
            await page.screenshot(path=str(out / 'final.png'))
            await browser.close()
            await muse.close()
    (out / 'trace.json').write_text(json.dumps({
        'task': args.task, 'start_url': args.url, 'provider': provider.name, 'model': provider.model,
        'success': result.success, 'answer': result.answer, 'evidence': result.evidence,
        'seconds': round(result.seconds, 1), 'usage': result.usage, 'steps': result.steps},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n{"✓" if result.success else "✗"} {result.answer}')
    if result.evidence:
        print(f'  evidence: {result.evidence[:300]}')
    print(f'  {result.seconds:.1f}s · {len(result.steps)} steps · {result.usage} · trace {out / "trace.json"}')
    return 0 if result.success else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
