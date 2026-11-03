"""The page as the model sees it, and the actions it can take on it.

A snapshot lists every visible interactive element with a short ref (e12) plus the readable
text, so the model can act on many elements in one batch without a screenshot. A screenshot is
attached only when asked for (canvas apps, charts, visual checks).
"""
from __future__ import annotations

import base64
import re

from playwright.async_api import Page

SNAPSHOT_JS = r"""
(maxText) => {
  const visible = (e) => {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(e);
    return s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0.05;
  };
  const sel = 'a[href],button,input,textarea,select,summary,[role=button],[role=link],[role=tab],' +
              '[role=menuitem],[role=option],[role=checkbox],[role=radio],[role=switch],[role=combobox],' +
              '[role=searchbox],[role=textbox],[contenteditable=true],[onclick],[tabindex]:not([tabindex="-1"])';
  let next = Number(document.documentElement.dataset.sparkNext || 1);
  const lines = [];
  for (const e of document.querySelectorAll(sel)) {
    if (!visible(e) || e.closest('[aria-hidden=true]')) continue;
    if (!e.dataset.sparkRef) e.dataset.sparkRef = 'e' + (next++);
    const tag = e.tagName.toLowerCase();
    const type = (e.getAttribute('type') || '').toLowerCase();
    const role = e.getAttribute('role') ||
      (tag === 'a' ? 'link' : tag === 'select' ? 'select' : tag === 'textarea' ? 'textbox' :
       tag === 'input' ? (['checkbox','radio','submit','button'].includes(type) ? type : type === 'password' ? 'password' : 'textbox') :
       e.isContentEditable ? 'textbox' : tag);
    const label = (e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText) ||
                   e.getAttribute('placeholder') || e.getAttribute('title') || e.innerText || e.value ||
                   e.getAttribute('alt') || e.getAttribute('name') || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    let line = `${e.dataset.sparkRef} ${role} ${JSON.stringify(label)}`;
    if (role === 'textbox' || role === 'searchbox' || role === 'combobox') line += ` =${JSON.stringify((e.value || '').slice(0, 60))}`;
    if (tag === 'select') line += ` options:${[...e.options].slice(0, 15).map(o => o.label.trim().slice(0, 30)).join('|')}`;
    if (type === 'checkbox' || type === 'radio') line += e.checked ? ' [checked]' : '';
    if (e.disabled) line += ' [disabled]';
    if (tag === 'a' && e.href && !e.href.startsWith('javascript')) line += ` -> ${e.href.slice(0, 100)}`;
    lines.push(line);
  }
  document.documentElement.dataset.sparkNext = String(next);
  const text = (document.body ? document.body.innerText : '').replace(/\n\s*\n+/g, '\n').slice(0, maxText);
  return {url: location.href, title: document.title, elements: lines.slice(0, 400), text,
          scroll: [Math.round(scrollY), Math.max(0, document.documentElement.scrollHeight - innerHeight)]};
}
"""

# Clicks that cannot be undone wait for the user's explicit permission (--allow-critical).
CRITICAL = re.compile(r'\b(pay|purchase|buy now|place order|checkout|delete account|remove account|'
                      r'change password)\b|결제|구매하기|주문하기|탈퇴|계정 삭제|비밀번호 변경', re.I)


class ActionError(Exception):
    pass


async def snapshot(page: Page, max_text: int = 6000) -> str:
    try:
        data = await page.evaluate(SNAPSHOT_JS, max_text)
    except Exception:  # navigation in flight
        await page.wait_for_load_state('domcontentloaded')
        data = await page.evaluate(SNAPSHOT_JS, max_text)
    top, bottom = data['scroll']
    return (f"URL: {data['url']}\nTITLE: {data['title']}\nSCROLL: {top}/{bottom}px\n"
            f"ELEMENTS:\n" + '\n'.join(data['elements']) + f"\n\nTEXT:\n{data['text']}")


async def screenshot(page: Page) -> str:
    png = await page.screenshot(type='jpeg', quality=60)
    return 'data:image/jpeg;base64,' + base64.b64encode(png).decode()


def locate(page: Page, ref: str):
    if not re.fullmatch(r'e\d+', str(ref or '')):
        raise ActionError(f'bad ref {ref!r}')
    return page.locator(f'[data-spark-ref="{ref}"]').first


async def run_action(page: Page, action: dict, allow_critical: bool = False) -> str:
    do = action.get('do')
    ref = action.get('ref')
    if do == 'goto':
        url = str(action.get('url') or '')
        if not re.match(r'https?://', url):
            raise ActionError('goto needs an http(s) url')
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        return f'opened {page.url}'
    if do in ('click', 'fill', 'select', 'check', 'hover'):
        target = locate(page, ref)
        if await target.count() == 0:
            raise ActionError(f'{ref} is no longer on the page; read the new snapshot')
        if do == 'click':
            text = (await target.evaluate("e => e.innerText || e.value || e.getAttribute('aria-label') || ''"))[:120]
            if CRITICAL.search(text) and not allow_critical:
                raise ActionError(f'"{text}" is a payment/deletion/password step; the user must allow it '
                                  '(--allow-critical). Stop and report instead.')
            await target.click(timeout=8000)
            return f'clicked {ref}'
        if do == 'fill':
            if await target.get_attribute('type') == 'password':
                raise ActionError('password fields are typed by the user, never by the model')
            await target.fill(str(action.get('value') or ''), timeout=8000)
            return f'filled {ref}'
        if do == 'select':
            await target.select_option(label=str(action.get('value') or ''), timeout=8000)
            return f'selected {action.get("value")!r} in {ref}'
        if do == 'check':
            await target.set_checked(bool(action.get('value', True)), timeout=8000)
            return f'set {ref}'
        await target.hover(timeout=8000)
        return f'hovered {ref}'
    if do == 'press':
        await page.keyboard.press(str(action.get('key') or 'Enter'))
        return f'pressed {action.get("key")}'
    if do == 'scroll':
        await page.mouse.wheel(0, int(action.get('dy') or 700))
        return 'scrolled'
    if do == 'back':
        await page.go_back(wait_until='domcontentloaded')
        return 'went back'
    if do == 'wait':
        await page.wait_for_timeout(min(5000, int(action.get('ms') or 1000)))
        return 'waited'
    raise ActionError(f'unknown action {do!r}')
