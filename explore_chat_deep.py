"""Exploração profunda: clica num chat-list-item e extrai mensagens."""
import sys, asyncio, json, time, os
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.sync_api import sync_playwright
from config import PROFILE_DIR

OUT = os.path.join(os.path.dirname(__file__), "data", "dom_exploration")
os.makedirs(OUT, exist_ok=True)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            channel="msedge", user_data_dir=PROFILE_DIR, headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            locale="pt-BR", timezone_id="America/Sao_Paulo",
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        print("1. Navegando pro chat...")
        page.goto("https://chat.olx.com.br", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Detalhar primeiros chat-list-items
        print("2. Extraindo dados dos chat-list-items...")
        items_data = page.evaluate("""() => {
            const items = document.querySelectorAll('[data-testid="chat-list-item"]');
            return Array.from(items).slice(0, 5).map((item, i) => ({
                index: i,
                tag: item.tagName,
                outerHTML: item.outerHTML.substring(0, 2000),
                innerText: item.innerText.substring(0, 300),
                role: item.getAttribute('role'),
                cursor: getComputedStyle(item).cursor,
            }));
        }""")
        with open(os.path.join(OUT, "10_chat_items_detail.json"), "w", encoding="utf-8") as f:
            json.dump(items_data, f, indent=2, ensure_ascii=False)
        for it in items_data:
            print(f"   [{it['index']}] {it['tag']} cursor={it['cursor']} | {it['innerText'][:80]}")

        # Clicar no primeiro chat-list-item
        print("\n3. Clicando no primeiro chat-list-item...")
        first_item = page.locator('[data-testid="chat-list-item"]').first
        first_item.click()
        time.sleep(5)

        page.screenshot(path=os.path.join(OUT, "11_after_click.png"), full_page=True)
        print("   Screenshot salva")

        # Testids da conversa aberta
        conv_testids = page.evaluate("""() => {
            const els = document.querySelectorAll('[data-testid]');
            const ids = {};
            els.forEach(el => {
                const tid = el.getAttribute('data-testid');
                ids[tid] = (ids[tid] || 0) + 1;
            });
            return ids;
        }""")
        with open(os.path.join(OUT, "12_conv_testids.json"), "w", encoding="utf-8") as f:
            json.dump(conv_testids, f, indent=2, ensure_ascii=False)
        print(f"   testids encontrados: {len(conv_testids)}")
        for tid, cnt in sorted(conv_testids.items()):
            print(f"     {tid}: {cnt}")

        # Extrair mensagens
        print("\n4. Extraindo mensagens...")
        messages = page.evaluate("""() => {
            const results = [];
            const msgSels = [
                '[data-testid*="message"]', '[data-testid*="bubble"]',
                '[data-testid*="chat-message"]', '[data-testid*="msg"]',
            ];
            for (const sel of msgSels) {
                const els = document.querySelectorAll(sel);
                if (els.length > 0) {
                    els.forEach((el, i) => {
                        results.push({
                            selector: sel, index: i,
                            text: el.innerText?.substring(0, 300),
                            classes: el.className?.substring(0, 200),
                            tag: el.tagName,
                            testid: el.getAttribute('data-testid'),
                        });
                    });
                    return {found: sel, count: els.length, results: results};
                }
            }
            // Fallback: spans no painel direito
            const allDivs = document.querySelectorAll('div');
            for (const d of Array.from(allDivs)) {
                const rect = d.getBoundingClientRect();
                if (rect.left > 300 && rect.width > 400 && rect.height > 300) {
                    const spans = d.querySelectorAll('span');
                    spans.forEach((s, i) => {
                        const txt = s.innerText?.trim();
                        if (txt && txt.length > 2 && i < 60) {
                            results.push({
                                selector: 'panel-span', index: i,
                                text: txt.substring(0, 200),
                                classes: s.className?.substring(0, 200),
                                parentClasses: s.parentElement?.className?.substring(0, 200),
                                grandparentClasses: s.parentElement?.parentElement?.className?.substring(0, 200),
                            });
                        }
                    });
                    if (results.length > 0)
                        return {found: 'panel-span-fallback', count: results.length, results: results};
                }
            }
            return {found: 'none', count: 0, results: []};
        }""")
        with open(os.path.join(OUT, "13_messages_detail.json"), "w", encoding="utf-8") as f:
            json.dump(messages, f, indent=2, ensure_ascii=False)
        found = messages.get("found", "?")
        count = messages.get("count", 0)
        print(f"   Método: {found} ({count} items)")
        for msg in messages.get("results", [])[:20]:
            print(f"     {msg.get('text', '')[:90]}")

        # HTML bruto do painel de conversa
        print("\n5. Extraindo HTML do painel de conversa...")
        panel_html = page.evaluate("""() => {
            // Procurar o container de mensagens (lado direito, grande)
            const allDivs = document.querySelectorAll('div');
            let best = null;
            let bestArea = 0;
            for (const d of allDivs) {
                const rect = d.getBoundingClientRect();
                if (rect.left > 250 && rect.width > 400 && rect.height > 200) {
                    const area = rect.width * rect.height;
                    if (area > bestArea) {
                        bestArea = area;
                        best = d;
                    }
                }
            }
            if (best) return best.innerHTML.substring(0, 15000);
            return document.body.innerHTML.substring(0, 15000);
        }""")
        with open(os.path.join(OUT, "14_panel_html.html"), "w", encoding="utf-8") as f:
            f.write(panel_html)
        print(f"   HTML salvo ({len(panel_html)} chars)")

        # URL atual
        print(f"\n   URL final: {page.url}")

        browser.close()
        print("\n✅ Done!")


if __name__ == "__main__":
    run()
