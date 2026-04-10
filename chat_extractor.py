"""
chat_extractor.py — Módulo 2, Fase 1
Extrai histórico completo de conversas do chat OLX.
Salva em SQLite (tabela chat_history) + exportação JSON.
"""
import sys
import asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import os
import json
import time
import logging
import sqlite3
from datetime import datetime

from playwright.sync_api import sync_playwright
from config import PROFILE_DIR, DB_PATH

logger = logging.getLogger(__name__)

# ── DB Setup ──────────────────────────────────────────────────────────────────

def init_chat_tables():
    """Cria tabelas de histórico de chat se não existirem."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id         TEXT,
            ad_title        TEXT,
            ad_price        TEXT,
            seller_name     TEXT,
            sender          TEXT,
            message         TEXT,
            timestamp       TEXT,
            date_label      TEXT,
            extracted_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chat_id, message, timestamp, sender)
        );

        CREATE TABLE IF NOT EXISTS chat_extraction_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TIMESTAMP,
            finished_at     TIMESTAMP,
            total_chats     INTEGER DEFAULT 0,
            total_messages  INTEGER DEFAULT 0,
            status          TEXT DEFAULT 'running'
        );
    """)
    conn.commit()
    conn.close()


def save_messages_to_db(chat_id: str, ad_title: str, ad_price: str,
                        seller_name: str, messages: list[dict]):
    """Salva mensagens de uma conversa no SQLite."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    inserted = 0
    for msg in messages:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO chat_history
                   (chat_id, ad_title, ad_price, seller_name, sender, message, timestamp, date_label)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (chat_id, ad_title, ad_price, seller_name,
                 msg["sender"], msg["text"], msg["time"], msg.get("date_label", ""))
            )
            inserted += conn.total_changes
        except Exception:
            pass
    conn.commit()
    conn.close()
    return inserted


# ── Extração de mensagens de uma conversa aberta ─────────────────────────────

JS_EXTRACT_MESSAGES = """() => {
    const results = [];

    // Estratégia: buscar todos os spans de texto de mensagem (sc-dDtQUp)
    // O parent de cada span é o bubble div da mensagem
    // Seller msgs: bubble tem span.typo-caption.text-neutral-120 (nome do remetente)
    // My msgs: bubble tem [data-testid="status-icon"] (checkmark de leitura)

    const textSpans = document.querySelectorAll('span[class*="sc-dDtQUp"]');

    for (const span of textSpans) {
        const text = span.innerText?.trim();
        if (!text) continue;

        // Pular alertas de segurança
        if (span.closest('.olx-core-alertbox')) continue;

        // Bubble = parent div
        const bubble = span.parentElement;
        if (!bubble) continue;

        // Determinar remetente
        const senderSpan = bubble.querySelector('.typo-caption.text-neutral-120');
        const statusIcon = bubble.querySelector('[data-testid="status-icon"]');

        let sender = 'me';
        let senderName = '';
        if (senderSpan) {
            sender = 'seller';
            senderName = senderSpan.innerText?.trim();
        }

        // Timestamp (dentro de sub-div no bubble)
        const timeSpan = bubble.querySelector('span[class*="sc-hPiRQo"]');
        const msgTime = timeSpan ? timeSpan.innerText?.trim() : '';

        results.push({
            sender: sender,
            senderName: senderName,
            text: text,
            time: msgTime,
            dateLabel: '',
        });
    }

    // Tentar extrair date labels (separadores como "Hoje", "Ontem", "12/06")
    // O separador de data tem classe sc-fBPDFl + typo-body-small
    const dateSpans = document.querySelectorAll('span[class*="sc-fBPDFl"]');
    const dateLabels = [];
    for (const ds of dateSpans) {
        const t = ds.innerText?.trim();
        if (t && t.length < 20) {
            dateLabels.push(t);
        }
    }
    // Fallback: buscar spans com typo-body-small que parecem datas
    if (dateLabels.length === 0) {
        const fallbackSpans = document.querySelectorAll('span.typo-body-small');
        for (const ds of fallbackSpans) {
            const t = ds.innerText?.trim();
            if (t && (t === 'Hoje' || t === 'Ontem' || /^\\d{2}\\/\\d{2}/.test(t))) {
                dateLabels.push(t);
            }
        }
    }
    if (dateLabels.length > 0 && results.length > 0) {
        // Aplicar o primeiro date label encontrado a todas as mensagens como fallback
        for (const r of results) { r.dateLabel = dateLabels[0]; }
    }

    return results;
}"""

JS_EXTRACT_CHAT_INFO = """() => {
    const info = {adTitle: '', adPrice: '', sellerName: '', chatId: ''};

    // Chat ID da URL
    const urlParams = new URLSearchParams(window.location.search);
    info.chatId = urlParams.get('chat-id') || '';

    // Seller name: buscar nas mensagens recebidas (parent com sc-elEIAz = msg do vendedor)
    // O nome do vendedor é span.typo-caption.text-neutral-120 DENTRO de um bubble de msg
    const msgBubbles = document.querySelectorAll('[class*="sc-bQEPLu"][class*="sc-elEIAz"]');
    for (const bubble of msgBubbles) {
        const nameSpan = bubble.querySelector('.typo-caption.text-neutral-120');
        if (nameSpan) {
            const name = nameSpan.innerText?.trim();
            if (name && name.length > 1 && !name.match(/^[\\d.]+$/)) {
                info.sellerName = name;
                break;
            }
        }
    }

    // Ad title e price: estão no card do anúncio no header da conversa
    // O card tem divs com classes sc-cvzDUw (parent) > sc-fsKmBw (titulo)
    // Price é um span que começa com R$ dentro desse mesmo card
    // Identificar o card: procurar div que contém tanto o título quanto o preço
    const allSpans = document.querySelectorAll('span');
    for (const s of allSpans) {
        const t = s.innerText?.trim();
        if (!t) continue;

        // Preço: span que começa com R$ e está no topo da página
        if (t.startsWith('R$') && !info.adPrice) {
            const rect = s.getBoundingClientRect();
            if (rect.top < 250) {
                info.adPrice = t;
                // O título geralmente é um irmão ou próximo do preço
                const parent = s.parentElement;
                if (parent) {
                    const siblings = parent.querySelectorAll('span');
                    for (const sib of siblings) {
                        const st = sib.innerText?.trim();
                        if (st && st !== t && st.length > 3 && st.length < 120
                            && !st.startsWith('R$')) {
                            info.adTitle = st;
                            break;
                        }
                    }
                }
            }
        }
    }

    return info;
}"""

JS_SCROLL_MSG_TO_TOP = """() => {
    // Scroll o painel de mensagens até o topo pra carregar mensagens antigas
    const panels = document.querySelectorAll('div');
    for (const p of panels) {
        const rect = p.getBoundingClientRect();
        const style = getComputedStyle(p);
        if (rect.left > 200 && rect.width > 400 && rect.height > 200
            && (style.overflowY === 'auto' || style.overflowY === 'scroll')) {
            const oldTop = p.scrollTop;
            p.scrollTop = 0;
            return {scrolled: true, oldTop: oldTop, newTop: p.scrollTop};
        }
    }
    return {scrolled: false};
}"""


# ── Extração principal ────────────────────────────────────────────────────────

class ChatExtractor:
    def __init__(self, max_chats: int = 0, scroll_attempts: int = 3,
                 progress_callback=None):
        """
        max_chats: 0 = extrair todos
        scroll_attempts: quantas vezes dar scroll up pra carregar msgs antigas
        progress_callback: fn(current, total, chat_title) para progresso
        """
        self.max_chats = max_chats
        self.scroll_attempts = scroll_attempts
        self.progress = progress_callback or (lambda *a: None)
        self.all_data = []

    def extract(self) -> dict:
        """Executa extração completa. Retorna resumo."""
        init_chat_tables()

        # Log de extração
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            "INSERT INTO chat_extraction_log (started_at, status) VALUES (?, 'running')",
            (datetime.now().isoformat(),)
        )
        log_id = cur.lastrowid
        conn.commit()
        conn.close()

        total_chats = 0
        total_msgs = 0

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch_persistent_context(
                    channel="msedge",
                    user_data_dir=PROFILE_DIR,
                    headless=False,
                    viewport={"width": 1280, "height": 900},
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                )
                page = browser.pages[0] if browser.pages else browser.new_page()

                # Navegar pro chat
                logger.info("Navegando para chat.olx.com.br...")
                page.goto("https://chat.olx.com.br", wait_until="domcontentloaded", timeout=60000)
                time.sleep(5)

                # Contar conversas disponíveis
                chat_count = page.locator('[data-testid="chat-list-item"]').count()
                logger.info(f"Conversas na lista: {chat_count}")

                if self.max_chats > 0:
                    chat_count = min(chat_count, self.max_chats)

                # Scroll na lista pra carregar todas as conversas
                chat_count = self._scroll_chat_list(page, chat_count)

                # Iterar conversas
                for idx in range(chat_count):
                    try:
                        self.progress(idx + 1, chat_count, "")
                        msgs = self._extract_single_chat(page, idx)
                        if msgs:
                            total_chats += 1
                            total_msgs += len(msgs)
                    except Exception as e:
                        logger.warning(f"Erro na conversa {idx}: {e}")
                        # Voltar pra lista
                        try:
                            page.goto("https://chat.olx.com.br", wait_until="domcontentloaded", timeout=60000)
                            time.sleep(3)
                        except Exception:
                            pass

                browser.close()

            status = "completed"
        except Exception as e:
            logger.error(f"Erro na extração: {e}")
            status = f"error: {str(e)[:100]}"

        # Atualizar log
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """UPDATE chat_extraction_log
               SET finished_at=?, total_chats=?, total_messages=?, status=?
               WHERE id=?""",
            (datetime.now().isoformat(), total_chats, total_msgs, status, log_id)
        )
        conn.commit()
        conn.close()

        # Exportar JSON
        self._export_json()

        return {
            "total_chats": total_chats,
            "total_messages": total_msgs,
            "status": status,
        }

    def _scroll_chat_list(self, page, current_count: int) -> int:
        """Scroll na lista de chats pra carregar mais conversas."""
        last_count = current_count
        for _ in range(10):
            # Scroll o último item into view
            items = page.locator('[data-testid="chat-list-item"]')
            count = items.count()
            if count == 0:
                break
            items.last.scroll_into_view_if_needed()
            time.sleep(2)
            new_count = items.count()
            if new_count == last_count:
                break  # Não carregou mais
            last_count = new_count
            logger.info(f"  Lista de chats: {new_count} conversas carregadas")

        final_count = page.locator('[data-testid="chat-list-item"]').count()
        if self.max_chats > 0:
            final_count = min(final_count, self.max_chats)
        return final_count

    def _extract_single_chat(self, page, idx: int) -> list[dict]:
        """Extrai mensagens de uma conversa pelo índice na lista."""
        # Clicar na conversa
        items = page.locator('[data-testid="chat-list-item"]')
        if idx >= items.count():
            return []

        # Pegar preview da conversa (título do anúncio da lista)
        item = items.nth(idx)
        preview_text = item.inner_text()
        # Estrutura do innerText: "NomeVendedor\nTítuloAnúncio\nÚltimaMensagem\nTimestamp"
        preview_lines = [l.strip() for l in preview_text.splitlines() if l.strip()]
        ad_title_from_list = preview_lines[1] if len(preview_lines) > 1 else ""
        logger.info(f"  [{idx+1}] {preview_lines[0] if preview_lines else '?'} — {ad_title_from_list[:50]}")

        item.click()
        time.sleep(3)

        # Scroll up pra carregar mensagens antigas
        for attempt in range(self.scroll_attempts):
            result = page.evaluate(JS_SCROLL_MSG_TO_TOP)
            if not result.get("scrolled") or result.get("oldTop", 0) == 0:
                break
            time.sleep(2)

        # Extrair info do chat
        chat_info = page.evaluate(JS_EXTRACT_CHAT_INFO)
        ad_title = chat_info.get("adTitle", "") or ad_title_from_list
        ad_price = chat_info.get("adPrice", "")
        seller_name = chat_info.get("sellerName", "")
        chat_id = chat_info.get("chatId", f"chat_{idx}")

        # Fallback: seller_name da lista (primeira linha do preview)
        if not seller_name and preview_lines:
            seller_name = preview_lines[0]

        self.progress(idx + 1, 0, ad_title)

        # Extrair mensagens
        messages = page.evaluate(JS_EXTRACT_MESSAGES)

        if messages:
            # Salvar no DB
            save_messages_to_db(chat_id, ad_title, ad_price, seller_name, messages)

            # Guardar pra JSON
            self.all_data.append({
                "chat_id": chat_id,
                "ad_title": ad_title,
                "ad_price": ad_price,
                "seller_name": seller_name,
                "messages": messages,
            })

            logger.info(f"    → {len(messages)} mensagens extraídas")

        return messages

    def _export_json(self):
        """Exporta todos os dados pra JSON."""
        out_path = os.path.join(os.path.dirname(__file__), "data", "chat_history.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(self.all_data, f, indent=2, ensure_ascii=False)
        logger.info(f"JSON exportado: {out_path} ({len(self.all_data)} conversas)")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import argparse
    parser = argparse.ArgumentParser(description="Extrai histórico de chat OLX")
    parser.add_argument("--max", type=int, default=0, help="Máx conversas (0=todas)")
    parser.add_argument("--scroll", type=int, default=3, help="Tentativas de scroll por conversa")
    args = parser.parse_args()

    def show_progress(current, total, title):
        if total > 0:
            print(f"\r  📨 Extraindo {current}/{total}... {title[:40]}", end="", flush=True)

    print("🚀 Iniciando extração de histórico de chat OLX...")
    print(f"   Máx conversas: {'todas' if args.max == 0 else args.max}")
    print(f"   Scroll attempts: {args.scroll}\n")

    extractor = ChatExtractor(
        max_chats=args.max,
        scroll_attempts=args.scroll,
        progress_callback=show_progress,
    )
    result = extractor.extract()

    print(f"\n\n✅ Extração finalizada!")
    print(f"   Conversas: {result['total_chats']}")
    print(f"   Mensagens: {result['total_messages']}")
    print(f"   Status: {result['status']}")
