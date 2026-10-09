# Data dictionary

Layers, in pipeline order: **landing** (raw files, S3 layout) → **bronze** (typed Apache
Iceberg tables) → **staging / intermediate** (dbt views and tables) → **snapshots** (SCD2) →
**marts**, plus **ops** (pipeline state).

Conventions:

* `city` is Inside Airbnb's URL slug (`amsterdam`, `austin`, `singapore`). `snapshot_date` is the
  snapshot's publish date. Together they identify a snapshot.
* `listing_key` = `city:listing_id` and `host_key` = `city:host_id`. A host id can appear in
  several cities, so portfolios are counted per city.
* Money is in the city's **local currency** (EUR, USD, SGD; see `currency_symbol`). It is never
  converted or summed across cities.
* `*_share` columns are fractions in [0, 1]. `est_*` columns are **model estimates**, not
  observed bookings. `iab_*` columns are Inside Airbnb's own published estimates, kept for
  reconciliation only.

## Landing (S3 layout; local runs use the same keys on disk)

| Key | Content |
|---|---|
| `landing/insideairbnb/city=<c>/snapshot_date=<d>/{listings,calendar,reviews}.csv.gz` | Files exactly as published (byte-for-byte) |
| `landing/insideairbnb/city=<c>/snapshot_date=<d>/_manifest.json` | URL, ETag, Last-Modified, bytes, uncompressed bytes and SHA-256 per file. Written last: a snapshot counts as landed only once its manifest exists. |
| `landing/insideairbnb/_meta/city_params/city=<c>.json` | Dashboard model parameters for the city, with capture time |
| `state/plan.json` | The run plan: snapshots selected, city parameters, fingerprint |

## Bronze (Iceberg; namespace `bronze`, Glue database `<prefix>_bronze` on AWS)

| Table | Grain | Partitioning / write | Notes |
|---|---|---|---|
| `listings` | listing × snapshot | `city`, `snapshot_date`; partition overwrite | 64 declared columns, cast with `TRY_CAST`. `price_raw` stays text ("$1,234.00") and is parsed in staging. Source `id` → `listing_id`, `neighbourhood_cleansed` → `neighbourhood`, `estimated_*_l365d` → `iab_estimated_*`. |
| `calendar` | listing × night × snapshot | `city`, `snapshot_date` | `available` is boolean. It is false for nights that are booked *or* blocked by the host. |
| `reviews` | review × listing | `city`; overwritten from the newest snapshot | Each reviews file is cumulative, so only the newest is kept. Reviewer names are never loaded. |
| `snapshots` | snapshot | `city`, `snapshot_date` | Model parameters (`average_nights_stay`, `night_per_year_threshold`, `str_threshold`, `requires_license`, `currency_symbol`) and the three source SHA-256 hashes |
| `load_audit` | snapshot × table | overwrite per key | `rows`, `source_sha256` (drives skip-if-unchanged), `cast_failures` (JSON column → count), `missing_columns`, `extra_columns` (schema drift), `loaded_at` |

## Staging (dbt views, schema `staging`)

