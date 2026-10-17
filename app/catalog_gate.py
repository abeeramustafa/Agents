"""Stop the app from inventing products that are not in the CSVs.

Maya proposes filters. This module checks them. A typo can still match a
real subcategory. A product the catalog does not sell comes back as a
message and three closest labels, never as a made-up row.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.data_store import DataStore
from app.schemas import ClosestMatch, GateResult, IntakeOutput, ProductFilters

# earfones vs Earphones scores about 82 on this catalog.
# drones and shoes top out around 67, so they stay out.
MATCH_SCORE = 80

# The catalog says both "earbuds" and "earphones" for the same ask.
# Fuzzy match finds the spelling. The group then includes the other spelling
# so a typo of earphones can still reach True Wireless Earbuds.
WORD_GROUPS = (
    {"earbud", "earbuds", "earphone", "earphones"},
)

SORTS = {"margin", "demand", "price_asc", "price_desc"}
TRENDS = {"rising", "stable", "declining"}

FUTURE_NOTE = "Live web research is a future plug-in. This answer uses the sample catalog only."
FALLBACK_QUESTION = "What kind of product do you want to sell, and about what retail price?"

# Words that describe the catalog or a limit, not a product the catalog might be missing.
GENERIC_WORDS = {
    "electronics",
    "electronic",
    "product",
    "products",
    "item",
    "items",
    "something",
    "anything",
    "under",
    "below",
    "less",
    "than",
    "over",
    "above",
    "about",
    "around",
    "dollar",
    "dollars",
    "retail",
    "price",
    "prices",
    "good",
    "margin",
    "margins",
    "low",
    "high",
    "return",
    "returns",
    "with",
    "want",
    "sell",
    "selling",
    "start",
    "have",
    "idea",
    "what",
    "that",
    "this",
    "from",
    "your",
    "some",
    "cheap",
    "budget",
    "please",
    "looking",
    "need",
    "would",
    "like",
    "help",
    "kind",
}

# Longer keys first. These are not catalog rows; they only steer closest matches.
SYNONYMS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("home appliances", (("category", "Smart Home"), ("subcategory", "Robot Vacuums"))),
    ("home appliance", (("category", "Smart Home"), ("subcategory", "Robot Vacuums"))),
    ("appliances", (("category", "Smart Home"), ("subcategory", "Robot Vacuums"))),
    ("appliance", (("category", "Smart Home"), ("subcategory", "Robot Vacuums"))),
    ("gadgets", (("category", "Computer Accessories"), ("category", "Mobile Accessories"), ("category", "Wearables"))),
    ("gadget", (("category", "Computer Accessories"), ("category", "Mobile Accessories"), ("category", "Wearables"))),
    ("accessories", (("category", "Computer Accessories"), ("category", "Mobile Accessories"))),
    ("accessory", (("category", "Computer Accessories"), ("category", "Mobile Accessories"))),
    ("chargers", (("category", "Power & Charging"),)),
    ("charger", (("category", "Power & Charging"),)),
    ("cameras", (("category", "Cameras & Security"),)),
    ("camera", (("category", "Cameras & Security"),)),
    ("watches", (("subcategory", "Smartwatches"),)),
    ("speakers", (("subcategory", "Bluetooth Speakers"),)),
    ("headphones", (("subcategory", "Headsets & Headphones"),)),
    ("headset", (("subcategory", "Headsets & Headphones"),)),
)


def apply_intake(store: DataStore, intake: IntakeOutput, user_text: str | None = None) -> GateResult:
    source = (user_text or "").strip()
    item = requested_item(intake.requested_item_text, source)
    text = item or source
    cleaned, blocked = _clean_filters(store, intake.filters)
    # "electronics" and "retail" are not product names. Leaving them as keywords
    # would reject every row, because the substring filter would miss.
    cleaned = _drop_generic_keywords(cleaned)
    # The model sometimes drops a price that is already written in the sentence.
    # Code reads that number. It does not invent one.
    cleaned = _fill_obvious_limits(source or text, cleaned)
    question = (intake.clarifying_question or "").strip() or None
    missing = _names_missing_product(store, text)

    # Empty filters match every product. That is not a search result.
    # A vague request becomes one question instead of the whole catalog.
    if _is_open(cleaned) and not blocked and not missing:
        if question or not text:
            return _clarify(question or FALLBACK_QUESTION)
    elif not blocked and not missing:
        products = store.filter_products(cleaned.model_dump())
        if products:
            return _ready(cleaned, products, intake.message)

    if not missing:
        rescued = _rescue(store, text, cleaned)
        if rescued is not None:
            filters, products, label = rescued
            # Code found a real row, so the chat line must not say the item is missing.
            quoted = item or intake.requested_item_text or text
            message = f'I matched "{quoted}" to {label} in the sample catalog.'
            return _ready(filters, products, message, matched_label=label)

    shown = item or "that"
    return GateResult(
        status="out_of_catalog",
        in_catalog=False,
        message=f'I couldn\'t find "{shown}" in the sample catalog.',
        clarifying_question=None,
        filters=cleaned,
        product_ids=[],
        closest_matches=_closest(store, text or shown),
        note=FUTURE_NOTE,
    )


def requested_item(maya_text: str | None, user_text: str | None) -> str:
    """The noun they asked for, not the whole sentence."""

    maya = (maya_text or "").strip()
    user = (user_text or "").strip()
    if maya and not _echoes_sentence(maya, user):
        phrase = _content_phrase(maya)
        if phrase:
            return phrase
    return _content_phrase(user)


def _echoes_sentence(maya: str, user: str) -> bool:
    if not user:
        return False
    if maya.casefold() == user.casefold():
        return True
    return len(maya.split()) > 8


def _content_phrase(text: str) -> str:
    lowered = (text or "").casefold()
    for phrase, _mapped in SYNONYMS:
        if phrase in lowered:
            return phrase
    raw = "".join(char if char.isalnum() or char.isspace() else " " for char in (text or ""))
    words = [word for word in raw.split() if word]
    kept = []
    for word in words:
        key = word.casefold()
        if key in GENERIC_WORDS or key in {"the", "a", "an", "my", "for", "to", "in", "of", "and", "or", "i", "me"}:
            continue
        if word.isdigit():
            continue
        kept.append(word)
    return " ".join(kept)


def _ready(
    filters: ProductFilters,
    products: list[dict],
    message: str,
    matched_label: str | None = None,
) -> GateResult:
    return GateResult(
        status="ready",
        in_catalog=True,
        message=message,
        filters=filters,
        product_ids=[row["product_id"] for row in products],
        matched_label=matched_label,
    )


def _clarify(question: str) -> GateResult:
    return GateResult(
        status="clarify",
        in_catalog=True,
        message=question,
        clarifying_question=question,
        filters=ProductFilters(),
        product_ids=[],
    )


def _clean_filters(store: DataStore, filters: ProductFilters) -> tuple[ProductFilters, bool]:
    """Drop labels the model invented. `blocked` means every value in a field was invented.

    An empty category list normally means "no category constraint". If the
    model sent only fake categories, treating that as "no constraint" would
    return the whole catalog. blocked stops that.
    """

    meta = store.catalog_meta()
    categories, bad_categories = _keep_known(filters.categories, meta["categories"])
    subcategories, bad_subcategories = _keep_known(filters.subcategories, meta["subcategories"])
    trends = [item for item in filters.trends if item.casefold() in TRENDS]
    bad_trends = bool(filters.trends) and not trends
    sort_priority = filters.sort_priority if (filters.sort_priority or "") in SORTS else None
    blocked = bad_categories or bad_subcategories or bad_trends
    cleaned = filters.model_copy(
        update={
            "categories": categories,
            "subcategories": subcategories,
            "trends": trends,
            "sort_priority": sort_priority,
        }
    )
    return cleaned, blocked


def _keep_known(requested: list[str], valid: list[str]) -> tuple[list[str], bool]:
    if not requested:
        return [], False
    lookup = {item.casefold(): item for item in valid}
    kept = []
    for item in requested:
        canonical = lookup.get(item.strip().casefold())
        if canonical and canonical not in kept:
            kept.append(canonical)
    return kept, not kept


def _drop_generic_keywords(filters: ProductFilters) -> ProductFilters:
    kept = [word for word in filters.keywords if word.casefold() not in GENERIC_WORDS]
    if kept == list(filters.keywords):
        return filters
    return filters.model_copy(update={"keywords": kept})


def _fill_obvious_limits(text: str, filters: ProductFilters) -> ProductFilters:
    lowered = text.casefold()
    updates = {}
    if filters.max_retail_price is None:
        match = re.search(
            r"(?:under|below|less than|up to|maximum|max)\s*\$?\s*(\d+(?:\.\d+)?)",
            lowered,
        )
        if match:
            updates["max_retail_price"] = float(match.group(1))
    if filters.min_net_margin_pct is None and "good margin" in lowered:
        updates["min_net_margin_pct"] = 15
    if filters.max_return_rate_pct is None and "low return" in lowered:
        updates["max_return_rate_pct"] = 8
    if not updates:
        return filters
    return filters.model_copy(update=updates)


def _names_missing_product(store: DataStore, text: str) -> bool:
    """True when the sentence names a product word the catalog cannot fuzzy-match.

    "electronics" is not such a word: this catalog is electronics.
    "drones" is. A price limit must not turn drones into every cheap row.
    """

    if _synonym_hits(text):
        # Mapped phrases are not catalog rows. Closest matches handle them.
        return True

    tokens = [
        token
        for token in _query_tokens(text)
        if token not in GENERIC_WORDS and len(token) >= 4
    ]
    # The full sentence is also a token. Ignore it once the words are split out.
    tokens = [token for token in tokens if " " not in token]
    if not tokens:
        return False
    for token in tokens:
        _word, score = _best_word(store, token)
        if score >= MATCH_SCORE:
            return False
    return True


def _is_open(filters: ProductFilters) -> bool:
    return not any(
        (
            filters.categories,
            filters.subcategories,
            filters.keywords,
            filters.trends,
            filters.min_retail_price is not None,
            filters.max_retail_price is not None,
            filters.min_net_margin_pct is not None,
            filters.max_return_rate_pct is not None,
        )
    )


def _rescue(
    store: DataStore,
    text: str,
    cleaned: ProductFilters,
) -> tuple[ProductFilters, list[dict], str] | None:
    word, score = _best_word(store, text)
    if word is None or score < MATCH_SCORE:
        return None
    group = _expand(word)
    subcategories = _subcategories_for_words(store, group)
    if not subcategories:
        return None
    # The typo keyword would fail the substring filter and wipe out the match.
    updated = cleaned.model_copy(update={"subcategories": subcategories, "keywords": []})
    products = store.filter_products(updated.model_dump())
    if not products and updated.categories:
        updated = updated.model_copy(update={"categories": []})
        products = store.filter_products(updated.model_dump())
    if not products:
        return None
    return updated, products, word


def _best_word(store: DataStore, text: str) -> tuple[str | None, float]:
    queries = _query_tokens(text)
    if not queries:
        return None, 0
    best_word = None
    best_score = 0.0
    for word in _catalog_words(store):
        score = max(fuzz.ratio(query, word.casefold()) for query in queries)
        if score > best_score:
            best_score = score
            best_word = word
    return best_word, best_score


def _query_tokens(text: str) -> list[str]:
    # Punctuation is not part of the word. "retail," must stay "retail".
    raw = "".join(char if char.isalnum() or char.isspace() else " " for char in text.casefold())
    raw = " ".join(raw.split())
    tokens = [raw]
    for part in raw.replace("&", " ").replace("-", " ").split():
        if len(part) >= 4 and part not in tokens:
            tokens.append(part)
    return [token for token in tokens if token.strip()]


def _catalog_words(store: DataStore) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for product in store.filter_products({}):
        pieces = [product["category"], product["subcategory"], product["name"]]
        for piece in pieces:
            for word in str(piece).replace("&", " ").replace("-", " ").split():
                if len(word) < 4:
                    continue
                key = word.casefold()
                if key in seen:
                    continue
                seen.add(key)
                words.append(word)
    return words


def _expand(word: str) -> set[str]:
    key = word.casefold()
    for group in WORD_GROUPS:
        if key in group:
            return set(group)
    return {key}


def _subcategories_for_words(store: DataStore, words: set[str]) -> list[str]:
    found: list[str] = []
    for product in store.filter_products({}):
        subcategory = product["subcategory"]
        if subcategory in found:
            continue
        haystack = f"{subcategory} {product['name']}".casefold()
        tokens = set(haystack.replace("&", " ").replace("-", " ").split())
        if tokens & words:
            found.append(subcategory)
    return found


def _synonym_hits(text: str) -> list[tuple[str, str]]:
    query = (text or "").casefold()
    hits = []
    seen = set()
    for phrase, mapped in SYNONYMS:
        if phrase not in query:
            continue
        for kind, label in mapped:
            key = (kind, label)
            if key in seen:
                continue
            seen.add(key)
            hits.append(key)
        break
    return hits


def _closest(store: DataStore, text: str, limit: int = 3) -> list[ClosestMatch]:
    meta = store.catalog_meta()
    labels = [("category", label) for label in meta["categories"]]
    labels += [("subcategory", label) for label in meta["subcategories"]]
    known = {(kind, label) for kind, label in labels}
    ranked: list[ClosestMatch] = []
    seen = set()
    for kind, label in _synonym_hits(text):
        if (kind, label) not in known or label in seen:
            continue
        seen.add(label)
        ranked.append(ClosestMatch(kind=kind, label=label, score=100.0))
    query = text.casefold()
    fuzzy = []
    for kind, label in labels:
        if label in seen:
            continue
        score = float(fuzz.ratio(query, label.casefold()))
        fuzzy.append(ClosestMatch(kind=kind, label=label, score=round(score, 1)))
    fuzzy.sort(key=lambda item: (-item.score, item.label))
    for item in fuzzy:
        if len(ranked) >= limit:
            break
        ranked.append(item)
    return ranked[:limit]
