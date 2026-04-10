import sys
import asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import os
import time
import random
import logging
import threading
import streamlit as st
from datetime import datetime

from scraper import create_browser, close_browser, scrape_ads, scrape_page, build_search_url
from messenger import send_messages, StopBotException
from database import (
    get_today_sent_count,
    get_total_sent,
    get_execution_history,
    start_execution,
    finish_execution,
)
from config import (
    DEFAULT_MIN_DELAY,
    DEFAULT_MAX_DELAY,
    DEFAULT_BATCH_SIZE,
    DEFAULT_BATCH_PAUSE,
    DEFAULT_DAILY_LIMIT,
    DEFAULT_MAX_PER_RUN,
    DEFAULT_PARALLEL_TABS,
    MAX_PAGES,
)

# ─── Debug logger para arquivo ──────────────────────────────
DEBUG_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "debug.log")

def _setup_debug_logger():
    """Configura logger que escreve em data/debug.log."""
    dbg = logging.getLogger("olxbot_debug")
    dbg.setLevel(logging.DEBUG)
    if not dbg.handlers:
        fh = logging.FileHandler(DEBUG_LOG_PATH, encoding="utf-8", mode="a")
        fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        dbg.addHandler(fh)
    return dbg

dbg_logger = _setup_debug_logger()
# ─── Mapeamentos de filtros OLX ─────────────────────────────
CONDITION_MAP = {
    "Novo": 1,
    "Usado - Excelente": 2,
    "Usado - Bom": 3,
    "Recondicionado": 4,
    "Com defeitos ou avarias": 5,
}
BATTERY_MAP = {
    "Perfeita": 1,
    "Boa": 2,
    "OK": 3,
    "Ruim": 4,
    "Muito ruim": 5,
}
CATEGORY_MAP = {
    "Celulares": "celulares",
}
MEMORY_MAP = {
    "512MB": 1,
    "1GB": 2,
    "2GB": 3,
    "4GB": 4,
    "8GB": 5,
    "16GB": 6,
    "32GB": 7,
    "64GB": 8,
    "128GB": 9,
    "256GB": 10,
    "512GB": 11,
    "1TB": 12,
}
COLOR_MAP = {
    "Preto": 1,
    "Prata": 2,
    "Branco": 3,
    "Verde": 4,
    "Amarelo": 5,
    "Vermelho": 6,
    "Rosa": 7,
    "Dourado": 8,
    "Azul": 9,
    "Cinza": 10,
    "Laranja": 11,
    "Roxo": 12,
    "Bronze": 13,
    "Outros": 14,
}

st.set_page_config(page_title="OLX Bot", page_icon="🤖", layout="wide")

# ─── Save/Load de configurações ─────────────────────────────
import json
from pathlib import Path

CONFIGS_DIR = Path(__file__).parent / "data" / "configs"
CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
LAST_CONFIG_FILE = CONFIGS_DIR / "_last_used.txt"


def _list_configs() -> list[str]:
    return sorted([f.stem for f in CONFIGS_DIR.glob("*.json")])


