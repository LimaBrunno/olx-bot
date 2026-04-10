import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Paths
DB_PATH = os.path.join(BASE_DIR, "data", "olx_bot.db")
PROFILE_DIR = os.path.join(BASE_DIR, "profiles", "default")

# OLX
OLX_BASE_URL = "https://www.olx.com.br"

# Delays (segundos)
DEFAULT_MIN_DELAY = 5
DEFAULT_MAX_DELAY = 12

# Lotes
DEFAULT_BATCH_SIZE = 50
DEFAULT_BATCH_PAUSE = 300  # 5 minutos em segundos

# Limites
DEFAULT_DAILY_LIMIT = 300
DEFAULT_MAX_PER_RUN = 200

# Abas paralelas
DEFAULT_PARALLEL_TABS = 1  # 1 = sequencial, 2-9 = abas simultâneas

# Scraper
MAX_PAGES = 10
PAGE_LOAD_TIMEOUT = 60000  # ms
