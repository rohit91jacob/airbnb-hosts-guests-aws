# Data licence and attribution

**Listings, calendar and review data: [Inside Airbnb](https://insideairbnb.com/)**, published
under the [Creative Commons Attribution 4.0 International License (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
The licence statement is on the [Get the Data](https://insideairbnb.com/get-the-data/) page.

Inside Airbnb compiles the data from public Airbnb pages. Inside Airbnb is not associated with
Airbnb, and neither is this project.

## Why no Inside Airbnb data is committed here

CC BY 4.0 would allow redistribution. However, Inside Airbnb's
[Data Policies](https://insideairbnb.com/data-policies/) ask users to:

> Do not republish the data. We think this site provides the best context for the data.

> If you are writing data analysis scripts, download the data once, not each time you run your
> analysis.

This repository follows that request:

| Item | Status |
|---|---|
| Source code, docs, configuration, dbt seeds | MIT ([LICENSE](LICENSE)). The seeds are written for this project: a hand-made English sentiment word list and a short table of city rules with official source links. |
| Inside Airbnb files (`listings.csv.gz`, `calendar.csv.gz`, `reviews.csv.gz`) | **Not committed.** `airbnb ingest` downloads them at runtime into the landing zone, outside the repository. |
| Test fixtures (`tests/fixtures/insideairbnb/`) | **Not committed** (gitignored). `make fixtures` builds a small sample (120 listings per city) from a live download. CI builds it once and caches it until Inside Airbnb publishes a new snapshot. |
| Synthetic unit-test vectors (`tests/conftest.py`) | Invented rows in Inside Airbnb's column layout. They are labelled as synthetic and contain no real data. |
| `docs/verification/published_dashboards.json` | About 20 headline aggregates per city (counts and averages) read from Inside Airbnb's public city dashboards, with capture time and URL. They are used to reconcile this pipeline's output, and they contain no listing-level data. |
| The results site (GitHub Pages) | Aggregates only (city, neighbourhood and month level). It shows no listing names, host names or review text. Hosts in the "largest portfolios" table appear by rank and count only. |

Each refresh downloads only the snapshots it has not landed yet. The landing zone and lake are
cached between runs, so each file is downloaded once.

## Attribution for anything you publish

If you publish tables, charts or dashboards built with this pipeline, credit Inside Airbnb and
the licence. For example:

> Contains data from Inside Airbnb (https://insideairbnb.com/), licensed under CC BY 4.0.

Also consider the project's [community guidelines](https://insideairbnb.com/data-policies/) and
mission. If the data is useful to you, the project accepts
[donations](https://insideairbnb.com/).

## Other sources

| Source | Used for | Terms |
|---|---|---|
| Inside Airbnb city dashboards (`insideairbnb.com/<city>/`) and their `page-data` JSON | Occupancy-model parameters (average stay, thresholds) and reconciliation figures | Same project and terms as above. Only parameters and aggregates are stored. |
| City regulation pages (Amsterdam, Austin, Singapore URA), linked in `dbt/seeds/city_regulation.csv` | Plain-language summaries of a rule and a threshold, with a link | Facts, cited per row. No text is copied beyond a short paraphrase. |

"Airbnb" is a trademark of Airbnb, Inc. This project has no affiliation with Airbnb, Inc. or
with Inside Airbnb.
