# Verification against published figures

This build was reconciled with figures Inside Airbnb publishes itself, from three independent
angles. All numbers below come from the run of **9 October 2026** on six real snapshots:
Amsterdam 2026-06-15 and 2026-09-16, Austin 2026-06-22 and 2026-09-17, Singapore 2026-06-29 and
2026-09-23. Rerun them with `make capture` (live dashboards) or `make verify` (stored captures).

| Check | Source of the published figure | Result |
|---|---|---|
| 1. City dashboard headline figures | [insideairbnb.com/amsterdam](https://insideairbnb.com/amsterdam/), [/austin](https://insideairbnb.com/austin/), [/singapore](https://insideairbnb.com/singapore/) | **45 of 45 exact** (15 figures × 3 cities). The first pass had 43 of 45; see below. |
| 2. Occupancy model vs Inside Airbnb's own per-listing estimate | `estimated_occupancy_l365d` and `estimated_revenue_l365d` in each `listings.csv.gz` | **100% of listings** have exactly the same estimated nights, in all six snapshots. Revenue totals agree to within 2 currency units. |
| 3. Reviews recounted from `reviews.csv.gz` | `number_of_reviews_ltm` in `listings.csv.gz` | 97.3–98.8% of listings exact. Totals are 0.19–0.23% lower. |

## 1. City dashboards

**How it is captured.** The dashboards compute their headline numbers in the browser, so
`airbnb verify --capture` renders each city page with headless Chrome and parses the visible
text. Captures are stored with their URL and time in
[`verification/published_dashboards.json`](verification/published_dashboards.json). The captures
used here were taken on 2026-10-09 between 16:36 and 16:38 UTC. Each dashboard showed the
snapshot this build loaded ("Inside Airbnb data: September 16/17/23, 2026").

**What is compared.** The dashboard opens with its default filter, "only short-term rentals":
minimum nights below the city's threshold (30 nights for all three cities). Missing minimum
nights count as 0 there, so those listings count as short-term. `mart_city_snapshot_summary`
reproduces that filter as `scope = 'dashboard_default'`, and the comparison reads that scope. The
unfiltered total ("out of N listings") is compared with `scope = 'all'`.

| Metric | Amsterdam published | ours | Austin published | ours | Singapore published | ours |
|---|--:|--:|--:|--:|--:|--:|
| total listings | 10,612 | 10,612 | 11,111 | 11,111 | 3,208 | 3,208 |
| short-term listings (default filter) | 10,530 | 10,530 | 9,593 | 9,593 | 1,721 | 1,721 |
| entire homes/apartments | 8,508 | 8,508 | 8,122 | 8,122 | 721 | 721 |
| private rooms | 1,976 | 1,976 | 1,284 | 1,284 | 892 | 892 |
| shared rooms | 22 | 22 | 16 | 16 | 64 | 64 |
| hotel rooms | 24 | 24 | 171 | 171 | 44 | 44 |
| average nights booked (model) | 51 | 51 | 77 | 77 | 36 | 36 |
| average price per night | €197 | 197 | $360 | 360 | S$314 | 314 |
| average income (model) | €11,336 | 11,336 | $22,554 | 22,554 | S$7,287 | 7,287 |
| unlicensed | 466 | 466 | 6,055 | 6,055 | 314 | 314 |
| licensed | 9,463 | 9,463 | 3,012 | 3,012 | 1,061 | 1,061 |
| exempt | 601 | 601 | 526 | 526 | 346 | 346 |
| pending | 0 | 0 | 0 | 0 | 0 | 0 |
| single-listing host listings | 8,581 | 8,581 | 3,523 | 3,523 | 29 | 29 |
| multi-listing host listings | 1,949 | 1,949 | 6,070 | 6,070 | 1,692 | 1,692 |

**The one mismatch, and its fix.** On the first pass, average income was 3 lower than published
for Amsterdam (11,333 vs 11,336) and 5 lower for Austin (22,549 vs 22,554). Singapore matched.
Nights booked matched everywhere, so the difference had to come from price. The dashboards read
`visualisations/listings.csv` rather than the detailed `listings.csv.gz`. In that file the
price is rounded to whole currency units: 4,212 of the 9,593 short-term Austin listings have a
different price there (for example 135.50 becomes 136). With whole-unit prices, both cities
reproduce exactly. The `dash_*` columns now use `round(price)`, to match the dashboard's
convention. Every other price, revenue and ADR figure in the marts still uses the exact price
from `listings.csv.gz`.

**Licence counts are scoped.** The licence figures above cover the short-term filter. Over all
listings, `mart_licence_compliance` reports 9,541 licensed, 466 unlicensed and 605 exempt for
Amsterdam. The classification rule is the dashboard's: empty means unlicensed; text matching
`exempt|bail mobilité|32+ days listing` means exempt; "pending" means pending; anything else
counts as licensed. It does not validate licence numbers.

## 2. The occupancy model against Inside Airbnb's own estimate

The current `listings.csv.gz` files carry Inside Airbnb's per-listing `estimated_occupancy_l365d` and
`estimated_revenue_l365d`. This pipeline does not use them. It computes its own estimate from
the published method (see [data assumptions](https://insideairbnb.com/data-assumptions/)):

* bookings = `number_of_reviews_ltm` ÷ 0.5 (review rate 50%)
* nights per booking = max(`minimum_nights`, the city's average length of stay), with average stay
  3.9 for Amsterdam and 3.0 for Austin and Singapore, read from each dashboard's page data
* nights = min(bookings × nights per booking, 255), the 70% cap, rounded
* revenue = nights × price

| City | Snapshot | Listings | Nights exactly equal | Our nights | Published nights | Our revenue | Published revenue |
|---|---|--:|--:|--:|--:|--:|--:|
| Amsterdam | 2026-06-15 | 10,369 | 100.0% | 500,884 | 500,884 | 116,679,450 | 116,679,450 |
| Amsterdam | 2026-09-16 | 10,612 | 100.0% | 541,512 | 541,512 | 119,814,113 | 119,814,113 |
| Austin | 2026-06-22 | 11,295 | 100.0% | 868,569 | 868,569 | 218,414,690 | 218,414,692 |
| Austin | 2026-09-17 | 11,111 | 100.0% | 857,982 | 857,982 | 225,598,015 | 225,598,016 |
| Singapore | 2026-06-29 | 3,097 | 100.0% | 50,221 | 50,221 | 7,774,942 | 7,774,942 |
| Singapore | 2026-09-23 | 3,208 | 100.0% | 72,133 | 72,133 | 13,346,026 | 13,346,026 |

The Austin revenue gaps of 2 and 1 come from rounding each listing's revenue. The marts DQ gate
`model_tracks_published_occupancy` (warning level) watches this agreement on every run.

Agreeing with Inside Airbnb is not the same as being right about bookings. The model's own
assumptions (review rate, average stay, the cap) are not checked against real booking data,
because none is public. The README's limitations section repeats this.

## 3. Reviews recount

For each city's latest snapshot, reviews dated in the 365 days up to `last_scraped` were counted
in `reviews.csv.gz` and compared with `number_of_reviews_ltm`. Only listings that had at least one
review in the file are compared.

| City | Snapshot | Listings compared | Published reviews (LTM) | Recounted | Listings exact |
|---|---|--:|--:|--:|--:|
| Amsterdam | 2026-09-16 | 9,610 | 102,591 | 102,362 | 97.7% |
| Austin | 2026-09-17 | 9,256 | 138,699 | 138,441 | 97.3% |
| Singapore | 2026-09-23 | 1,839 | 12,601 | 12,572 | 98.8% |

The recount is 0.19–0.23% short. This was not investigated further. Plausible causes are
reviews counted on Airbnb but missing from the scraped review pages, and the exact window
boundary Inside Airbnb uses. The occupancy model uses the published `number_of_reviews_ltm`, as
the dashboard does, so this gap does not affect checks 1 and 2.

## Internal reconciliations (every run)

These run as data-quality gates or dbt tests, not against published figures:

* bronze rows = `mart_city_snapshot_summary` listings per city × snapshot (error gate and dbt test)
* host-portfolio listings and hosts = summary listings and hosts (error gate)
* open SCD2 versions = listings in each city's latest snapshot (error gate)
* SCD2 price changes = a direct comparison of consecutive snapshots (dbt test
  `assert_scd2_matches_snapshot_pairs`)
* rerun leaves every mart unchanged, and a full-refresh SCD2 rebuild equals the incremental
  history (e2e tests, compared by content hash)
