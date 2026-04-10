import time
import random
import logging
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright, Page
from playwright_stealth import Stealth

from config import OLX_BASE_URL, MAX_PAGES, PAGE_LOAD_TIMEOUT, PROFILE_DIR
from messenger import StopBotException

logger = logging.getLogger(__name__)
dbg = logging.getLogger("olxbot_debug")


def create_browser(headless: bool = False) -> tuple:
    """Cria browser com perfil persistente e stealth."""
    pw = sync_playwright().start()
    browser = pw.chromium.launch_persistent_context(
        channel="msedge",
        user_data_dir=PROFILE_DIR,
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
        viewport={"width": 1366, "height": 768},
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    stealth = Stealth()
    stealth.apply_stealth_sync(page)
    return pw, browser, page


def close_browser(pw, browser):
    """Fecha browser e playwright."""
    browser.close()
    pw.stop()


def build_search_url(
    search_term: str,
    category: str = "celulares",
    page_num: int = 1,
    min_price: int = 0,
    max_price: int = 0,
    conditions: list[int] | None = None,
    battery_health: list[int] | None = None,
    memory: list[int] | None = None,
    color: list[int] | None = None,
    shipping: int = 0,
) -> str:
    """Monta URL de busca da OLX com filtros nativos."""
    base = f"{OLX_BASE_URL}/{category}"

    params = []
    if min_price > 0:
        params.append(f"ps={min_price}")
    if max_price > 0:
        params.append(f"pe={max_price}")
    params.append(f"q={quote_plus(search_term)}")
    if shipping > 0:
        params.append(f"opst={shipping}")
    if color:
        for cl in color:
            params.append(f"elc={cl}")
    if battery_health:
        for b in battery_health:
            params.append(f"elbh={b}")
    if memory:
        for m in memory:
            params.append(f"cps={m}")
    if conditions:
        for c in conditions:
            params.append(f"elcd={c}")
    if page_num > 1:
        params.append(f"o={page_num}")

    return f"{base}?{'&'.join(params)}"


def _extract_ads_from_page(page: Page) -> list[dict]:
    """Extrai dados dos anúncios da página atual."""
    # Seletor real da OLX: a[data-testid="adcard-link"]
    selector = 'a[data-testid="adcard-link"]'

    try:
        page.wait_for_selector(selector, timeout=10000)
    except Exception:
        logger.warning("Nenhum anúncio encontrado na página")
        return []

    # Extrai anúncios via JS usando o seletor correto
    ads = page.evaluate("""
        () => {
            const results = [];
            const seen = new Set();
            const links = document.querySelectorAll('a[data-testid="adcard-link"]');

            for (const a of links) {
                const href = a.href || '';
                if (!href) continue;

                // ID do anúncio: último segmento numérico da URL
                // Ex: https://pb.olx.com.br/paraiba/celulares/iphone-13-1491817090
                const idMatch = href.match(/-(\\d+)$/);
                const id = idMatch ? idMatch[1] : null;
                if (!id || seen.has(id)) continue;
                seen.add(id);

                // Título: h2 dentro do link (class olx-adcard__title)
                const title = a.querySelector('h2')?.textContent?.trim() || '';
                if (!title) continue;

                // Card pai: subir até o section ou li mais próximo
                const card = a.closest('section') || a.closest('li') || a.parentElement;

                // Preço: procura span/p com R$
                let price = '';
                if (card) {
                    const texts = card.querySelectorAll('span, p');
                    for (const el of texts) {
                        const t = el.textContent?.trim() || '';
                        if (t.startsWith('R$')) { price = t; break; }
                    }
                }

                // Localização: texto com " - " (ex: "João Pessoa - PB")
                let location = '';
                if (card) {
                    const texts = card.querySelectorAll('span, p');
                    for (const el of texts) {
                        const t = el.textContent?.trim() || '';
                        if (t.includes(' - ') && !t.startsWith('R$')) {
                            location = t;
                        }
                    }
                }

                results.push({
                    id: id,
                    title: title,
                    price: price,
                    location: location,
                    url: href,
                });
            }
            return results;
        }
    """)

    logger.info(f"Extraídos {len(ads)} anúncios da página")
    return ads


def _get_results_info(page: Page) -> tuple[int, int, int] | None:
    """Extrai (start, end, total) do texto 'X - Y de Z resultados' da OLX.
    Retorna None se não encontrar."""
    import re
    try:
        text = page.evaluate("""
            () => {
                // Procura texto tipo "101 - 149 de 149 resultados" ou "1 - 50 de 2.935"
                const els = document.querySelectorAll('span, p, div');
                for (const el of els) {
                    const t = el.textContent.trim();
                    if (/\\d+\\s*-\\s*\\d+\\s+de\\s+[\\d.]+/.test(t) && t.includes('resultado')) {
                        return t;
                    }
                }
                // Fallback: body text
                const body = document.body.innerText;
                const match = body.match(/(\\d[\\d.]*)\\s*-\\s*(\\d[\\d.]*)\\s+de\\s+([\\d.]+)\\s*resultado/);
                return match ? match[0] : null;
            }
        """)
        if not text:
            return None

        m = re.search(r'([\d.]+)\s*-\s*([\d.]+)\s+de\s+([\d.]+)', text)
        if not m:
            return None

        start = int(m.group(1).replace('.', ''))
        end = int(m.group(2).replace('.', ''))
        total = int(m.group(3).replace('.', ''))
        return start, end, total
    except Exception:
        return None


def _has_next_page(page: Page, current_page: int) -> bool:
    """Verifica se existe próxima página checando o seletor de paginação."""
    # Tenta múltiplos seletores conhecidos da OLX
    selectors = [
        'a[data-lurker-detail="next_page"]',
        '[aria-label="Próxima página"]',
        f'a[href*="o={current_page + 1}"]',
    ]
    for sel in selectors:
        if page.query_selector(sel):
            return True
    return False


def scrape_page(
    page_obj: Page,
    search_term: str,
    category: str,
    page_num: int,
    min_price: int = 0,
    max_price: int = 0,
    conditions: list[int] | None = None,
    battery_health: list[int] | None = None,
    memory: list[int] | None = None,
    color: list[int] | None = None,
    shipping: int = 0,
    prev_total: int | None = None,
) -> tuple[list[dict], bool, str, int | None]:
    """
    Scrape uma única página de resultados.
    Retorna (ads, has_next, url, total_results).
    prev_total: total de resultados da página anterior (para detectar ampliação da OLX).
    """
    url = build_search_url(
        search_term, category, page_num,
        min_price, max_price, conditions, battery_health, memory, color, shipping,
    )
    dbg.info(f"scrape_page: página {page_num} — {url}")
    logger.info(f"Scraping página {page_num}: {url}")

    page_obj.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="load")
    time.sleep(random.uniform(3, 5))

    try:
        page_obj.wait_for_selector('a[data-testid="adcard-link"]', timeout=15000)
    except Exception:
        dbg.info(f"scrape_page: timeout esperando anúncios na página {page_num}")
        logger.warning("Timeout esperando anúncios carregarem")

    ads = _extract_ads_from_page(page_obj)

    # Detecção inteligente de última página via "X - Y de Z resultados"
    results_info = _get_results_info(page_obj)
    dbg.info(f"scrape_page: results_info={results_info}")

    has_next = False
    current_total = None
    if results_info:
        start, end, total = results_info
        current_total = total
        dbg.info(f"scrape_page: mostrando {start}-{end} de {total} resultados")

        # Se total mudou drasticamente, a OLX ampliou a busca (bug da última página)
        if prev_total is not None and total > prev_total * 3:
            dbg.info(f"scrape_page: DETECTADO ampliação da OLX! prev_total={prev_total} → total={total}. Parando.")
            logger.warning(f"OLX ampliou busca: {prev_total} → {total}. Ignorando página.")
            return [], False, url, current_total

        # Se end >= total, estamos na última página real
        if end >= total:
            dbg.info(f"scrape_page: última página real (end={end} >= total={total})")
            has_next = False
        else:
            has_next = _has_next_page(page_obj, page_num)
    else:
        # Fallback: usar apenas seletor do botão
        has_next = _has_next_page(page_obj, page_num)

    dbg.info(f"scrape_page: {len(ads)} anúncios extraídos, has_next={has_next}")

    return ads, has_next, url, current_total


