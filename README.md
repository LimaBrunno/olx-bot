# OLX Bot 🤖

Sistema pessoal de automação de negociações na OLX, com interface web local, scraper inteligente e negociador via IA local.

## Módulos

### Módulo 1 — Captação e Envio de Mensagens
- Scraper de anúncios com filtros (preço, região, palavras bloqueadas)
- Envio automático de mensagens via chat OLX
- Anti-bloqueio com delays humanizados e lotes configuráveis
- Interface Streamlit com controles em tempo real

### Módulo 2 — Negociador Automático com IA (em desenvolvimento)
- Monitor de inbox que detecta respostas de vendedores
- Geração de respostas via LLM local (Ollama) — sem custo de API
- Máquina de estados para cada negociação (nova → negociando → aceita/rejeitada)
- Escalação para humano via Telegram quando necessário

## Stack

| Componente | Tecnologia |
|---|---|
| Interface | Streamlit |
| Automação browser | Playwright (Edge) |
| Anti-bloqueio | Delays humanizados + lotes |
| LLM local | Ollama + Llama 3.1 8B |
| Notificações | Bot Telegram |
| Banco de dados | SQLite |

## Estrutura

```
olx-bot/
├── app.py                  # Interface Streamlit
├── scraper.py              # Coleta anúncios
├── messenger.py            # Envia mensagens via chat OLX
├── filters.py              # Filtros de anúncios
├── database.py             # SQLite
├── config.py               # Configurações e defaults
├── chat_extractor.py       # Extrai histórico de chat (Módulo 2)
├── requirements.txt
└── data/                   # Banco de dados e exports (gitignored)
```

## Requisitos

- Python 3.10+
- Microsoft Edge instalado
- Conta OLX ativa (sessão salva no perfil do Playwright)

## Instalação

```bash
pip install -r requirements.txt
playwright install msedge
```

## Uso

```bash
# Interface principal
streamlit run app.py

# Extração de histórico de chat
python chat_extractor.py --max 50
```

---

> Projeto pessoal para uso com conta própria na OLX.
