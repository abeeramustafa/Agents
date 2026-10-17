"""Load the sample catalog and answer questions about it.

Agents are not allowed to invent rows, so this module is the only
place that reads the CSVs. If the files disagree with each other the
process stops at startup instead of serving a half-valid catalog.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.config import settings
from app.scoring import net_margin_pct

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "data"

PRODUCT_COLUMNS = [
    "product_id",
    "name",
    "category",
    "subcategory",
    "market_wholesale_price_usd",
    "retail_price_usd",
    "min_order_qty",
    "monthly_search_index",
    "avg_rating",
    "review_count",
    "competitor_count",
    "trend",
    "return_rate_pct",
    "notes",
]
SUPPLIER_COLUMNS = [
    "supplier_id",
    "name",
    "country",
    "city",
    "region",
    "categories",
    "reliability_score",
    "avg_lead_time_days",
    "payment_terms",
    "certifications",
    "languages",
    "typical_reply_hours",
    "contact_person",
    "contact_email",
    "years_in_business",
    "notes",
]
OFFER_COLUMNS = [
    "offer_id",
    "supplier_id",
    "product_id",
    "unit_price_usd",
    "min_order_qty",
    "lead_time_days",
    "stock_status",
]

PRODUCT_NUMBERS = [
    "market_wholesale_price_usd",
    "retail_price_usd",
    "min_order_qty",
    "monthly_search_index",
    "avg_rating",
    "review_count",
    "competitor_count",
    "return_rate_pct",
]
SUPPLIER_NUMBERS = [
    "reliability_score",
    "avg_lead_time_days",
    "typical_reply_hours",
    "years_in_business",
]
OFFER_NUMBERS = ["unit_price_usd", "min_order_qty", "lead_time_days"]

TRENDS = {"rising", "stable", "declining"}
STOCK_STATUSES = {"in_stock", "made_to_order"}

# List fields are stored as semicolon-separated text in the CSV.
LIST_FIELDS = {
    "suppliers.csv": ("categories", "certifications", "languages"),
}


class CatalogError(RuntimeError):
    """The sample files cannot be used. The message names the file."""


class DataStore:
    def __init__(self, data_dir: Path | None = None):
        folder = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        products = _load_products(folder / "products.csv")
        suppliers = _load_suppliers(folder / "suppliers.csv")
        offers = _load_offers(folder / "offers.csv", products, suppliers)
        self._products = products
        self._suppliers = suppliers
        self._offers = offers
        self._offers_by_product: dict[str, list[dict]] = {}
        for offer in offers:
            self._offers_by_product.setdefault(offer["product_id"], []).append(offer)

    def get_product(self, product_id: str) -> dict | None:
        row = self._products.get(str(product_id).strip())
        return _public_row(row) if row else None

    def get_supplier(self, supplier_id: str) -> dict | None:
        row = self._suppliers.get(str(supplier_id).strip())
        return _public_row(row) if row else None

    def offers_for_product(self, product_id: str) -> list[dict]:
        rows = self._offers_by_product.get(str(product_id).strip(), [])
        return [_public_row(row) for row in rows]

    def catalog_meta(self) -> dict:
        prices = [row["retail_price_usd"] for row in self._products.values()]
        return {
            "categories": _sorted_unique(row["category"] for row in self._products.values()),
            "subcategories": _sorted_unique(
                row["subcategory"] for row in self._products.values()
            ),
            "retail_price_min": min(prices) if prices else None,
            "retail_price_max": max(prices) if prices else None,
            "product_count": len(self._products),
            "supplier_count": len(self._suppliers),
            "offer_count": len(self._offers),
        }

    def filter_products(
        self,
        filters: dict | None = None,
        cost_allowance: float | None = None,
    ) -> list[dict]:
        """Keep products that match every constraint the caller set.

        An empty list or a missing key means "do not constrain that field".
        Price and return bounds are inclusive: a $30 max keeps a $30 product.
        Keywords are a substring match, not a fuzzy match. Typos are handled
        later by the catalog gate.
        """

        filters = filters or {}
        allowance = settings.cost_allowance if cost_allowance is None else cost_allowance
        categories = _lower_set(filters.get("categories"))
        subcategories = _lower_set(filters.get("subcategories"))
        trends = _lower_set(filters.get("trends"))
        keywords = _keyword_list(filters.get("keywords"))
        min_price = filters.get("min_retail_price")
        max_price = filters.get("max_retail_price")
        min_margin = filters.get("min_net_margin_pct")
        max_returns = filters.get("max_return_rate_pct")

        matched: list[tuple[dict, float]] = []
        for row in self._products.values():
            if categories and row["category"].casefold() not in categories:
                continue
            if subcategories and row["subcategory"].casefold() not in subcategories:
                continue
            if trends and row["trend"] not in trends:
                continue
            if min_price is not None and row["retail_price_usd"] < float(min_price):
                continue
            if max_price is not None and row["retail_price_usd"] > float(max_price):
                continue
            if max_returns is not None and row["return_rate_pct"] > float(max_returns):
                continue
            margin = net_margin_pct(
                row["retail_price_usd"],
                row["market_wholesale_price_usd"],
                allowance,
            )
            if min_margin is not None and margin < float(min_margin):
                continue
            if keywords and not _keyword_hit(row, keywords):
                continue
            matched.append((row, margin))

        matched.sort(
            key=lambda item: _sort_key(item[0], item[1], filters.get("sort_priority"))
        )
        return [_public_row(row) for row, _margin in matched]


def load_catalog(data_dir: Path | None = None) -> DataStore:
    return DataStore(data_dir)


def _load_products(path: Path) -> dict[str, dict]:
    frame = _read_table(path, PRODUCT_COLUMNS, PRODUCT_NUMBERS)
    _require_unique(frame, "product_id", path.name)
    _require_enum(frame, "trend", TRENDS, path.name)
    _require_range(frame, "monthly_search_index", 0, 100, path.name)
    if (frame["retail_price_usd"] <= 0).any():
        raise CatalogError(f"{path.name}: retail_price_usd must be greater than 0")
    frame["notes"] = frame["notes"].fillna("").map(lambda value: str(value).strip())
    frame["trend"] = frame["trend"].str.strip().str.lower()
    return _index_rows(frame, "product_id")


def _load_suppliers(path: Path) -> dict[str, dict]:
    frame = _read_table(path, SUPPLIER_COLUMNS, SUPPLIER_NUMBERS)
    _require_unique(frame, "supplier_id", path.name)
    _require_range(frame, "reliability_score", 1, 10, path.name)
    frame["notes"] = frame["notes"].fillna("").map(lambda value: str(value).strip())
    for column in LIST_FIELDS["suppliers.csv"]:
        # Callers compare list membership. The CSV stays the source of the text.
        frame[column] = frame[column].map(_split_list)
    return _index_rows(frame, "supplier_id")


def _load_offers(
    path: Path,
    products: dict[str, dict],
    suppliers: dict[str, dict],
) -> list[dict]:
    frame = _read_table(path, OFFER_COLUMNS, OFFER_NUMBERS)
    _require_unique(frame, "offer_id", path.name)
    _require_enum(frame, "stock_status", STOCK_STATUSES, path.name)
    frame["stock_status"] = frame["stock_status"].str.strip().str.lower()
    unknown_products = sorted(set(frame["product_id"]) - set(products))
    if unknown_products:
        raise CatalogError(
            f"{path.name}: offer references unknown product_id {', '.join(unknown_products)}"
        )
    unknown_suppliers = sorted(set(frame["supplier_id"]) - set(suppliers))
    if unknown_suppliers:
        raise CatalogError(
            f"{path.name}: offer references unknown supplier_id {', '.join(unknown_suppliers)}"
        )
    return frame.to_dict(orient="records")


def _read_table(path: Path, columns: list[str], numbers: list[str]) -> pd.DataFrame:
    if not path.is_file():
        raise CatalogError(f"{path.name}: file not found at {path}")
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.ParserError as exc:
        raise CatalogError(f"{path.name}: could not parse CSV ({exc})") from exc
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise CatalogError(f"{path.name}: missing columns {', '.join(missing)}")
    frame = frame[columns].copy()
    for column in frame.columns:
        frame[column] = frame[column].map(lambda value: str(value).strip())
    required = [column for column in columns if column != "notes"]
    for column in required:
        blank = frame.index[frame[column] == ""].tolist()
        if blank:
            rows = ", ".join(str(index + 2) for index in blank[:8])
            raise CatalogError(f"{path.name}: blank {column} on CSV row {rows}")
    for column in numbers:
        converted = pd.to_numeric(frame[column], errors="coerce")
        bad = frame.index[converted.isna()].tolist()
        if bad:
            rows = ", ".join(str(index + 2) for index in bad[:8])
            raise CatalogError(f"{path.name}: non-numeric {column} on CSV row {rows}")
        frame[column] = converted
    return frame


def _require_unique(frame: pd.DataFrame, column: str, filename: str) -> None:
    duplicated = frame.loc[frame[column].duplicated(), column].tolist()
    if duplicated:
        shown = ", ".join(dict.fromkeys(str(value) for value in duplicated))
        raise CatalogError(f"{filename}: duplicate {column} {shown}")


def _require_enum(frame: pd.DataFrame, column: str, allowed: set[str], filename: str) -> None:
    values = set(frame[column].str.strip().str.lower())
    unknown = sorted(values - allowed)
    if unknown:
        raise CatalogError(
            f"{filename}: {column} has unsupported values {', '.join(unknown)}"
        )


def _require_range(
    frame: pd.DataFrame,
    column: str,
    low: float,
    high: float,
    filename: str,
) -> None:
    outside = frame.index[(frame[column] < low) | (frame[column] > high)].tolist()
    if outside:
        rows = ", ".join(str(index + 2) for index in outside[:8])
        raise CatalogError(
            f"{filename}: {column} outside {low}-{high} on CSV row {rows}"
        )


def _index_rows(frame: pd.DataFrame, id_column: str) -> dict[str, dict]:
    rows = {}
    for record in frame.to_dict(orient="records"):
        rows[record[id_column]] = record
    return rows


def _public_row(row: dict) -> dict:
    # Lists are copied so a caller cannot change the in-memory catalog.
    return {
        key: list(value) if isinstance(value, list) else value
        for key, value in row.items()
    }


def _split_list(value: str) -> list[str]:
    return [part.strip() for part in str(value).split(";") if part.strip()]


def _sorted_unique(values) -> list[str]:
    return sorted(set(values))


def _lower_set(value) -> set[str]:
    if not value:
        return set()
    if isinstance(value, str):
        value = [value]
    return {str(item).strip().casefold() for item in value if str(item).strip()}


def _keyword_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = value.split()
    return [str(item).strip().casefold() for item in value if str(item).strip()]


def _keyword_hit(row: dict, keywords: list[str]) -> bool:
    # OR across keywords: one hit is enough. AND across the other filters.
    haystack = " ".join(
        [row["name"], row["category"], row["subcategory"], row["notes"]]
    ).casefold()
    return any(keyword in haystack for keyword in keywords)


def _sort_key(row: dict, margin: float, sort_priority) -> tuple:
    priority = str(sort_priority or "").strip().casefold()
    # Unknown priorities fall back to the catalog id so a bad label cannot
    # drop rows or crash the request. product_id keeps the order stable.
    if priority == "margin":
        return (-margin, row["product_id"])
    if priority == "price_asc":
        return (row["retail_price_usd"], row["product_id"])
    if priority == "price_desc":
        return (-row["retail_price_usd"], row["product_id"])
    if priority in {"", "demand"}:
        return (-row["monthly_search_index"], row["product_id"])
    return (row["product_id"],)
