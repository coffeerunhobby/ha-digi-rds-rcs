# Changelog

All notable changes to this integration are documented here. Versions follow
the integration's `manifest.json` and the GitHub release tags.

## v0.5.0 — Security fix + internal restructuring

> 🔒 **Security — update from v0.4.0.** Downloading diagnostics in v0.4.0
> produced a file containing the Digi account **e-mail address and password in
> clear text**, plus the client code and address list. Diagnostics downloads are
> routinely attached to bug reports, so anyone who did that in v0.4.0 should
> treat those credentials as exposed and **change the account password**. Fixed
> here, with a test that fails if any credential ever reaches the output again.
>
> The redaction had been correct in earlier releases; a cleanup in v0.4.0 removed
> the field names from the redaction set by accident. The new test asserts on the
> *rendered* diagnostics rather than on the list of redacted keys, so the same
> class of regression cannot pass CI again. That test immediately found a second,
> older leak: the config entry title (`Digi — <e-mail>`) was emitted verbatim and
> is now redacted too.

**No functional changes.** Sensors, entity ids, attributes and behaviour are
unchanged. This release is a security fix plus internal work to make that class
of bug harder to reintroduce.

**Under the hood.** The internals were reorganised along the seams that were
already there:

- HTML parsing moved out of the API client into `parser.py`; `api.py` is now
  transport and session handling only (1194 → 646 lines).
- Three near-identical date parsers became one `dates.py`.
- The four platforms share one base entity, so device identity and availability
  are defined once instead of four times.
- The coordinator snapshot is described with `TypedDict`s, so the shape entities
  rely on is checked rather than assumed.
- `parser.py` and `dates.py` carry no Home Assistant imports, which is what let
  the release be verified directly against live account data.

**Verification.** Old and new code were run side by side over the same live page:
29 invoice rows, 2 addresses, invoice detail and 22 helper inputs all byte
identical. New tests boot the integration inside a real Home Assistant and assert
on the entities and states that reach the UI, so a future refactor cannot keep
the unit tests green while breaking setup.

**Full diff:** https://github.com/coffeerunhobby/ha-digi-rds-rcs/compare/v0.4.0...v0.5.0

## v0.4.0 — FiberLink connection insights + native traffic graphs

> ⚠️ **Breaking — entity ids change for addresses without a Digi address-id.**
> Where the numeric address-id could not be scraped, the fallback identifier was
> derived with MD5; it now uses BLAKE2b. Those entity ids therefore change once,
> and any dashboard card, automation or history that referenced them needs
> updating. Addresses with a real Digi address-id (the common case) are
> unaffected. The change removes MD5 from the codebase entirely — it was only
> ever an identifier, never a security measure, but it required a scanner
> suppression and failed outright on FIPS-enabled systems.

**New — overdue as a binary sensor.** Each address also gets
`binary_sensor.digi_<...>_overdue` with device class `problem`. Home Assistant
renders `problem` entities in red when they are on (with `state_color: true`),
which is the native way to make an unpaid balance stand out — an integration
cannot style text itself. The existing `sensor.*_overdue` (`yes`/`no`) is kept
for automations.

**New — `Due date (relative)`.** A second, timestamp-typed due-date sensor
(`sensor.digi_<...>_due_date_timestamp`) that Home Assistant renders as
"in 3 days" / "5 days ago", so how overdue an invoice is reads at a glance.
The original `Due date` keeps Digi's `DD-MM-YYYY` text unchanged, so existing
templates and automations keep working. Both switch to `mdi:calendar-alert`
while there is an unpaid balance.

*No breaking changes in this release.*


**New — FiberLink connection sensors** (per internet address; opt-in, disabled by
default):

- **Connection status** — best-effort online state, with `connected_since`, last
  connect/disconnect, reconnects in the last 30 days, and current IP/MAC exposed
  as attributes.
- **Connection uptime** — a timestamp of when the current session started, shown
  as "connected since … / X days ago".

**New — native download/upload traffic graphs.** Traffic is published as Home
Assistant **long-term statistics** (`digi:connection_download_<id>` and
`digi:connection_upload_<id>`), so you can chart hourly/daily/weekly/**monthly**
TX/RX with the built-in **Statistics Graph** card — no custom cards required.

- History is kept locally for ~6 months and re-imported each poll, so monthly
  graphs span several months without hammering the Digi site.
- A session that spans multiple months is spread proportionally across its days,
  so monthly bars are accurate (not dumped onto the disconnect day).
- The connection is keyed by **address** (one endpoint), never the IP — Digi's
  PPPoE IP changes between sessions.

**Privacy.** New sensors are disabled by default; IP/MAC are redacted from
diagnostics; the FiberLink username is used internally only (never surfaced).

**Compatibility.** Verified across **Python 3.12–3.14** and **Home Assistant
2024.12 → 2026.x**. CI now runs a three-version matrix (min HA / current /
latest).

> Note: Digi only exposes *completed* PPPoE sessions (there is no live "online"
> flag), so the connection status is best-effort — it assumes the line
> auto-reconnected at the last disconnect.

**Under the hood:** persistent connection-session cache, daily-spread traffic
aggregation, `datetime.utcnow()` → timezone-aware, and version-robust statistics
metadata (`has_mean` / `mean_type`).

**Full diff:** https://github.com/coffeerunhobby/ha-digi-rds-rcs/compare/v0.3.1...v0.4.0

## v0.3.1 — Public IP sensor (opt-in)

- Optional **Public IP** sensor for the internet (FiberLink) service, disabled by
  default; IPv6 and plan exposed as attributes.

## v0.3.0 — Real address-ids everywhere + fewer requests

- Use the real Digi numeric address-id in entity ids; request optimisation
  (fetch only current/latest invoice details, cache paid ones).

## v0.2.2 — Real Digi address-id in entity ids

- Entity ids use the scraped Digi address-id (a hash of the address when unavailable).

## v0.2.0

- Per-address device layout; round-robin scheduler so multiple accounts update
  serially; auto-discovery of all addresses.

## v0.1.7 — English sensor keys

- All entity names and states in English; bilingual (ro/en) setup dialogs.
