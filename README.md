# Bellhaven Senior Living CRM Reconciliation Pipeline

Modular, zero-dependency Python pipeline that scrapes Bellhaven Senior Living's public directory, reconciles every facility and administrator contact against CRM accounts, enforces Change of Ownership (CHOW) billing rules, and presents all proposed changes in a local approval UI before writing to the CRM API.

## Architecture

- `bellhaven_sync/config.py`: Loads configuration from `.env` or environment variables and stores parent IDs and care-type mappings.
- `bellhaven_sync/scraper.py`: Crawls both `/` (catching homepage-featured announcements) and paginated `/communities?page=N`, then parses every detail page cleanly into `WebsiteCommunity` objects.
- `bellhaven_sync/crm_client.py`: Paginated REST client (`GET`, `POST`, `PATCH`) for `/api/v1/accounts` and `/api/v1/contacts`.
- `bellhaven_sync/normalizer.py`: Street abbreviation, city, zip, phone, name token, and care-offering canonicalization.
- `bellhaven_sync/matcher.py`: Deterministic matcher and classifier handling:
  - Direct field fixes (PO Box street corrections, ZIP fixes, outdated names, phone updates, care types)
  - Direct re-parenting when `lifetime_revenue == 0` or `outstanding_ar == 0`
  - **CHOW SOP split/link** when `lifetime_revenue > 0` and `outstanding_ar > 0`: preserves existing account `parent_id` untouched, creates new Bellhaven child account, and links `chow_current_account` on the old account
  - Duplicate clustering (`status = Inactive`, `duplicate_of_account = survivor_id`) while preserving historical CHOW predecessors
  - New account creation for unmatched website communities
  - False-positive candidate detection (`REVIEW_FALSE_POSITIVE` / `FALSE_POSITIVE_GUARD`) recommending `Reject` on out-of-state homonyms and competitor-parent same-city facilities
  - Offboarded Bellhaven account checks (`OFFBOARDED_CHOW_LINK` when a new owner exists in CRM, or `status = Needs Review` with explanatory audit notes when no successor exists)
  - Administrator contact sync (`create_contact`, `patch_contact`)
- `bellhaven_sync/executor.py`: Executes approved multi-step API operations sequentially, resolving `$CREATED_ACCOUNT_ID` between account creation and CHOW/contact follow-up calls.
- `bellhaven_sync/state_store.py`: SQLite storage (`data/state.db`) keyed by deterministic SHA-256 proposal fingerprints so daily re-runs never re-propose approved or rejected items.
- `bellhaven_sync/review_app.py`: Local HTTP review UI with side-by-side evidence tables, `Approve & Apply to CRM`, `Reject`, and `Run Daily Sync Now`.

## Quick Start

1. Copy `.env.example` to `.env` and set `BELLHAVEN_API_TOKEN`.
2. Run the daily sync pass:

   ```bash
   python run.py --sync
   ```

3. Launch the local review interface:

   ```bash
   python run.py --serve
   ```

   Then open `http://127.0.0.1:8765/`.

4. Run unit tests:

   ```bash
   python -m unittest discover -s tests -v
   ```

## Daily Schedule

- GitHub Actions workflow: `scripts/daily-sync.github-workflow.yml` (`0 6 * * *` UTC)
- Standard cron example: `scripts/cron.example`
