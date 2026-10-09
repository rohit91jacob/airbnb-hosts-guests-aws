from datetime import date

import pytest

from airbnb_lakehouse.sources import insideairbnb as iab


def _dataset(link, day, root, city=None):
    return {
        "link": link,
        "city": city or link.title(),
        "country": "The Netherlands",
        "region": "North Holland",
        "publishDate": day,
        "scrapeID": "20260916",
        "dataRoot": root,
    }


CATALOG = {
    "data": {
        "allData": {
            "datasets": [
                _dataset(
                    "amsterdam", "2026-03-10", "https://data.insideairbnb.com/nl/nh/amsterdam/"
                ),
                _dataset(
                    "amsterdam", "2026-06-15", "https://data.insideairbnb.com/nl/nh/amsterdam/"
                ),
                _dataset(
                    "amsterdam", "2026-09-16", "https://data.insideairbnb.com/nl/nh/amsterdam/"
                ),
                _dataset("tokyo", "2026-09-30", "https://data.insideairbnb.com/japan/kantō/tokyo"),
                {"link": "broken", "city": "Broken"},  # no dataRoot/publishDate: ignored
            ]
        }
    }
}


def test_parse_catalog_skips_incomplete_rows():
    snaps = iab.parse_catalog(CATALOG)
    assert [s.city for s in snaps] == ["amsterdam"] * 3 + ["tokyo"]
    assert snaps[0].snapshot_date == date(2026, 3, 10)


def test_file_url_quotes_non_ascii_but_keeps_scheme():
    tokyo = iab.parse_catalog(CATALOG)[-1]
    url = tokyo.file_url("listings")
    assert url.startswith("https://data.insideairbnb.com/japan/kant%C5%8D/tokyo/2026-09-30/data/")
    assert url.endswith("listings.csv.gz")


def test_select_latest_n_per_city_oldest_first():
    snaps = iab.parse_catalog(CATALOG)
    chosen = iab.select_snapshots(snaps, ("tokyo", "amsterdam"), 2)
    assert [(s.city, s.snapshot_date.isoformat()) for s in chosen] == [
        ("amsterdam", "2026-06-15"),
        ("amsterdam", "2026-09-16"),
        ("tokyo", "2026-09-30"),
    ]


def test_select_unknown_city_fails_loudly():
    with pytest.raises(ValueError, match="atlantis"):
        iab.select_snapshots(iab.parse_catalog(CATALOG), ("atlantis",), 2)


def test_snapshot_json_round_trip():
    s = iab.parse_catalog(CATALOG)[0]
    assert iab.Snapshot.from_json(s.to_json()) == s


def test_html_fallback_parser():
    html = (
        '<a href="https://data.insideairbnb.com/the-netherlands/north-holland/amsterdam/'
        '2026-09-16/data/listings.csv.gz">x</a>'
    )
    (s,) = iab.parse_html_links(html)
    assert (s.city, s.country, s.region) == ("amsterdam", "the-netherlands", "north-holland")
    assert s.base_url.endswith("/amsterdam/2026-09-16/")


def test_city_params_defaults():
    p = iab.parse_city_params(
        "austin",
        {
            "result": {
                "pageContext": {
                    "averageNightsStay": None,
                    "strThreshold": "",
                    "requiresLicense": True,
                }
            }
        },
    )
    assert p.average_nights_stay == 3
    assert p.effective_str_threshold == 30
    assert p.requires_license is True