def scrape_ads(
    search_term: str,
    category: str = "celulares",
    max_pages: int = MAX_PAGES,
    start_page: int = 1,
    min_price: int = 0,
    max_price: int = 0,
    conditions: list[int] | None = None,
    battery_health: list[int] | None = None,
    memory: list[int] | None = None,
    color: list[int] | None = None,
    shipping: int = 0,
    headless: bool = False,
    page_obj: Page | None = None,
    progress_callback=None,
    stop_flag=None,
) -> list[dict]:
    """
    Coleta anúncios da OLX com filtros nativos na URL.

    Args:
        search_term: Termo de busca
        category: Categoria OLX ('celulares', 'brasil', etc.)
        max_pages: Máximo de páginas a percorrer
        min_price: Preço mínimo (0 = sem filtro)
        max_price: Preço máximo (0 = sem filtro)
        conditions: Lista de códigos de condição (1=Novo, 2=Usado excelente, etc.)
        battery_health: Lista de códigos de bateria (1=Perfeita, 2=Boa, etc.)
        shipping: Filtrar apenas Entrega Fácil
        headless: Rodar sem interface gráfica
        page_obj: Reutilizar página existente do Playwright
        progress_callback: Função callback(page_num, total_ads) para progresso
        stop_flag: Callable que retorna True para parar coleta

    Returns:
        Lista de dicts com dados dos anúncios
    """
    own_browser = page_obj is None
    pw, browser, page = (None, None, None)

    if own_browser:
        pw, browser, page = create_browser(headless=headless)
    else:
        page = page_obj

    all_ads = []
    seen_ids = set()

    try:
        for page_num in range(start_page, start_page + max_pages):
            # Checar stop antes de cada página
            if stop_flag and stop_flag():
                dbg.info("scraper: stop_flag detectado antes da página")
                logger.info("⏹️ Coleta interrompida pelo usuário")
                raise StopBotException("Coleta interrompida pelo usuário")

            url = build_search_url(
                search_term, category, page_num,
                min_price, max_price, conditions, battery_health, memory, color, shipping,
            )
            dbg.info(f"scraper: página {page_num} — {url}")
            logger.info(f"Scraping página {page_num}: {url}")

            page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="load")
            time.sleep(random.uniform(3, 5))

            # Espera os cards de anúncio aparecerem
            try:
                page.wait_for_selector('a[data-testid="adcard-link"]', timeout=15000)
            except Exception:
                dbg.info(f"scraper: timeout esperando anúncios na página {page_num}")
                logger.warning("Timeout esperando anúncios carregarem")

            ads = _extract_ads_from_page(page)
            dbg.info(f"scraper: _extract_ads_from_page retornou {len(ads)} anúncios")

            new_ads = 0
            for ad in ads:
                if ad["id"] not in seen_ids:
                    seen_ids.add(ad["id"])
                    all_ads.append(ad)
                    new_ads += 1
                    dbg.info(f"  + AD id={ad['id']} | {ad['title'][:50]}")

            dbg.info(f"scraper: página {page_num}: {new_ads} novos (total acum: {len(all_ads)})")
            logger.info(f"Página {page_num}: {new_ads} novos anúncios (total: {len(all_ads)})")

            if progress_callback:
                progress_callback(page_num, len(all_ads))

            if new_ads == 0:
                dbg.info("scraper: 0 novos, parando coleta")
                logger.info("Nenhum anúncio novo encontrado, parando.")
                break

            if not _has_next_page(page, page_num):
                dbg.info("scraper: sem próxima página, parando")
                logger.info("Sem próxima página, parando.")
                break

            time.sleep(random.uniform(1, 3))

    except StopBotException:
        raise
    except Exception as e:
        dbg.info(f"scraper: EXCEPTION: {e}")
        logger.error(f"Erro no scraping: {e}")
        raise
    finally:
        if own_browser and browser:
            close_browser(pw, browser)

    return all_ads