| Model | Grain | What it adds |
|---|---|---|
| `stg_iab__snapshots` | snapshot | `str_threshold_nights` (default 30), `snapshot_seq`, `previous_snapshot_date`, `is_latest_snapshot` |
| `stg_iab__listings` | listing × snapshot | `listing_key`, `host_key`, parsed `price`, `licence_text`, and `licence_status` (`licensed` / `unlicensed` / `exempt` / `pending`, using the dashboard's rule) |
| `stg_iab__calendar` | listing × night × snapshot | `listing_key` |
| `stg_iab__reviews` | review × listing | cleaned `comments` (HTML line breaks removed), `source_snapshot_date` |

## Intermediate (dbt tables, schema `intermediate`)

| Model | Grain | Key columns |
|---|---|---|
| `int_listing_snapshots` | listing × snapshot | The occupancy model: `est_bookings_ltm` = round(`number_of_reviews_ltm` / 0.5); `est_nights_per_booking` = max(`minimum_nights`, `average_nights_stay`); `est_nights_ltm` = round(min(bookings × nights per booking, 255)); `est_occupancy_rate` = nights / 365; `est_revenue_ltm` = round(nights × price). Flags: `is_short_term` (minimum nights < threshold), `is_multi_listing_host` (`calculated_host_listings_count` > 1), `is_highly_available` (`availability_365` > 90), `is_recently_reviewed` (last review within 6 months), `is_frequently_booked` (est. nights > `night_per_year_threshold`). Also `reviews_ltm_recount`, a cross-check. |
| `int_calendar_availability` | listing × snapshot | `available_nights_{30,90,365}` and `unavailable_nights_{30,90,365}`, counted from the first calendar night |
| `int_hosts` | host × snapshot | `listings`, room-type counts, `is_superhost`, `portfolio_tier` (`1`, `2-4`, `5-9`, `10+`) |
| `int_review_sentiment` | review | `positive_hits`, `negative_hits`, `sentiment_label` (`positive` / `negative` / `mixed` / `no_signal`), `sentiment_score` in [-1, 1]. **Simple English lexicon**, no negation handling. |

## Snapshots (SCD Type 2, schema `snapshots`)

**`snap_listing_attributes`**: one row per version of a listing.

| Column | Meaning |
|---|---|
| `listing_key` | Business key |
| tracked: `price`, `room_type`, `property_type`, `accommodates`, `minimum_nights`, `maximum_nights`, `licence_text`, `host_id`, `host_is_superhost`, `instant_bookable`, `neighbourhood` | A change in any of these opens a new version |
| carried, not tracked: `bedrooms`, `beds` | Their scrape coverage varies between snapshots, so tracking them would create versions for fill-ins |
| `observed_in_snapshot` | Snapshot the version's values were read from |
| `dbt_valid_from` / `dbt_valid_to` | Snapshot date the version was first seen / superseded or delisted. Null `dbt_valid_to` = current. These are **Inside Airbnb snapshot dates** (replay clock), not pipeline run times. |
| `dbt_scd_id`, `dbt_updated_at` | dbt bookkeeping |

A listing missing from a later snapshot is closed (`hard_deletes = invalidate`). If it reappears,
it gets a new version.

## Marts (dbt tables, schema `marts`)

### `fct_listing_snapshot` (listing × snapshot)
The base fact. It holds every attribute, flag and model estimate of `int_listing_snapshots`, plus
calendar availability (`calendar_nights`, `available_nights_*`, `unavailable_nights_*`,
`unavailable_share_30` = unavailable share of the next 30 nights) and the `iab_estimated_*`
columns.

### `dim_listing` (listing)
The latest version of each listing ever seen: `last_price`, `first_seen`, `delisted_on`,
`is_active`, `versions`, `price_changes`.

### `dim_city_snapshot` (snapshot)
The snapshot calendar with the model parameters each snapshot was modelled with.

### `mart_city_snapshot_summary` (city × snapshot × scope)
`scope`: `all` (every listing), `short_term` (minimum nights known and below the threshold), or
`dashboard_default` (the Inside Airbnb dashboard's default filter, where an empty minimum nights
counts as 0).
Counts: `listings`, `hosts`, room-type counts, `priced_listings`, `short_term_listings`,
`multi_host_listings` / `single_host_listings`, `highly_available_listings`, and licence-status
counts. Prices: `mean_price`, `median_price`. Model: `mean_est_nights_ltm`,
`mean_est_occupancy_rate`, `total_est_revenue_ltm`, and `est_adr` (revenue-weighted ADR).
Shares: `entire_home_share`, `private_room_share`, `short_term_share`,
`multi_host_listing_share`, `highly_available_share`, `unlicensed_share`, `priced_share`.
`dash_avg_nights_booked`, `dash_avg_price` and `dash_avg_income` follow the dashboard's display
convention: whole numbers, empty values as 0, and price rounded to whole units.

### `mart_host_portfolio` (city × snapshot)
`hosts`, `listings`, `single_listing_hosts`, `multi_listing_hosts`, `hosts_10_plus`,
`listings_of_multi_listing_hosts`, `listings_of_10_plus_hosts`, `superhosts`,
`listings_of_superhosts`, `largest_portfolio`, the share versions of each, and churn against the
previous snapshot of the city (`new_hosts`, `departed_hosts`). "Multi-listing" here means two or
more listings *in this snapshot*. The dashboard's single/multi split uses Inside Airbnb's
`calculated_host_listings_count` instead (`multi_host_listings` in the summary mart).

### `mart_top_hosts` (city × snapshot × rank)
The top 20 hosts by listing count. Holds ids and counts only; host names are not carried into
marts.

### `mart_occupancy_availability` (city × snapshot × room type, plus `All`)
Calendar lens: `mean_available_nights_{30,90,365}`, `mean_unavailable_share_30`,
`highly_available_share`, `fully_blocked_share`. Model lens: `mean_est_occupancy_rate`,
`median_est_occupancy_rate`, `mean_est_nights_ltm`, `frequently_booked_share`,
`recently_reviewed_share`.

### `mart_revenue_estimates` (city × snapshot × room type, plus `All`)
`priced_listings`, `mean_listed_price`, `median_listed_price`, `est_booked_nights_ltm`,
`est_revenue_ltm`, `est_adr` (= est. revenue / est. booked nights of priced listings),
`mean_est_revenue_per_priced_listing`, `median_est_revenue_per_priced_listing`, `est_revpal`
(revenue per priced listing per calendar night), and the published `iab_*` totals.

### `mart_price_change_history` (listing × SCD2 version)
`version_number`, `valid_from`, `valid_to`, `is_current`, `delisted_at_valid_to`, `price`,
`previous_price`, `price_change`, `price_change_pct`, the boolean `*_changed` flags, and
`change_type` (`first_seen`, `price_added`, `price_removed`, `price_up`, `price_down`,
`attributes_only`).

### `mart_price_change_summary` (city × snapshot, from the 2nd snapshot on)
Changes since the previous snapshot: `new_listings`, `delisted_listings`, `price_increases`,
`price_decreases`, `prices_added`, `prices_removed`, `attribute_only_changes`, the minimum-nights,
licence and room-type change counts, `median_price_change_pct` / `mean_price_change_pct` (changed
listings only), `listings_in_both`, `priced_in_both`, and `price_changed_share`. `price` is a quote
for one specific upcoming stay, so it moves with stay dates (see the README limitations).

### `mart_review_velocity` (city × review month)
`reviews`, `listings_reviewed`, `distinct_reviewers`, `reviews_per_reviewed_listing`,
`reviews_same_month_last_year`, `reviews_yoy_change`, and `is_partial_month` (the scrape month).
Built from the newest reviews file, so older months only count listings still listed.

### `mart_guest_sentiment` (city × review month)
`reviews`, `reviews_with_signal`, `lexicon_coverage`, `positive_share` / `negative_share` /
`mixed_share` (of reviews with a signal), `mean_sentiment_score`, and `method` (a constant that
labels the method). **Simple English lexicon. A trend indicator, not a measurement.**

### `mart_neighbourhood_supply` (city × snapshot × neighbourhood)
`listings`, `hosts`, `entire_homes`, `short_term_listings`, `multi_host_listings`,
`highly_available_listings`, `median_price`, `mean_est_occupancy_rate`, `est_revenue_ltm`,
`share_of_city_listings`, the share versions, and change against the previous snapshot
(`previous_listings`, `listings_change`, `listings_change_pct`). Neighbourhoods are Inside
Airbnb's cleansed ones (the listing's coordinates matched to the city's boundaries); Austin's
are ZIP codes.

### `mart_licence_compliance` (city × snapshot × rule)
Licence-status counts (`licensed`, `unlicensed`, `exempt`, `pending`, `unlicensed_short_term`,
`unlicensed_share`) and, for each rule in `seeds/city_regulation.csv`: `rule_id`, `rule_type`,
`threshold`, `rule_description`, `rule_source_url`, `listings_in_scope`, `listings_flagged`,
`flagged_share`. **Indicative flags, not legal findings.**

| Rule | City | Flags |
|---|---|---|
| `ams_night_cap` | Amsterdam | entire homes with est. nights in the last 12 months > 30 |
| `ams_registration` | Amsterdam | short-term listings with no registration number |
| `aus_operating_licence` | Austin | short-term listings with no operating licence number |
| `sgp_three_month_rule` | Singapore | non-hotel listings with minimum nights < 92 (stays under three months), excluding licence text marked exempt, serviced apartment or hotel |

## Seeds (schema `reference`)

| Seed | Content |
|---|---|
| `sentiment_lexicon` | 138 English words (72 positive, 66 negative), hand-written for this project |
| `city_regulation` | One row per rule above, with the official source URL and a note |

## Ops

| Table / file | Content |
|---|---|
| `ops.dq_results` (DuckDB) | One row per check per run: `run_id`, `layer`, `name`, `severity`, `violations`, `passed`, `detail`, `checked_at` |
| `ops.scd2_rounds` (DuckDB) | Snapshot dates already applied to the SCD2 history |
| `state/dq_<layer>.json` | Latest gate result per layer (read by the site) |
| `state/verification.json` | Latest reconciliation with published figures |
