import time
import random
import logging
import ctypes

from playwright.sync_api import Page

from database import was_already_sent, log_sent_message, get_today_sent_count

logger = logging.getLogger(__name__)
dbg = logging.getLogger("olxbot_debug")

# Exceção para parada imediata
class StopBotException(Exception):
    pass

# Referência global ao stop_flag para checagem dentro de funções internas
_stop_flag = None


def _check_stop():
    """Checa stop_flag e levanta exceção se ativado."""
    if _stop_flag and _stop_flag():
        raise StopBotException("Finalizado pelo usuário")


def _set_clipboard(text: str):
    """Copia texto para a área de transferência usando a API nativa do Windows (Unicode)."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Declarar tipos para 64-bit Windows
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    user32.OpenClipboard(0)
    user32.EmptyClipboard()

    # CF_UNICODETEXT = 13, encode como UTF-16-LE + null terminator
    data = text.encode('utf-16-le') + b'\x00\x00'
    h = kernel32.GlobalAlloc(0x0042, len(data))
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    user32.SetClipboardData(13, h)
    user32.CloseClipboard()


def _human_delay(min_s: float, max_s: float):
    """Pausa humanizada com variação — interruptível pelo stop_flag."""
    total = random.uniform(min_s, max_s)
    elapsed = 0.0
    step = 0.2
    while elapsed < total:
        _check_stop()
        time.sleep(min(step, total - elapsed))
        elapsed += step


def _type_like_human(page: Page, selector: str, text: str):
    """Digita texto com velocidade humana."""
    page.click(selector)
    for char in text:
        page.keyboard.type(char, delay=random.randint(30, 120))
        if random.random() < 0.05:
            time.sleep(random.uniform(0.3, 0.8))


def _chat_already_has_our_message(chat_page, message: str) -> bool:
    """Verifica se a área de chat ativa já contém uma mensagem nossa."""
    try:
        return chat_page.evaluate("""(msg) => {
            // Pega texto da sidebar (lista de conversas)
            const sidebar = document.querySelector('.olx-list');
            const sidebarText = sidebar ? sidebar.innerText : '';
            // Pega texto total da página
            const fullText = document.body.innerText;
            // Remove texto da sidebar para ficar só com a área do chat ativo
            const chatText = fullText.replace(sidebarText, '');
            return chatText.includes(msg);
        }""", message)
    except Exception:
        return False


def _open_chat_and_send(page: Page, ad: dict, message: str) -> bool:
    """
    Abre o chat diretamente pela URL (sem depender do botão Chat)
    e envia a mensagem. Verifica se já existe mensagem nossa no chat.

    Returns:
        True se a mensagem foi enviada com sucesso.
        "already_in_chat" se já foi enviado para este vendedor.
        False se houve erro.
    Raises:
        StopBotException se o usuário pediu para finalizar.
    """
    chat_page = None
    try:
        _check_stop()
        context = page.context
        chat_url = f"https://chat.olx.com.br/?list-id={ad['id']}"

        chat_page = context.new_page()
        chat_page.goto(chat_url, timeout=30000, wait_until="domcontentloaded")

        _check_stop()

        try:
            chat_page.wait_for_selector('#input-text-message', timeout=15000)
        except Exception:
            logger.warning(f"Campo de mensagem não encontrado no chat: {ad['title']}")
            chat_page.close()
            return False

        _check_stop()

        if _chat_already_has_our_message(chat_page, message):
            logger.info(f"⏭️ Chat já tem mensagem nossa (vendedor repetido): {ad['title']}")
            chat_page.close()
            page.bring_to_front()
            return "already_in_chat"

        _check_stop()

        _set_clipboard(message)

        chat_page.click('#input-text-message')
        chat_page.keyboard.press("Control+v")
        _human_delay(0.1, 0.2)

        chat_page.keyboard.press("Enter")
        _human_delay(0.3, 0.5)

        logger.info(f"✅ Mensagem enviada: {ad['title']}")

        chat_page.close()
        page.bring_to_front()

        return True

    except StopBotException:
        # Fecha chat se estava aberto e re-lança
        if chat_page and not chat_page.is_closed():
            chat_page.close()
        raise

    except Exception as e:
        logger.error(f"Erro ao enviar para {ad.get('title', '?')}: {e}")
        try:
            for p in page.context.pages:
                if "chat.olx.com.br" in p.url:
                    p.close()
        except Exception:
            pass
        return False


def _send_batch_parallel(page: Page, ads_batch: list[dict], message: str) -> list[tuple[dict, object]]:
    """
    Abre todas as abas de chat SIMULTANEAMENTE usando navegação JS
    não-bloqueante, depois faz polling: assim que qualquer aba carrega
    o textarea, envia a mensagem imediatamente e fecha.

    Returns:
        Lista de (ad, result) — result: True | "already_in_chat" | False
    """
    context = page.context
    results: list[tuple[dict, object]] = []
    pending: list[tuple[dict, Page, float]] = []

    POLL_INTERVAL = 0.3
    MAX_WAIT = 20

    dbg.info(f"_send_batch_parallel: disparando {len(ads_batch)} abas")

    try:
        # ── Fase 1: Criar todas as abas e disparar navegação INSTANTÂNEA ──
        # evaluate("location.href=...") retorna imediatamente;
        # a navegação real acontece em background no browser.
        # Diferente de goto() que bloqueia até receber resposta HTTP.
        for ad in ads_batch:
            _check_stop()
            try:
                chat_url = f"https://chat.olx.com.br/?list-id={ad['id']}"
                chat_page = context.new_page()
                try:
                    chat_page.evaluate(f"window.location.href = '{chat_url}'")
                except Exception:
                    # Se evaluate falha (contexto destruído pela nav), a navegação
                    # já iniciou — é o cenário desejado.
                    pass
                pending.append((ad, chat_page, time.time()))
                dbg.info(f"  aba disparada: {ad['id']} - {ad['title'][:40]}")
            except StopBotException:
                raise
            except Exception as e:
                dbg.info(f"  ERRO ao criar aba {ad['id']}: {e}")
                results.append((ad, False))

        dbg.info(f"  {len(pending)} abas disparadas, entrando em polling...")

        # ── Fase 2: Polling — classifica todas, depois envia em RAJADA ──
        while pending:
            _check_stop()
            ready: list[tuple[dict, Page, float]] = []
            still_pending: list[tuple[dict, Page, float]] = []

            # Passo 1: Classificar TODAS as abas (rápido — query_selector é instantâneo)
            for ad, chat_page, opened_at in pending:
                try:
                    if chat_page.is_closed():
                        dbg.info(f"  aba fechada inesperadamente: {ad['id']}")
                        results.append((ad, False))
                        continue

                    textarea = chat_page.query_selector('#input-text-message')

                    if textarea:
                        ready.append((ad, chat_page, opened_at))
                    elif time.time() - opened_at > MAX_WAIT:
                        dbg.info(f"  TIMEOUT: {ad['id']} ({MAX_WAIT}s)")
                        logger.warning(f"Timeout carregando chat: {ad['title']}")
                        results.append((ad, False))
                        if not chat_page.is_closed():
                            chat_page.close()
                    else:
                        still_pending.append((ad, chat_page, opened_at))

                except StopBotException:
                    raise
                except Exception as e:
                    dbg.info(f"  ERRO check {ad['id']}: {e}")
                    results.append((ad, False))
                    if not chat_page.is_closed():
                        chat_page.close()

            # Passo 2: Pipeline simultâneo — fill ALL → Enter ALL → close ALL
            # Visualmente o texto aparece em todas as abas "ao mesmo tempo"
            if ready:
                dbg.info(f"  {len(ready)} abas prontas — pipeline simultâneo")

                # 2a: Filtrar duplicatas (rápido — 1 evaluate por aba)
                to_send: list[tuple[dict, Page, float]] = []
                for ad, chat_page, opened_at in ready:
                    _check_stop()
                    try:
                        if _chat_already_has_our_message(chat_page, message):
                            dbg.info(f"  already_in_chat: {ad['id']}")
                            logger.info(f"⏭️ Chat já tem mensagem nossa: {ad['title']}")
                            results.append((ad, "already_in_chat"))
                            chat_page.close()
                            continue
                        to_send.append((ad, chat_page, opened_at))
                    except StopBotException:
                        raise
                    except Exception as e:
                        dbg.info(f"  ERRO dupe check {ad['id']}: {e}")
                        results.append((ad, False))
                        if not chat_page.is_closed():
                            chat_page.close()

                # 2b: FILL em todas as abas (texto aparece "simultâneo")
                filled: list[tuple[dict, Page, float]] = []
                for ad, chat_page, opened_at in to_send:
                    try:
                        try:
                            chat_page.fill('#input-text-message', message)
                        except Exception:
                            chat_page.bring_to_front()
                            _set_clipboard(message)
                            chat_page.click('#input-text-message')
                            chat_page.keyboard.press("Control+v")
                        filled.append((ad, chat_page, opened_at))
                    except StopBotException:
                        raise
                    except Exception as e:
                        dbg.info(f"  ERRO fill {ad['id']}: {e}")
                        results.append((ad, False))
                        if not chat_page.is_closed():
                            chat_page.close()

                # 2c: ENTER em todas as abas (envio "simultâneo")
                sent: list[tuple[dict, Page, float]] = []
                for ad, chat_page, opened_at in filled:
                    try:
                        chat_page.press('#input-text-message', 'Enter')
                        sent.append((ad, chat_page, opened_at))
                    except StopBotException:
                        raise
                    except Exception as e:
                        dbg.info(f"  ERRO enter {ad['id']}: {e}")
                        results.append((ad, False))
                        if not chat_page.is_closed():
                            chat_page.close()

                # 2d: Espera mínima + CLOSE todas + registrar resultados
                if sent:
                    time.sleep(0.2)
                for ad, chat_page, opened_at in sent:
                    elapsed = time.time() - opened_at
                    dbg.info(f"  ENVIADO: {ad['id']} ({elapsed:.1f}s desde abertura)")
                    logger.info(f"✅ Mensagem enviada: {ad['title']}")
                    results.append((ad, True))
                    if not chat_page.is_closed():
                        chat_page.close()

            pending = still_pending
            if pending:
                time.sleep(POLL_INTERVAL)

    except StopBotException:
        for ad, chat_page, _ in pending:
            if not chat_page.is_closed():
                try:
                    chat_page.close()
                except Exception:
                    pass
        raise

    except Exception as e:
        dbg.info(f"  ERRO geral batch: {e}")
        for ad, chat_page, _ in pending:
            if not chat_page.is_closed():
                try:
                    chat_page.close()
                except Exception:
                    pass
        processed_ids = {a['id'] for a, _ in results}
        for ad in ads_batch:
            if ad['id'] not in processed_ids:
                results.append((ad, False))

    try:
        page.bring_to_front()
    except Exception:
        pass

    dbg.info(f"_send_batch_parallel: {len(results)} resultados")
    return results


def send_messages(
    page: Page,
    ads: list[dict],
    message: str,
    min_delay: float = 5,
    max_delay: float = 12,
    batch_size: int = 50,
    batch_pause: int = 300,
    daily_limit: int = 300,
    max_per_run: int = 200,
    parallel_tabs: int = 1,
    progress_callback=None,
    stop_flag=None,
    pause_flag=None,
) -> dict:
    """
    Envia mensagens para uma lista de anúncios.

    Args:
        page: Página do Playwright (logada)
        ads: Lista de anúncios
        message: Texto da mensagem
        min_delay / max_delay: Delay entre envios
        batch_size: Tamanho de cada lote (para pausa entre lotes)
        batch_pause: Pausa entre lotes (segundos)
        daily_limit: Limite diário de mensagens
        max_per_run: Máximo por execução
        parallel_tabs: Quantas abas abrir simultaneamente (1-9)
        progress_callback: callback(stats, total, current_ad, event_type)
        stop_flag: Callable que retorna True para finalizar
        pause_flag: Callable que retorna True enquanto pausado

    Returns:
        Dict com estatísticas {sent, skipped, errors, stopped_reason}
    """
    stats = {"sent": 0, "skipped": 0, "skipped_db": 0, "skipped_chat": 0, "errors": 0, "stopped_reason": None}

    global _stop_flag
    _stop_flag = stop_flag

    parallel_tabs = max(1, min(9, parallel_tabs))

    dbg.info(f"send_messages() chamado com {len(ads)} anúncios, parallel_tabs={parallel_tabs}")
    dbg.info(f"  max_per_run={max_per_run}, daily_limit={daily_limit}, batch_size={batch_size}")
    dbg.info(f"  stop_flag={stop_flag}, stop_flag()={stop_flag() if stop_flag else 'N/A'}")

    def _process_result(ad, result):
        """Processa resultado de um envio e atualiza stats."""
        if result == "already_in_chat":
            log_sent_message(ad["id"], ad["title"], ad["url"], ad.get("price", ""), message)
            stats["skipped"] += 1
            stats["skipped_chat"] += 1
            dbg.info(f"  SKIP (chat): {ad['id']}")
            if progress_callback:
                progress_callback(stats, len(ads), ad, "skipped_chat")
        elif result:
            log_sent_message(ad["id"], ad["title"], ad["url"], ad.get("price", ""), message)
            stats["sent"] += 1
            dbg.info(f"  ENVIADO OK: {ad['id']}")
            if progress_callback:
                progress_callback(stats, len(ads), ad, "sent")
        else:
            stats["errors"] += 1
            dbg.info(f"  ERRO: {ad['id']}")
            if progress_callback:
                progress_callback(stats, len(ads), ad, "error")

    try:
        i = 0
        while i < len(ads):
            # Coletar próximo lote de ads enviáveis (pula DB dupes inline)
            sendable: list[dict] = []
            while i < len(ads) and len(sendable) < parallel_tabs:
                ad = ads[i]
                i += 1

                dbg.info(f"--- Ad [{i-1}] ID:{ad['id']} | {ad['title'][:50]} ---")

                # Espera enquanto pausado
                while pause_flag and pause_flag():
                    _check_stop()
                    time.sleep(0.5)

                _check_stop()

                # Limites
                if stats["sent"] >= max_per_run:
                    dbg.info(f"  PAROU: sent={stats['sent']} >= max_per_run={max_per_run}")
                    stats["stopped_reason"] = f"Limite por execução atingido ({max_per_run})"
                    break

                today_count = get_today_sent_count()
                if today_count >= daily_limit:
                    dbg.info(f"  PAROU: today_count={today_count} >= daily_limit={daily_limit}")
                    stats["stopped_reason"] = f"Limite diário atingido ({daily_limit})"
                    break

                # DB dedup
                if was_already_sent(ad["id"]):
                    stats["skipped"] += 1
                    stats["skipped_db"] += 1
                    dbg.info(f"  SKIP (DB): já enviado")
                    logger.info(f"⏭️ Já enviado (ID {ad['id']}): {ad['title']}")
                    if progress_callback:
                        progress_callback(stats, len(ads), ad, "skipped_db")
                    continue

                sendable.append(ad)

            if stats.get("stopped_reason"):
                break

            if not sendable:
                continue

            # ── Enviar: sequencial (1 aba) ou paralelo (N abas) ──
            if len(sendable) == 1:
                ad = sendable[0]
                dbg.info(f"  ENVIO SEQUENCIAL: {ad['id']}")
                if progress_callback:
                    progress_callback(stats, len(ads), ad, "sending")
                result = _open_chat_and_send(page, ad, message)
                _process_result(ad, result)
            else:
                dbg.info(f"  ENVIO PARALELO: {len(sendable)} abas")
                if progress_callback:
                    progress_callback(stats, len(ads), sendable[0], f"sending_parallel_{len(sendable)}")
                batch_results = _send_batch_parallel(page, sendable, message)
                for ad, result in batch_results:
                    _process_result(ad, result)

            # Delay entre envios/lotes
            _human_delay(min_delay, max_delay)

            # Pausa entre lotes grandes
            if stats["sent"] > 0 and stats["sent"] % batch_size == 0 and stats["sent"] < max_per_run:
                logger.info(f"⏸️ Lote de {batch_size} concluído. Pausando {batch_pause}s...")
                if progress_callback:
                    progress_callback(stats, len(ads), None, "batch_pause")
                elapsed = 0.0
                while elapsed < batch_pause:
                    _check_stop()
                    time.sleep(min(0.5, batch_pause - elapsed))
                    elapsed += 0.5

    except StopBotException:
        dbg.info(f"StopBotException capturada! stats={stats}")
        stats["stopped_reason"] = "Finalizado pelo usuário"
    finally:
        _stop_flag = None

    dbg.info(f"send_messages() finalizado: {stats}")
    return stats
