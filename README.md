# Spark Muse

**A fast, safe browser agent on Meta Muse — the open-source core of [Spark AI](https://github.com/jaemin-jo).**

Spark is a desktop AI agent that does real work on your computer: it reads your mail, fills out
forms, books things and operates any website or Windows app for you. Spark Muse is the browser loop
behind Spark ("Flow"), rewritten as a small open-source package that runs on **Meta Muse** through the
Meta Model API (or OpenRouter).

Built for the Meta Global AI Developer Hackathon — Startup track, *Build for Your World*.

## Why it is fast

Most browser agents send a screenshot every step and do one click per model call. Spark Muse does
what Spark's production engine does:

| | Typical screenshot agent | Spark Muse |
|---|---|---|
| What the model sees | a screenshot every step | a text snapshot: every visible control as `ref role "label"` + page text; pixels only when it asks (`look: true`) |
| Actions per model call | 1 | a batch (`fill` + `press Enter` + `click` …) run in order, stopping at the first error |
| Context growth | every screenshot stays | only the 2 newest snapshots and the newest screenshot stay |
| Finish | "done" | `finish` must quote **evidence** from the page |

Muse Spark's tool calling, 1M context and price ($1.25 / $4.25 per million input/output tokens) suit
this loop: fewer, larger steps instead of many screenshot round-trips.

## Safety rails (from Spark)

- Passwords are never typed by the model — the `fill` action refuses `type=password` fields.
- Payment, account deletion and password-change clicks are refused unless the user passes
  `--allow-critical`; the batch stops there and the model must report back.
- Provider errors are reported by code and message only; keys never appear in logs or traces.

## Quickstart

```bash
pip install -r requirements.txt
python -m playwright install chromium

# Meta Model API (https://dev.meta.ai → API keys)
export MODEL_API_KEY=...
# or OpenRouter (https://openrouter.ai/keys) — same Muse models as meta/muse-spark-1.3
export OPENROUTER_API_KEY=...

python -m spark_muse "Search Wikipedia for Menlo Park and tell me its population" \
    --url https://en.wikipedia.org/ --headed
```

Options: `--provider meta|openrouter`, `--model muse-spark-1.3|meta/muse-wonder|...`,
`--effort low|medium|high`, `--max-steps`, `--allow-critical`, `--out runs`.
Every run writes `runs/<time>/trace.json` (each step's actions, results, think/act seconds, token
usage) and `final.png`.

## Use it as a library

```python
from playwright.async_api import async_playwright
from spark_muse import Muse, Provider, run

async with async_playwright() as pw:
    page = await (await pw.chromium.launch()).new_page()
    await page.goto('https://news.ycombinator.com/')
    result = await run('What is the top story right now?', page, Muse(Provider.pick()))
    print(result.success, result.answer, result.evidence)
```

## Layout

```
spark_muse/model.py   Muse over Chat Completions (Meta Model API or OpenRouter), retries, usage
spark_muse/page.py    snapshot with refs, actions, safety checks
spark_muse/agent.py   the loop: batches, context compaction, evidence-checked finish
tests/test_agent.py   offline tests: a scripted model drives real Chromium (no key needed)
```

```bash
python tests/test_agent.py
```

## How this relates to Spark

Spark (desktop app, Windows/macOS/Android) runs the same loop in production with more around it:
a credential vault, OTP-code handling, Windows/macOS app control through UI Automation and
screenshots, KakaoTalk and Office adapters, and multi-site parallel runs. Spark's production engine
now also speaks the Meta Model API (`flow_provider: muse`). This repository is the open, minimal core.

## License

MIT
