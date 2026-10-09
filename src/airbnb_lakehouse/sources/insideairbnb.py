"""Inside Airbnb snapshot discovery.

insideairbnb.com is a Gatsby site. The "Get the data" page is rendered from a static query
whose JSON lists every free snapshot (the last ~12 months, one per city per quarter) with its
publish date and data root. Each city dashboard page has a `pageContext` with the parameters the
dashboard uses for its occupancy model (average length of stay, nights-per-year threshold,
short-term-rental threshold, whether licences are required).

Both are plain public JSON files. If the static-query hash ever changes we look it up again
from the page-data file; if the JSON layout changes entirely we fall back to scraping the
latest snapshot links out of the HTML page.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import requests

from airbnb_lakehouse.config import USER_AGENT

log = logging.getLogger(__name__)

SITE = "https://insideairbnb.com"
FILES = ("listings", "calendar", "reviews")


@dataclass(frozen=True)
class CityParams:
    """Occupancy-model and regulation parameters from the city's dashboard page."""

    city: str
    average_nights_stay: float
    night_per_year_threshold: int | None
    str_threshold: int | None  # None means the dashboard default of 30 nights
    requires_license: bool
    currency_symbol: str
    visualisation_version: str | None
    dashboard_data_date: str | None

    @property
    def effective_str_threshold(self) -> int:
        return self.str_threshold or 30


@dataclass(frozen=True)
class Snapshot:
    city: str  # the URL slug Inside Airbnb uses, e.g. "amsterdam"
    city_name: str
    country: str
    region: str
    snapshot_date: date
    scrape_id: str
    data_root: str  # https://data.insideairbnb.com/<country>/<region>/<city>/

    @property
    def base_url(self) -> str:
        root = self.data_root if self.data_root.endswith("/") else self.data_root + "/"
        return f"{root}{self.snapshot_date.isoformat()}/"

    def file_url(self, name: str) -> str:
        url = f"{self.base_url}data/{name}.csv.gz"
        # Some roots contain non-ASCII characters (e.g. "kantō"); keep ':' and '/' intact.
        return urllib.parse.quote(url, safe=":/")

    def visualisation_url(self, name: str = "listings.csv") -> str:
        return urllib.parse.quote(f"{self.base_url}visualisations/{name}", safe=":/")

    def to_json(self) -> dict:
        d = asdict(self)
        d["snapshot_date"] = self.snapshot_date.isoformat()
        return d

    @classmethod
    def from_json(cls, d: dict) -> Snapshot:
        return cls(**{**d, "snapshot_date": date.fromisoformat(d["snapshot_date"])})


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    adapter = requests.adapters.HTTPAdapter(
        max_retries=requests.adapters.Retry(
            total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504)
        )
    )
    s.mount("https://", adapter)
    return s


def _get_json(session: requests.Session, url: str, timeout: float) -> dict:
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def parse_catalog(payload: dict) -> list[Snapshot]:
    """Parse the `allData.datasets` static query into Snapshot records."""
    datasets = payload["data"]["allData"]["datasets"]
    out = []
    for d in datasets:
        if not d.get("dataRoot") or not d.get("publishDate"):
            continue
        out.append(
            Snapshot(
                city=d["link"],
                city_name=d["city"],
                country=d.get("country", ""),
                region=d.get("region", ""),
                snapshot_date=date.fromisoformat(d["publishDate"]),
                scrape_id=str(d.get("scrapeID", "")),
                data_root=d["dataRoot"],
            )
        )
    return out


_LINK_RE = re.compile(
    r"https://data\.insideairbnb\.com/([^\"]+?)/(\d{4}-\d{2}-\d{2})/data/listings\.csv\.gz"
)


def parse_html_links(html: str) -> list[Snapshot]:
    """Fallback: latest snapshot per city from the links on the Get the data page."""
    out = []
    for m in _LINK_RE.finditer(html):
        path, day = m.group(1), m.group(2)
        parts = path.split("/")
        out.append(
            Snapshot(
                city=parts[-1],
                city_name=parts[-1].replace("-", " ").title(),
                country=parts[0],
                region=parts[1] if len(parts) > 2 else "",
                snapshot_date=date.fromisoformat(day),
                scrape_id="",
                data_root=f"https://data.insideairbnb.com/{path}/",
            )
        )
    return out


def fetch_catalog(timeout: float = 60.0) -> list[Snapshot]:
    """All free snapshots currently listed by Inside Airbnb."""
    session = _session()
    try:
        page = _get_json(session, f"{SITE}/page-data/get-the-data/page-data.json", timeout)
        for h in page.get("staticQueryHashes", []):
            sq = _get_json(session, f"{SITE}/page-data/sq/d/{h}.json", timeout)
            if "allData" in sq.get("data", {}):
                snaps = parse_catalog(sq)
                log.info("catalog: %d snapshots from static query %s", len(snaps), h)
                return snaps
        log.warning("catalog: no static query with allData; falling back to HTML links")
    except (requests.RequestException, KeyError, ValueError) as exc:
        log.warning("catalog: JSON discovery failed (%s); falling back to HTML links", exc)
    r = session.get(f"{SITE}/get-the-data/", timeout=timeout)
    r.raise_for_status()
    return parse_html_links(r.text)


def parse_city_params(city: str, payload: dict) -> CityParams:
    ctx = payload["result"]["pageContext"]

    def _int(v):
        return int(v) if v not in (None, "") else None

    return CityParams(
        city=city,
        average_nights_stay=float(ctx.get("averageNightsStay") or 3),
        night_per_year_threshold=_int(ctx.get("nightPerYearThreshold")),
        str_threshold=_int(ctx.get("strThreshold")),
        requires_license=bool(ctx.get("requiresLicense")),
        currency_symbol=ctx.get("currencySymbol") or "",
        visualisation_version=ctx.get("visualisationVersion"),
        dashboard_data_date=ctx.get("dataDate"),
    )


def fetch_city_params(city: str, timeout: float = 60.0) -> CityParams:
    payload = _get_json(_session(), f"{SITE}/page-data/{city}/page-data.json", timeout)
    return parse_city_params(city, payload)


def select_snapshots(
    catalog: list[Snapshot], cities: tuple[str, ...], per_city: int
) -> list[Snapshot]:
    """The latest `per_city` snapshots for each requested city, oldest first."""
    chosen: list[Snapshot] = []
    known = {s.city for s in catalog}
    for city in cities:
        if city not in known:
            raise ValueError(f"city {city!r} is not in the Inside Airbnb catalog")
        snaps = sorted(
            {s.snapshot_date: s for s in catalog if s.city == city}.values(),
            key=lambda s: s.snapshot_date,
        )
        chosen.extend(snaps[-per_city:])
    return sorted(chosen, key=lambda s: (s.snapshot_date, s.city))


# --- local fixture source ----------------------------------------------------------------


def fixture_catalog(fixtures_dir: Path) -> tuple[list[Snapshot], dict[str, CityParams]]:
    """Read a fixture directory written by `airbnb fixtures`."""
    meta = json.loads((fixtures_dir / "catalog.json").read_text(encoding="utf-8"))
    snaps = [Snapshot.from_json(s) for s in meta["snapshots"]]
    params = {c: CityParams(**p) for c, p in meta["city_params"].items()}
    return snaps, params


def fixture_file(fixtures_dir: Path, snap: Snapshot, name: str) -> Path:
    return fixtures_dir / snap.city / snap.snapshot_date.isoformat() / f"{name}.csv.gz"
