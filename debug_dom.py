"""Script de debug para inspecionar o CHAT da OLX após clicar no botão."""
import sys
import asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import time
from scraper import create_browser

pw, browser, page = create_browser(headless=False)

# Busca um anúncio
search_url = "https://www.olx.com.br/celulares?ps=1500&pe=2500&q=iPhone+13&opst=2&elbh=1&elbh=2&elcd=1&elcd=2"
print(f"Buscando anúncios...")
page.goto(search_url, timeout=60000, wait_until="load")
time.sleep(5)

first_ad = page.evaluate("""
    (() => {
        const a = document.querySelector('a[data-testid="adcard-link"]');
        return a ? { href: a.href, title: a.title } : null;
    })()
""")
print(f"Anúncio: {first_ad['title']} → {first_ad['href']}")

# Abre o anúncio
page.goto(first_ad['href'], timeout=60000, wait_until="load")
time.sleep(4)

# Clica no botão Chat
print("\nClicando no botão Chat...")
page.click('button:has-text("Chat")')
time.sleep(6)

# Agora inspeciona a página/estado atual
print(f"\nURL após clicar Chat: {page.url}")
print(f"Título: {page.title()}")

# Checa se abriu nova aba
all_pages = browser.pages
print(f"Número de abas: {len(all_pages)}")
for i, p in enumerate(all_pages):
    print(f"  Aba [{i}]: {p.url}")

# Usa a última aba (pode ter aberto nova)
chat_page = all_pages[-1] if len(all_pages) > 1 else page
print(f"\nUsando aba: {chat_page.url}")
time.sleep(3)

print("\n=== TEXTAREAS e INPUTS no chat ===")
inputs = chat_page.evaluate("""
    Array.from(document.querySelectorAll('textarea, input[type="text"], [contenteditable="true"], [role="textbox"]')).map(el => ({
        tag: el.tagName,
        name: el.name || '',
        placeholder: el.placeholder || '',
        testid: el.getAttribute('data-testid') || '',
        ariaLabel: el.getAttribute('aria-label') || '',
        classes: el.className.substring(0, 120),
        outerHTML: el.outerHTML.substring(0, 300),
    }))
""")
for el in inputs:
    print(f"  <{el['tag']}> placeholder=\"{el['placeholder']}\" testid=\"{el['testid']}\" aria=\"{el['ariaLabel']}\"")
    print(f"    HTML: {el['outerHTML']}")
    print()

print("=== BOTÕES no chat ===")
buttons = chat_page.evaluate("""
    Array.from(document.querySelectorAll('button')).map(b => ({
        text: b.textContent?.trim().substring(0, 80),
        testid: b.getAttribute('data-testid') || '',
        ariaLabel: b.getAttribute('aria-label') || '',
        type: b.type || '',
        outerHTML: b.outerHTML.substring(0, 250),
    }))
""")
for i, btn in enumerate(buttons):
    if btn['text'] or btn['testid'] or btn['ariaLabel']:
        print(f"  [{i}] text=\"{btn['text']}\" testid=\"{btn['testid']}\" aria=\"{btn['ariaLabel']}\" type=\"{btn['type']}\"")

print("\n=== Elementos com 'send/enviar/message' ===")
send_els = chat_page.evaluate("""
    Array.from(document.querySelectorAll('[data-testid*="send"], [data-testid*="message"], [aria-label*="Enviar"], [aria-label*="enviar"], button[type="submit"]')).map(el => ({
        tag: el.tagName,
        testid: el.getAttribute('data-testid') || '',
        ariaLabel: el.getAttribute('aria-label') || '',
        text: el.textContent?.trim().substring(0, 80),
        outerHTML: el.outerHTML.substring(0, 250),
    }))
""")
for el in send_els:
    print(f"  <{el['tag']}> testid=\"{el['testid']}\" aria=\"{el['ariaLabel']}\" text=\"{el['text']}\"")
    print(f"    HTML: {el['outerHTML']}")

chat_page.screenshot(path="data/debug_chat.png", full_page=False)
print("\nScreenshot salvo em data/debug_chat.png")

print("\nDone! Pressione Enter para fechar...")
input()
try:
    browser.close()
except Exception:
    pass
pw.stop()
