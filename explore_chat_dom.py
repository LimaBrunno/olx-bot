"""
Explorador de DOM do chat OLX.
Abre chat.olx.com.br com o perfil logado e salva a estrutura.
"""
import sys, os, json, time

# Asyncio policy fix para Windows
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.sync_api import sync_playwright
from config import PROFILE_DIR

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data", "dom_exploration")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def explore():
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            channel="msedge",
            user_data_dir=PROFILE_DIR,
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # 1. Navegar pro chat
        print("🔄 Navegando para chat.olx.com.br...")
        page.goto("https://chat.olx.com.br", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Screenshot da lista de chats
        page.screenshot(path=os.path.join(OUTPUT_DIR, "01_chat_list.png"), full_page=True)
        print("📸 Screenshot da lista salva")

        # 2. Extrair estrutura da lista de chats
        print("\n📋 Extraindo estrutura da lista de chats...")

        # Pegar o HTML simplificado (sem estilos inline)
        chat_list_structure = page.evaluate("""() => {
            function simplify(el, depth = 0) {
                if (depth > 8) return null;
                const tag = el.tagName?.toLowerCase() || '';
                const id = el.id ? `#${el.id}` : '';
                const classes = el.className && typeof el.className === 'string'
                    ? '.' + el.className.split(' ').filter(c => c).join('.')
                    : '';
                const text = el.childNodes.length === 1 && el.childNodes[0].nodeType === 3
                    ? el.childNodes[0].textContent.trim().substring(0, 50)
                    : '';
                const attrs = {};
                ['data-testid', 'data-lurker-detail', 'role', 'aria-label', 'href', 'data-id'].forEach(a => {
                    if (el.getAttribute(a)) attrs[a] = el.getAttribute(a);
                });
                const children = Array.from(el.children)
                    .map(c => simplify(c, depth + 1))
                    .filter(Boolean);
                return {
                    tag: `${tag}${id}${classes}`,
                    text: text || undefined,
                    attrs: Object.keys(attrs).length ? attrs : undefined,
                    children: children.length ? children : undefined
                };
            }
            return simplify(document.body);
        }""")

        with open(os.path.join(OUTPUT_DIR, "01_chat_list_structure.json"), "w", encoding="utf-8") as f:
            json.dump(chat_list_structure, f, indent=2, ensure_ascii=False)
        print("💾 Estrutura da lista salva")

        # 3. Tentar encontrar itens de conversa
        print("\n🔍 Buscando seletores de conversa...")
        selectors_to_try = [
            "[data-testid*='chat']",
            "[data-testid*='conversation']",
            "[data-testid*='message']",
            "[data-testid*='thread']",
            "[data-testid*='inbox']",
            "[data-testid*='room']",
            "[role='listitem']",
            "[role='list'] > *",
            "a[href*='chat']",
            "a[href*='room']",
            "li",
        ]

        found_selectors = {}
        for sel in selectors_to_try:
            try:
                count = page.locator(sel).count()
                if count > 0:
                    found_selectors[sel] = count
                    # Pegar texto do primeiro elemento
                    first_text = page.locator(sel).first.inner_text()[:200]
                    found_selectors[f"{sel}_sample"] = first_text
            except Exception:
                pass

        print(f"  Seletores encontrados: {len([k for k in found_selectors if '_sample' not in k])}")
        for sel, val in found_selectors.items():
            if '_sample' not in sel:
                print(f"    {sel} → {val} elementos")

        with open(os.path.join(OUTPUT_DIR, "02_found_selectors.json"), "w", encoding="utf-8") as f:
            json.dump(found_selectors, f, indent=2, ensure_ascii=False)

        # 4. Extrair todos os data-testid da página
        all_testids = page.evaluate("""() => {
            const els = document.querySelectorAll('[data-testid]');
            const ids = {};
            els.forEach(el => {
                const tid = el.getAttribute('data-testid');
                ids[tid] = (ids[tid] || 0) + 1;
            });
            return ids;
        }""")
        print(f"\n🏷️ data-testid encontrados: {len(all_testids)}")
        for tid, count in sorted(all_testids.items()):
            print(f"    {tid}: {count}")

        with open(os.path.join(OUTPUT_DIR, "03_all_testids.json"), "w", encoding="utf-8") as f:
            json.dump(all_testids, f, indent=2, ensure_ascii=False)

        # 5. Tentar clicar na primeira conversa
        print("\n💬 Tentando abrir primeira conversa...")

        # Buscar links/itens clicáveis que pareçam conversas
        conversation_candidates = page.evaluate("""() => {
            const results = [];
            // Procurar elementos com texto que pareçam conversas
            const allLinks = document.querySelectorAll('a');
            allLinks.forEach((a, i) => {
                if (i < 30) {
                    results.push({
                        href: a.href,
                        text: a.innerText?.substring(0, 100),
                        testid: a.getAttribute('data-testid'),
                        classes: a.className?.substring(0, 100)
                    });
                }
            });
            return results;
        }""")

        with open(os.path.join(OUTPUT_DIR, "04_conversation_candidates.json"), "w", encoding="utf-8") as f:
            json.dump(conversation_candidates, f, indent=2, ensure_ascii=False)
        print(f"  Links encontrados: {len(conversation_candidates)}")

        # Tentar clicar no primeiro item que parece conversa
        clicked = False
        for cand in conversation_candidates:
            href = cand.get("href", "")
            if "chat" in href and "list-id" in href:
                print(f"  ✅ Encontrada conversa: {href[:80]}")
                page.goto(href, wait_until="domcontentloaded", timeout=60000)
                time.sleep(5)
                clicked = True
                break

        if not clicked:
            for cand in conversation_candidates:
                href = cand.get("href", "")
                if "chat" in href and cand.get("text", "").strip():
                    print(f"  🔗 Tentando: {href[:80]}")
                    page.goto(href, wait_until="domcontentloaded", timeout=60000)
                    time.sleep(5)
                    clicked = True
                    break

        if clicked:
            # 6. Screenshot da conversa aberta
            page.screenshot(path=os.path.join(OUTPUT_DIR, "05_conversation_open.png"), full_page=True)
            print("📸 Screenshot da conversa salva")

            # 7. Extrair estrutura da conversa
            conv_testids = page.evaluate("""() => {
                const els = document.querySelectorAll('[data-testid]');
                const ids = {};
                els.forEach(el => {
                    const tid = el.getAttribute('data-testid');
                    ids[tid] = (ids[tid] || 0) + 1;
                });
                return ids;
            }""")

            with open(os.path.join(OUTPUT_DIR, "06_conversation_testids.json"), "w", encoding="utf-8") as f:
                json.dump(conv_testids, f, indent=2, ensure_ascii=False)
            print(f"  data-testid na conversa: {len(conv_testids)}")
            for tid, count in sorted(conv_testids.items()):
                print(f"    {tid}: {count}")

            # 8. Extrair mensagens visíveis
            messages = page.evaluate("""() => {
                const results = [];
                // Tentar vários seletores de mensagens
                const selectors = [
                    '[data-testid*="message"]',
                    '[data-testid*="bubble"]',
                    '[class*="message"]',
                    '[class*="Message"]',
                    '[class*="bubble"]',
                    '[class*="Bubble"]',
                ];
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    if (els.length > 0) {
                        els.forEach((el, i) => {
                            results.push({
                                selector: sel,
                                index: i,
                                text: el.innerText?.substring(0, 200),
                                testid: el.getAttribute('data-testid'),
                                classes: el.className?.substring(0, 150),
                                tag: el.tagName
                            });
                        });
                        break; // usa o primeiro seletor que funcionar
                    }
                }
                if (results.length === 0) {
                    // Fallback: pegar todos os textos visíveis
                    const walker = document.createTreeWalker(
                        document.body, NodeFilter.SHOW_TEXT, null, false
                    );
                    let count = 0;
                    while (walker.nextNode() && count < 50) {
                        const text = walker.currentNode.textContent.trim();
                        if (text.length > 5 && text.length < 300) {
                            const parent = walker.currentNode.parentElement;
                            results.push({
                                selector: 'text_fallback',
                                text: text,
                                parentTag: parent?.tagName,
                                parentClasses: parent?.className?.substring(0, 100),
                                parentTestid: parent?.getAttribute('data-testid')
                            });
                            count++;
                        }
                    }
                }
                return results;
            }""")

            with open(os.path.join(OUTPUT_DIR, "07_messages_found.json"), "w", encoding="utf-8") as f:
                json.dump(messages, f, indent=2, ensure_ascii=False)
            print(f"\n📨 Mensagens/textos encontrados: {len(messages)}")
            for msg in messages[:10]:
                print(f"    [{msg.get('selector', '?')}] {msg.get('text', '')[:80]}")
        else:
            print("  ❌ Não encontrou link de conversa clicável")

        # 9. Salvar URL atual e cookies relevantes
        final_info = {
            "final_url": page.url,
            "title": page.title(),
        }
        with open(os.path.join(OUTPUT_DIR, "08_final_info.json"), "w", encoding="utf-8") as f:
            json.dump(final_info, f, indent=2, ensure_ascii=False)

        print(f"\n✅ Exploração concluída! Arquivos em: {OUTPUT_DIR}")
        print("   Mantenha o browser aberto pra inspecionar manualmente se necessário.")

        input("\n⏎ Pressione Enter pra fechar o browser...")
        browser.close()


if __name__ == "__main__":
    explore()
