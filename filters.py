import re


def parse_price(price_str: str) -> float | None:
    """Extrai valor numérico de string de preço ('R$ 1.500' -> 1500.0)."""
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d,.]", "", price_str)
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def filter_by_price(ad: dict, min_price: float | None, max_price: float | None) -> bool:
    """Retorna True se o anúncio passa no filtro de preço."""
    price = parse_price(ad.get("price", ""))
    if price is None:
        return True  # sem preço = não filtra
    if min_price is not None and price < min_price:
        return False
    if max_price is not None and price > max_price:
        return False
    return True


def filter_by_region(ad: dict, allowed_regions: list[str]) -> bool:
    """Retorna True se a região do anúncio está na lista permitida."""
    if not allowed_regions:
        return True
    location = ad.get("location", "").lower()
    return any(region.lower() in location for region in allowed_regions)


def filter_by_blocked_words(ad: dict, blocked_words: list[str]) -> bool:
    """Retorna True se o anúncio NÃO contém palavras bloqueadas."""
    if not blocked_words:
        return True
    title = ad.get("title", "").lower()
    return not any(word.lower() in title for word in blocked_words)


def filter_by_min_photos(ad: dict, min_photos: int) -> bool:
    """Retorna True se o anúncio tem fotos suficientes."""
    if min_photos <= 0:
        return True
    return ad.get("photo_count", 0) >= min_photos


def apply_filters(
    ads: list[dict],
    min_price: float | None = None,
    max_price: float | None = None,
    allowed_regions: list[str] | None = None,
    blocked_words: list[str] | None = None,
    min_photos: int = 0,
) -> list[dict]:
    """Aplica todos os filtros e retorna anúncios que passaram."""
    filtered = []
    for ad in ads:
        if not filter_by_price(ad, min_price, max_price):
            continue
        if not filter_by_region(ad, allowed_regions or []):
            continue
        if not filter_by_blocked_words(ad, blocked_words or []):
            continue
        if not filter_by_min_photos(ad, min_photos):
            continue
        filtered.append(ad)
    return filtered