def _save_config(name: str, data: dict):
    (CONFIGS_DIR / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    LAST_CONFIG_FILE.write_text(name, encoding="utf-8")


def _load_config(name: str) -> dict | None:
    path = CONFIGS_DIR / f"{name}.json"
    if path.exists():
        LAST_CONFIG_FILE.write_text(name, encoding="utf-8")
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _delete_config(name: str):
    path = CONFIGS_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
    if LAST_CONFIG_FILE.exists() and LAST_CONFIG_FILE.read_text(encoding="utf-8").strip() == name:
        LAST_CONFIG_FILE.unlink()


def _get_last_config_name() -> str | None:
    if LAST_CONFIG_FILE.exists():
        name = LAST_CONFIG_FILE.read_text(encoding="utf-8").strip()
        if (CONFIGS_DIR / f"{name}.json").exists():
            return name
    return None


# ─── Estado global ──────────────────────────────────────────
if "running" not in st.session_state:
    st.session_state.running = False
if "stop_event" not in st.session_state:
    st.session_state.stop_event = threading.Event()
if "pause_event" not in st.session_state:
    st.session_state.pause_event = threading.Event()
if "log" not in st.session_state:
    st.session_state.log = []
if "results" not in st.session_state:
    st.session_state.results = None
if "bot_status" not in st.session_state:
    st.session_state.bot_status = {}
if "bot_thread" not in st.session_state:
    st.session_state.bot_thread = None

# Auto-carregar última config usada na primeira abertura
if "autoloaded" not in st.session_state:
    st.session_state.autoloaded = True
    last_name = _get_last_config_name()
    if last_name:
        data = json.loads((CONFIGS_DIR / f"{last_name}.json").read_text(encoding="utf-8"))
        KEY_MAP = {
            "_cfg_search": "search_term",
            "_cfg_pages": "max_pages",
            "_cfg_startpage": "start_page",
            "_cfg_startad": "start_ad",
            "_cfg_msg": "message",
            "_cfg_pmin": "min_price",
            "_cfg_pmax": "max_price",
            "_cfg_condition": "condition",
            "_cfg_battery": "battery_health",
            "_cfg_memory": "memory",
            "_cfg_color": "color",
            "_cfg_shipping": "shipping",
            "_cfg_dmin": "min_delay",
            "_cfg_dmax": "max_delay",
            "_cfg_batch": "batch_size",
            "_cfg_bpause": "batch_pause",
            "_cfg_dlimit": "daily_limit",
            "_cfg_maxrun": "max_per_run",
            "_cfg_parallel": "parallel_tabs",
        }
        for widget_key, config_key in KEY_MAP.items():
            if config_key in data:
                st.session_state[widget_key] = data[config_key]
        # Carregar tags de palavras
        rw = data.get("required_words", "")
        st.session_state._tags_required = [w.strip() for w in rw.split(",") if w.strip()] if rw else []
        bw = data.get("blocked_words", "")
        st.session_state._tags_blocked = [w.strip() for w in bw.split(",") if w.strip()] if bw else []
        st.session_state.loaded_config = data
        st.toast(f"📂 Config '{last_name}' carregada automaticamente")


def add_log(msg: str):
    st.session_state.log.append(msg)


# ─── Sidebar ────────────────────────────────────────────────
with st.sidebar:
    st.title("🤖 OLX Bot")
    st.caption("Automação de mensagens na OLX")

    st.divider()
    st.metric("Enviadas hoje", get_today_sent_count())
    st.metric("Total histórico", get_total_sent())

    st.divider()
    st.subheader("📜 Últimas execuções")
    history = get_execution_history(5)
    if history:
        for h in history:
            status_icon = "✅" if h["status"] == "completed" else "⏹️"
            st.text(f"{status_icon} {h['search_term']}: {h['total_sent']} enviadas")
    else:
        st.caption("Nenhuma execução ainda.")

# ─── Abas ───────────────────────────────────────────────────
tab_run, tab_login, tab_history = st.tabs(["🚀 Executar", "🔑 Login OLX", "📊 Histórico"])

# ─── Aba: Login ─────────────────────────────────────────────
with tab_login:
    st.header("🔑 Login na OLX")
    st.info(
        "1️⃣ Clique em **Abrir navegador** → faça login na OLX\n\n"
        "2️⃣ Depois de logado, **feche o navegador** e clique em **Confirmar Login**"
    )

    if "login_browser" not in st.session_state:
        st.session_state.login_browser = None

    col_open, col_confirm = st.columns(2)

    with col_open:
        if st.button("🌐 Abrir navegador", use_container_width=True):
            # Fecha instância anterior se existir
            if st.session_state.login_browser:
                try:
                    st.session_state.login_browser[0].stop()
                except Exception:
                    pass

            pw, browser, page = create_browser(headless=False)
            page.goto("https://www.olx.com.br/account", timeout=30000)
            st.session_state.login_browser = (pw, browser)
            st.success("✅ Navegador aberto! Faça login na OLX.")

    with col_confirm:
        if st.button("✅ Confirmar Login", use_container_width=True):
            if st.session_state.login_browser:
                try:
                    pw, browser = st.session_state.login_browser
                    browser.close()
                    pw.stop()
                except Exception:
                    pass
                st.session_state.login_browser = None
            st.success("✅ Sessão salva com sucesso! Pode usar a aba Executar.")

# ─── Aba: Histórico ─────────────────────────────────────────
with tab_history:
    st.header("📊 Histórico de Execuções")
    history_full = get_execution_history(50)
    if history_full:
        for h in history_full:
            status_icon = "✅" if h["status"] == "completed" else "⏹️" if h["status"] == "stopped" else "❌"
            with st.expander(f"{status_icon} {h['search_term']} — {h['started_at'][:16]}"):
                c1, c2, c3 = st.columns(3)
                c1.metric("Encontrados", h["total_found"])
                c2.metric("Enviadas", h["total_sent"])
                c3.metric("Pulados", h["total_skipped"])
                st.text(f"Status: {h['status']}")
                if h["finished_at"]:
                    st.text(f"Finalizado: {h['finished_at'][:16]}")
    else:
        st.info("Nenhuma execução registrada ainda.")

# ─── Aba: Executar ──────────────────────────────────────────
with tab_run:
    st.header("🚀 Configuração de Envio")

    # ─── Save / Load configs ─────────────────────────────────
    cfg = st.session_state.loaded_config or {}

    with st.expander("💾 Salvar / Carregar configuração", expanded=False):
        saved_configs = _list_configs()

        sl_col1, sl_col2 = st.columns(2)

        with sl_col1:
            save_name = st.text_input("Nome da config", placeholder="ex: iphone-sp-barato")
            if st.button("💾 Salvar", use_container_width=True, disabled=not save_name):
                _save_config(save_name, {
                    "search_term": st.session_state.get("_cfg_search", "iPhone 13"),
                    "max_pages": st.session_state.get("_cfg_pages", 1),
                    "start_page": st.session_state.get("_cfg_startpage", 1),
                    "start_ad": st.session_state.get("_cfg_startad", 1),
                    "required_words": ",".join(st.session_state.get("_tags_required", [])),
                    "blocked_words": ",".join(st.session_state.get("_tags_blocked", [])),
                    "message": st.session_state.get("_cfg_msg", "Olá, tudo bem? Faz envio?"),
                    "min_price": st.session_state.get("_cfg_pmin", 1500),
                    "max_price": st.session_state.get("_cfg_pmax", 2000),
                    "condition": st.session_state.get("_cfg_condition", ["Novo", "Usado - Excelente"]),
                    "battery_health": st.session_state.get("_cfg_battery", ["Perfeita", "Boa"]),
                    "memory": st.session_state.get("_cfg_memory", []),
                    "color": st.session_state.get("_cfg_color", []),
                    "shipping": st.session_state.get("_cfg_shipping", "Entrega Fácil"),
                    "min_delay": st.session_state.get("_cfg_dmin", DEFAULT_MIN_DELAY),
                    "max_delay": st.session_state.get("_cfg_dmax", DEFAULT_MAX_DELAY),
                    "batch_size": st.session_state.get("_cfg_batch", DEFAULT_BATCH_SIZE),
                    "batch_pause": st.session_state.get("_cfg_bpause", DEFAULT_BATCH_PAUSE // 60),
                    "daily_limit": st.session_state.get("_cfg_dlimit", DEFAULT_DAILY_LIMIT),
                    "max_per_run": st.session_state.get("_cfg_maxrun", DEFAULT_MAX_PER_RUN),
                    "parallel_tabs": st.session_state.get("_cfg_parallel", DEFAULT_PARALLEL_TABS),
                })
                st.success(f"✅ Config '{save_name}' salva!")
                st.rerun()

        with sl_col2:
            if saved_configs:
                chosen = st.selectbox("Configs salvas", saved_configs)
                lc1, lc2 = st.columns(2)
                with lc1:
                    if st.button("📂 Carregar", use_container_width=True):
                        data = _load_config(chosen)
                        if data:
                            # Seta diretamente as keys dos widgets no session_state
                            KEY_MAP = {
                                "_cfg_search": "search_term",
                                "_cfg_pages": "max_pages",
                                "_cfg_startpage": "start_page",
                                "_cfg_startad": "start_ad",
                                "_cfg_msg": "message",
                                "_cfg_pmin": "min_price",
                                "_cfg_pmax": "max_price",
                                "_cfg_condition": "condition",
                                "_cfg_battery": "battery_health",
                                "_cfg_memory": "memory",
                                "_cfg_color": "color",
                                "_cfg_shipping": "shipping",
                                "_cfg_dmin": "min_delay",
                                "_cfg_dmax": "max_delay",
                                "_cfg_batch": "batch_size",
                                "_cfg_bpause": "batch_pause",
                                "_cfg_dlimit": "daily_limit",
                                "_cfg_maxrun": "max_per_run",
                                "_cfg_parallel": "parallel_tabs",
                            }
                            for widget_key, config_key in KEY_MAP.items():
                                if config_key in data:
                                    st.session_state[widget_key] = data[config_key]
                            # Carregar tags de palavras
                            rw = data.get("required_words", "")
                            st.session_state._tags_required = [w.strip() for w in rw.split(",") if w.strip()] if rw else []
                            bw = data.get("blocked_words", "")
                            st.session_state._tags_blocked = [w.strip() for w in bw.split(",") if w.strip()] if bw else []
                        st.success(f"✅ Config '{chosen}' carregada!")
                        st.rerun()
                with lc2:
                    if st.button("🗑️ Excluir", use_container_width=True):
                        _delete_config(chosen)
                        st.warning(f"Config '{chosen}' excluída.")
                        st.rerun()
            else:
                st.caption("Nenhuma config salva ainda.")

    # ─── Campos do formulário ─────────────────────────────────
    SHIPPING_OPTIONS = ["Entrega Fácil", "Pague Online", "Sem filtro"]

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🔍 Busca")
        search_term = st.text_input("Termo de busca", value=cfg.get("search_term", "iPhone 13"), key="_cfg_search")
        max_pages = st.slider("Páginas a percorrer", 1, MAX_PAGES, cfg.get("max_pages", 1), key="_cfg_pages")
        start_page = st.number_input("Começar da página", min_value=1, value=cfg.get("start_page", 1), key="_cfg_startpage")
        start_ad = st.number_input("Começar do anúncio nº", min_value=1, value=cfg.get("start_ad", 1), key="_cfg_startad", help="Pula os primeiros N-1 anúncios da primeira página")

        st.subheader("🔤 Filtro por título")

        # ── Palavras obrigatórias (tag input) ──
        if "_tags_required" not in st.session_state:
            saved = cfg.get("required_words", "")
            st.session_state._tags_required = [w.strip() for w in saved.split(",") if w.strip()] if saved else []

        st.caption("✅ Palavras obrigatórias — título DEVE conter TODAS")
        req_add_col, req_btn_col = st.columns([4, 1])
        with req_add_col:
            req_new = st.text_input("Adicionar obrigatória", key="_req_input", label_visibility="collapsed", placeholder="Digite e clique +")
        with req_btn_col:
            if st.button("➕", key="_req_add", use_container_width=True):
                word = req_new.strip()
                if word and word not in st.session_state._tags_required:
                    st.session_state._tags_required.append(word)
                    st.rerun()

        if st.session_state._tags_required:
            cols = st.columns(min(len(st.session_state._tags_required), 6))
            for idx, word in enumerate(st.session_state._tags_required):
                with cols[idx % 6]:
                    if st.button(f"✅ {word} ✕", key=f"_req_del_{idx}", use_container_width=True):
                        st.session_state._tags_required.pop(idx)
                        st.rerun()

        required_words = ",".join(st.session_state._tags_required)

        # ── Palavras bloqueadas (tag input) ──
        if "_tags_blocked" not in st.session_state:
            saved = cfg.get("blocked_words", "")
            st.session_state._tags_blocked = [w.strip() for w in saved.split(",") if w.strip()] if saved else []

        st.caption("🚫 Palavras bloqueadas — título NÃO pode conter nenhuma")
        blk_add_col, blk_btn_col = st.columns([4, 1])
        with blk_add_col:
            blk_new = st.text_input("Adicionar bloqueada", key="_blk_input", label_visibility="collapsed", placeholder="Digite e clique +")
        with blk_btn_col:
            if st.button("➕", key="_blk_add", use_container_width=True):
                word = blk_new.strip()
                if word and word not in st.session_state._tags_blocked:
                    st.session_state._tags_blocked.append(word)
                    st.rerun()

        if st.session_state._tags_blocked:
            cols = st.columns(min(len(st.session_state._tags_blocked), 6))
            for idx, word in enumerate(st.session_state._tags_blocked):
                with cols[idx % 6]:
                    if st.button(f"🚫 {word} ✕", key=f"_blk_del_{idx}", use_container_width=True):
                        st.session_state._tags_blocked.pop(idx)
                        st.rerun()

        blocked_words = ",".join(st.session_state._tags_blocked)

        st.subheader("💬 Mensagem")
        message = st.text_area(
            "Mensagem para enviar",
            value=cfg.get("message", "Olá, tudo bem? Faz envio?"),
            height=120,
            key="_cfg_msg",
        )

    with col2:
        st.subheader("🔧 Filtros")

        f_col1, f_col2 = st.columns(2)
        with f_col1:
            min_price = st.number_input("Preço mínimo (R$)", min_value=0, value=cfg.get("min_price", 1500), step=100, key="_cfg_pmin")
        with f_col2:
            max_price = st.number_input("Preço máximo (R$)", min_value=0, value=cfg.get("max_price", 2000), step=100, help="0 = sem limite", key="_cfg_pmax")

        condition = st.multiselect(
            "Situação do aparelho",
            list(CONDITION_MAP.keys()),
            default=cfg.get("condition", ["Novo", "Usado - Excelente"]),
            help="Vazio = todas as situações",
            key="_cfg_condition",
        )

        battery_health = st.multiselect(
            "Saúde da bateria",
            list(BATTERY_MAP.keys()),
            default=cfg.get("battery_health", ["Perfeita", "Boa"]),
            help="Vazio = todas",
            key="_cfg_battery",
        )

        memory = st.multiselect(
            "Memória do aparelho",
            list(MEMORY_MAP.keys()),
            default=cfg.get("memory", []),
            help="Vazio = todas as memórias",
            key="_cfg_memory",
        )

        color = st.multiselect(
            "Cor",
            list(COLOR_MAP.keys()),
            default=cfg.get("color", []),
            help="Vazio = todas as cores",
            key="_cfg_color",
        )

        shipping = st.selectbox(
            "📦 Tipo de envio",
            SHIPPING_OPTIONS,
            index=SHIPPING_OPTIONS.index(cfg.get("shipping", "Entrega Fácil")),
            key="_cfg_shipping",
        )

        st.subheader("⚙️ Anti-bloqueio")
        ab_col1, ab_col2 = st.columns(2)
        with ab_col1:
            min_delay = st.number_input("Delay mín (s)", min_value=1, value=cfg.get("min_delay", DEFAULT_MIN_DELAY), key="_cfg_dmin")
            batch_size = st.number_input("Tamanho do lote", min_value=1, value=cfg.get("batch_size", DEFAULT_BATCH_SIZE), key="_cfg_batch")
            daily_limit = st.number_input("Limite diário", min_value=1, value=cfg.get("daily_limit", DEFAULT_DAILY_LIMIT), key="_cfg_dlimit")
        with ab_col2:
            max_delay = st.number_input("Delay máx (s)", min_value=1, value=cfg.get("max_delay", DEFAULT_MAX_DELAY), key="_cfg_dmax")
            batch_pause = st.number_input("Pausa entre lotes (min)", min_value=1, value=cfg.get("batch_pause", DEFAULT_BATCH_PAUSE // 60), key="_cfg_bpause")
            max_per_run = st.number_input("Máx por execução", min_value=1, value=cfg.get("max_per_run", DEFAULT_MAX_PER_RUN), key="_cfg_maxrun")

        parallel_tabs = st.slider(
            "🗂️ Abas simultâneas",
            min_value=1, max_value=9,
            value=cfg.get("parallel_tabs", DEFAULT_PARALLEL_TABS),
            help="Abre N chats ao mesmo tempo. Mais rápido, mas pode chamar atenção da OLX se muito alto.",
            key="_cfg_parallel",
        )

    st.divider()

    # ─── Link com filtros aplicados ───────────────────────────
    link_col1, link_col2 = st.columns([5, 1])
    with link_col2:
        st.button("🔄 Atualizar link", use_container_width=True)

    conditions_preview = [CONDITION_MAP[c] for c in condition] if condition else None
    battery_preview = [BATTERY_MAP[b] for b in battery_health] if battery_health else None
    memory_preview = [MEMORY_MAP[m] for m in memory] if memory else None
    color_preview = [COLOR_MAP[c] for c in color] if color else None
    shipping_map = {"Entrega Fácil": 2, "Pague Online": 1, "Sem filtro": 0}
    shipping_value = shipping_map[shipping]

    preview_url = build_search_url(
        search_term=search_term,
        category="celulares",
        min_price=min_price,
        max_price=max_price,
        conditions=conditions_preview,
        battery_health=battery_preview,
        memory=memory_preview,
        color=color_preview,
        shipping=shipping_value,
    )

    with link_col1:
        st.code(preview_url, language=None)
    st.caption("🔗 Link da busca com filtros aplicados — clique no campo acima para copiar")

    st.divider()

    # ─── Botões de controle ──────────────────────────────────
    start_btn = False
    if st.session_state.running:
        col_pause, col_stop = st.columns(2)
        with col_pause:
            is_paused = st.session_state.pause_event.is_set()
            if is_paused:
                if st.button("▶️ Retomar", use_container_width=True, type="primary"):
                    st.session_state.pause_event.clear()
                    st.toast("▶️ Retomando...")
                    st.rerun()
            else:
                if st.button("⏸️ Pausar", use_container_width=True, type="secondary"):
                    st.session_state.pause_event.set()
                    st.toast("⏸️ Pausado!")
                    st.rerun()
        with col_stop:
            if st.button("⏹️ Finalizar", use_container_width=True, type="secondary"):
                st.session_state.stop_event.set()
                st.session_state.pause_event.clear()
                st.toast("⏹️ Finalizando...")
                st.rerun()
    else:
        start_btn = st.button(
            "🚀 Iniciar Envio",
            use_container_width=True,
            type="primary",
        )

    # ─── Execução ────────────────────────────────────────────
    # Fase 1: Clicou iniciar → salva params, lança thread, rerun
    if not st.session_state.running and start_btn and search_term and message:
        st.session_state.running = True
        st.session_state.stop_event.clear()
        st.session_state.pause_event.clear()
        st.session_state.log = []
        st.session_state.results = None
        st.session_state.bot_status = {"text": "Iniciando...", "pct": 0, "done": False, "stats": None, "started_at": time.time(), "finished_at": None}

        p = {
            "search_term": search_term,
            "message": message,
            "max_pages": max_pages,
            "start_page": start_page,
            "start_ad": start_ad,
            "required_words": required_words,
            "blocked_words": blocked_words,
            "min_price": min_price,
            "max_price": max_price,
            "min_delay": min_delay,
            "max_delay": max_delay,
            "batch_size": batch_size,
            "batch_pause": batch_pause,
            "daily_limit": daily_limit,
            "max_per_run": max_per_run,
            "parallel_tabs": parallel_tabs,
            "condition": condition,
            "battery_health": battery_health,
            "memory": memory,
            "color": color,
            "shipping": shipping,
        }

        # Refs locais aos events (seguros para thread)
        stop_ev = st.session_state.stop_event
        pause_ev = st.session_state.pause_event
        bot_status = st.session_state.bot_status

        # Preparar filtros nativos OLX
        conditions_values = [CONDITION_MAP[c] for c in p["condition"]] if p["condition"] else None
        battery_values = [BATTERY_MAP[b] for b in p["battery_health"]] if p["battery_health"] else None
        memory_values = [MEMORY_MAP[m] for m in p["memory"]] if p["memory"] else None
        color_values = [COLOR_MAP[c] for c in p["color"]] if p["color"] else None
        shipping_map_conv = {"Entrega Fácil": 2, "Pague Online": 1, "Sem filtro": 0}
        shipping_value_conv = shipping_map_conv[p["shipping"]]

        def bot_thread_func():
            """Roda em thread separada — NÃO tocar em widgets Streamlit aqui.
            Intercala coleta e envio: scrape 1 página → envia → próxima página."""
            stop_ev.clear()
            pause_ev.clear()

            dbg_logger.info("=" * 60)
            dbg_logger.info(f"NOVA EXECUÇÃO (interleaved) — busca: {p['search_term']}")
            dbg_logger.info(f"  stop_event={stop_ev.is_set()}, pause_event={pause_ev.is_set()}")
            dbg_logger.info(f"  required_words='{p['required_words']}', blocked_words='{p['blocked_words']}'")
            dbg_logger.info(f"  max_per_run={p['max_per_run']}, daily_limit={p['daily_limit']}, parallel_tabs={p['parallel_tabs']}")
            dbg_logger.info(f"  start_page={p['start_page']}, start_ad={p['start_ad']}, max_pages={p['max_pages']}")

            exec_id = start_execution(p["search_term"])

            # Preparar filtros de título
            req_list = [w.strip().lower() for w in p["required_words"].split(",") if w.strip()]
            block_list = [w.strip().lower() for w in p["blocked_words"].split(",") if w.strip()]
            dbg_logger.info(f"Filtros título — required: {req_list}, blocked: {block_list}")

            # Stats acumulados entre todas as páginas
            total_stats = {
                "sent": 0, "skipped": 0, "skipped_db": 0,
                "skipped_chat": 0, "errors": 0, "stopped_reason": None,
            }
            total_collected = 0
            total_after_filter = 0
            seen_ids = set()
            prev_total_results = None  # Para detectar ampliação da OLX

            def msg_progress(page_stats, total, current_ad, event_type):
                """Callback do send_messages — atualiza UI com stats acumulados."""
                # Combina stats da página atual com acumulado de páginas anteriores
                cum_sent = total_stats["sent"] + page_stats["sent"]
                cum_skip = total_stats["skipped"] + page_stats["skipped"]
                cum_err = total_stats["errors"] + page_stats["errors"]
                processed = cum_sent + cum_skip + cum_err

                ad_title = current_ad['title'][:40] if current_ad else '?'
                label = f"✅{cum_sent} ⏭️{cum_skip} ❌{cum_err}"

                if event_type == "batch_pause":
                    bot_status["text"] = f"⏸️ Pausa entre lotes... ({cum_sent} enviadas)"
                elif event_type == "skipped_db":
                    bot_status["text"] = f"⏭️ Já no DB: {ad_title} | {label}"
                elif event_type == "skipped_chat":
                    bot_status["text"] = f"💬 Já no chat: {ad_title} | {label}"
                elif event_type == "sending":
                    bot_status["text"] = f"📨 Enviando: {ad_title}... | {label}"
                elif event_type.startswith("sending_parallel_"):
                    n = event_type.split("_")[-1]
                    bot_status["text"] = f"🗂️ Abrindo {n} abas: {ad_title}... | {label}"
                elif event_type == "sent":
                    bot_status["text"] = f"✅ Enviado: {ad_title} | {label}"
                elif event_type == "error":
                    bot_status["text"] = f"❌ Erro: {ad_title} | {label}"

            try:
                pw, browser, page = create_browser(headless=False)

                try:
                    for page_num in range(p["start_page"], p["start_page"] + p["max_pages"]):
                        # ── Checar stop antes de cada página ──
                        if stop_ev.is_set():
                            dbg_logger.info(f"stop_event detectado antes da página {page_num}")
                            total_stats["stopped_reason"] = "Finalizado pelo usuário"
                            break

                        # ── SCRAPING: coletar 1 página ──
                        bot_status["phase"] = "scraping"
                        bot_status["text"] = f"🔍 Página {page_num}: Coletando anúncios..."

                        page_ads, has_next, url, page_total = scrape_page(
                            page_obj=page,
                            search_term=p["search_term"],
                            category="celulares",
                            page_num=page_num,
                            min_price=p["min_price"],
                            max_price=p["max_price"],
                            conditions=conditions_values,
                            battery_health=battery_values,
                            memory=memory_values,
                            color=color_values,
                            shipping=shipping_value_conv,
                            prev_total=prev_total_results,
                        )

                        # Atualizar total para detectar ampliação na próxima página
                        if page_total is not None:
                            prev_total_results = page_total

                        # Se scrape_page retornou vazio por ampliação, parar
                        if not page_ads and not has_next:
                            dbg_logger.info(f"Página {page_num}: scrape_page retornou vazio, parando")
                            break

                        # Dedup dentro da sessão
                        new_ads = []
                        for ad in page_ads:
                            if ad["id"] not in seen_ids:
                                seen_ids.add(ad["id"])
                                new_ads.append(ad)

                        dbg_logger.info(f"Página {page_num}: {len(new_ads)} novos anúncios (dedup: {len(page_ads)}→{len(new_ads)})")

                        if not new_ads:
                            dbg_logger.info("0 novos anúncios, parando coleta")
                            break

                        # start_ad: só na primeira página
                        if page_num == p["start_page"] and p["start_ad"] > 1:
                            new_ads = new_ads[p["start_ad"] - 1:]
                            dbg_logger.info(f"start_ad={p['start_ad']}: {len(new_ads)} restantes")

                        total_collected += len(new_ads)

                        # ── FILTRO DE TÍTULO ──
                        if req_list or block_list:
                            filtered = []
                            for ad in new_ads:
                                title_lower = ad["title"].lower()
                                if req_list and not all(w in title_lower for w in req_list):
                                    dbg_logger.info(f"  FILTRADO (required): {ad['title'][:50]}")
                                    continue
                                if block_list and any(w in title_lower for w in block_list):
                                    dbg_logger.info(f"  FILTRADO (blocked): {ad['title'][:50]}")
                                    continue
                                filtered.append(ad)
                            dbg_logger.info(f"Filtro título: {len(new_ads)} → {len(filtered)}")
                            new_ads = filtered

                        total_after_filter += len(new_ads)

                        if not new_ads:
                            dbg_logger.info(f"Página {page_num}: 0 após filtro, próxima página...")
                            if not has_next:
                                break
                            time.sleep(random.uniform(1, 3))
                            continue

                        # ── ENVIO: mandar mensagens para anúncios desta página ──
                        remaining = p["max_per_run"] - total_stats["sent"]
                        if remaining <= 0:
                            dbg_logger.info(f"max_per_run atingido ({total_stats['sent']})")
                            total_stats["stopped_reason"] = f"Limite por execução atingido ({p['max_per_run']})"
                            break

                        ads_to_send = new_ads[:remaining]
                        bot_status["phase"] = "sending"
                        bot_status["text"] = (
                            f"📨 Página {page_num}: Enviando para {len(ads_to_send)} anúncios... "
                            f"(✅{total_stats['sent']} acum.)"
                        )
                        dbg_logger.info(f"Enviando {len(ads_to_send)} anúncios da página {page_num}")

                        page_result = send_messages(
                            page=page,
                            ads=ads_to_send,
                            message=p["message"],
                            min_delay=p["min_delay"],
                            max_delay=p["max_delay"],
                            batch_size=p["batch_size"],
                            batch_pause=p["batch_pause"] * 60,
                            daily_limit=p["daily_limit"],
                            max_per_run=remaining,
                            parallel_tabs=p["parallel_tabs"],
                            progress_callback=msg_progress,
                            stop_flag=lambda: stop_ev.is_set(),
                            pause_flag=lambda: pause_ev.is_set(),
                        )

                        dbg_logger.info(f"send_messages página {page_num}: {page_result}")

                        # Acumular stats
                        for key in ("sent", "skipped", "skipped_db", "skipped_chat", "errors"):
                            total_stats[key] += page_result[key]

                        if page_result.get("stopped_reason"):
                            total_stats["stopped_reason"] = page_result["stopped_reason"]
                            break

                        if not has_next:
                            dbg_logger.info("Sem próxima página")
                            break

                        time.sleep(random.uniform(1, 3))

                    # ── Finalização ──
                    dbg_logger.info(f"RESULTADO FINAL: {total_stats}")
                    bot_status["pct"] = 1.0
                    bot_status["stats"] = total_stats

                    if total_stats.get("stopped_reason"):
                        bot_status["text"] = f"⏹️ {total_stats['stopped_reason']}"
                        finish_execution(exec_id, total_collected, total_stats["sent"], total_stats["skipped"], "stopped")
                    else:
                        bot_status["text"] = (
                            f"✅ Concluído! {total_stats['sent']} enviadas, "
                            f"{total_stats['skipped']} puladas, {total_stats['errors']} erros"
                        )
                        finish_execution(exec_id, total_collected, total_stats["sent"], total_stats["skipped"], "completed")

                finally:
                    close_browser(pw, browser)

            except StopBotException:
                dbg_logger.info(f"StopBotException no bot_thread_func. stats={total_stats}")
                bot_status["text"] = f"⏹️ Finalizado pelo usuário (✅{total_stats['sent']} enviadas)"
                bot_status["stats"] = total_stats
                try:
                    finish_execution(exec_id, total_collected, total_stats["sent"], total_stats["skipped"], "stopped")
                except Exception:
                    pass

            except Exception as e:
                dbg_logger.info(f"Exception no bot_thread_func: {e}")
                bot_status["text"] = f"❌ Erro: {e}"
                try:
                    finish_execution(exec_id, total_collected, total_stats["sent"], total_stats["skipped"], "error")
                except Exception:
                    pass

            finally:
                bot_status["finished_at"] = time.time()
                bot_status["done"] = True

        t = threading.Thread(target=bot_thread_func, daemon=True)
        st.session_state.bot_thread = t
        t.start()
        st.rerun()

    elif not st.session_state.running and start_btn:
        st.warning("⚠️ Preencha o termo de busca e a mensagem.")

    # ─── Fase 2: Polling da thread em execução ────────────────
    if st.session_state.running:
        bs = st.session_state.bot_status

        # Mostrar progresso
        phase = bs.get("phase", "")
        if phase == "scraping":
            st.info("🔍 **FASE 1 — Coletando anúncios** (o bot navega pelas páginas de busca e lê os anúncios do DOM, sem clicar em cada um)")
        elif phase == "sending":
            st.info("📨 **FASE 2 — Enviando mensagens** (abrindo chat de cada anúncio)")

        st.progress(min(bs.get("pct", 0), 1.0))
        status_label = bs.get("text", "Iniciando...")
        if st.session_state.pause_event.is_set() and not bs.get("done"):
            status_label = f"⏸️ PAUSADO — {status_label}"
        st.text(status_label)

        # Preview de anúncios (só na primeira vez)
        if bs.get("ads_preview"):
            st.caption(f"📋 Primeiros anúncios: {bs['ads_preview']}")

        # Verificar se thread terminou
        t = st.session_state.bot_thread
        if bs.get("done") or (t and not t.is_alive()):
            st.session_state.running = False
            st.session_state.results = bs.get("stats")
            st.session_state.bot_thread = None
            st.rerun()
        else:
            time.sleep(1)
            st.rerun()

    # ─── Resultado da última execução ─────────────────────────
    if st.session_state.results:
        st.divider()
        st.subheader("📊 Última Execução")
        r = st.session_state.results

        # Tempo de execução
        bs = st.session_state.bot_status
        t_start = bs.get("started_at")
        t_end = bs.get("finished_at")
        if t_start and t_end:
            elapsed = int(t_end - t_start)
            mins, secs = divmod(elapsed, 60)
            hrs, mins = divmod(mins, 60)
            if hrs > 0:
                time_str = f"{hrs}h {mins}min {secs}s"
            elif mins > 0:
                time_str = f"{mins}min {secs}s"
            else:
                time_str = f"{secs}s"
            st.caption(f"⏱️ Tempo total: **{time_str}**")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("✅ Enviadas", r.get("sent", 0))
        c2.metric("⏭️ Já no DB", r.get("skipped_db", 0))
        c3.metric("💬 Já no chat", r.get("skipped_chat", 0))
        c4.metric("❌ Erros", r.get("errors", 0))
        if r.get("stopped_reason"):
            st.info(f"ℹ️ {r['stopped_reason']}")
