# metalhop

A single-file Python CLI that walks the Metal Archives similar-artist graph.
User types a band name, picks a match, sees its Bandcamp link (or an embedded
player), picks a similar artist, repeats. Personal project, open source.

Entry point: `metalhop.py`. There is no package, no build step, no test suite.

## Hard rules

These are design constraints, not preferences. They exist because the project
sits in a legal grey zone that only stays comfortable if these hold. Do not
relax any of them without an explicit instruction to do so.

1. **Never store scraped data.** No caching layer, no SQLite, no JSON dumps, no
   "just a small local cache to speed things up." Every run queries live and
   forgets. Mirroring Metal Archives' database is the single move that would
   turn this from tolerated into an actual problem (EU sui generis database
   right, and their content is user-contributed under no license to us).
   The one carve-out is `--fest`, which writes lineup files: those are one
   festival's own published bill, taken from the organiser's site, and a bill
   is a fact set of a few hundred rows rather than a database to mirror.
   Nothing from Metal Archives is written to disk, ever, and that half of the
   rule is absolute.
2. **Never proxy or download audio.** Playback happens only via Bandcamp's own
   iframe embed or by opening a Bandcamp page in the user's browser. Extracting
   stream URLs and piping them through our own player would make us the
   infringer, against the labels and artists rather than against Bandcamp.
3. **Never remove the rate limiting.** MA is a small ad-free independent site
   running on donations, and killtowndeathfest.com is a WordPress site run by
   the people who book the festival. The realistic failure mode of this project
   is an IP ban from hammering them, not a lawsuit. Every host gets its own
   budget in `INTERVALS`; a new host means a new entry, not a shared one.
4. **Keep the User-Agent descriptive and the contact real.** `CONTACT` at the
   top of the file must be a working address.
5. **No monetization, no ads, no app-store distribution.** Those are the two
   things that reliably attract complaints. Personal + open source is the whole
   safety margin.
6. **Don't add Bandcamp search or discovery scraping.** Bandcamp's official API
   is sales-reporting only for artists/labels and exposes no public search, so
   anything else means hitting their internal endpoints against their ToS. The
   artist -> release path (two fetches per band) is the maximum acceptable
   Bandcamp contact, and it stays opt-in. Bandcamp URLs must arrive from
   somewhere that already lists them (MA's links tab, a fest band page, a
   hand-written lineup file), never from a search.

## Architecture

Everything lives in `metalhop.py`, deliberately. Six layers:

- **`get(url, host=...)`** - the only way out to the network: per-host rate
  budgets (`INTERVALS`), shared session, 429 handling, and the Cloudflare
  challenge detection that raises `Blocked`. All new requests must go through
  it.
- **MA scrapers** - `search_bands`, `similar_bands`, `bandcamp_links`. Each
  hits one AJAX endpoint and returns plain dicts.
- **Kill-Town scrapers** - `fest_editions` reads the fest's past-editions page
  into `{edition -> bands}`, `fest_band` reads one band page for country and
  Bandcamp URL, `write_lineup` emits a lineup file the `--list` layer below can
  read straight back. This is the only layer that writes anything but an
  output page to disk; see hard rule 1 for why it is allowed to.
- **Bandcamp embed resolver** - `resolve_release` turns an artist root or a
  direct release URL into an embeddable release by reading the `data-tralbum`
  blob. Opt-in, at most two fetches per band.
- **Lineup pages** - `load_list` / `resolve_list` / `build_page` turn a lineup
  file into a standalone HTML page with the resolved album ids baked in, so the
  page needs no runtime and no further scraping to keep working. `--list` takes
  several files at once and shares one in-memory `_resolved` cache across them,
  which matters because the archive repeats bands heavily: 340 slots across the
  14 editions are only ~230 distinct Bandcamp URLs.
- **Restyling** - `--restyle` re-emits every built page from its own baked
  records and rewrites the index, with no requests at all. Reach for it after
  any template edit; rebuilding from the lineup files instead would re-resolve
  the whole archive for a CSS change. It preserves each page's title, and so
  the localStorage key its heard-set lives under.
- **Landing page** - `--index` reads the built pages back (`page_summary`
  parses the `BANDS` blob out of each) and writes `index.html` linking them.
  It makes no requests, so it is safe to rerun after every build; the counts
  come from the pages rather than the lineup files because only a built page
  knows what actually resolved.
- **Local player page** - `--serve` runs a threaded HTTP server on 127.0.0.1: a
  static HTML page plus a `/state.json` endpoint. The CLI calls `publish()` to
  update shared state; the page polls every 2s and re-renders only when `rev`
  changes, so playback isn't interrupted while the user browses.

Navigation is a `stack` of band dicts in `main()`. `b` pops, `s` clears, dead
ends pop automatically. State lives in memory only.

## Endpoints used

All undocumented AJAX endpoints that the MA site itself calls. Expect them to
break without notice; that is the main maintenance burden.

| Purpose | Endpoint | Returns |
|---|---|---|
| Band search | `/search/ajax-band-search/?field=name&query=` | JSON, `aaData` rows of `[<a> html, genre, country]` |
| Similar artists | `/band/ajax-recommendations/id/{id}` | HTML table, rows keyed `recRow_{id}` |
| External links | `/link/ajax-list/type/band/id/{id}` | HTML, filtered for `bandcamp.com` |

Bandcamp embed resolution: artist root + `/music`, take the first `/album/` or
`/track/` href, then read the `data-tralbum` blob on that page. (Not `og:video`
- plenty of real release pages carry none, which made a resolvable band look
identical to a band with no Bandcamp at all.)

killtowndeathfest.com is ordinary WordPress, no API. Two pages carry
everything:

| Purpose | Page | Parse |
|---|---|---|
| Every past edition and its bands | `/past-editions/` | `div.portfolio-box`, whose class list names the editions that band played |
| Country, label, Bandcamp link | `/band/{slug}/` | first short `<p>` above the social-icon row is `Country · Label`; Bandcamp is the `bandcamp.com` href in that row |

The trap on `/past-editions/` is that the last grid on the page is unfiltered
and repeats all ~258 bands the fest has ever booked. Read edition membership
from each tile's classes, never from which heading a grid sits under, or every
band lands in 2010.

## Environment

Target is a **UserLAnd proot Debian session on Android**. Consequences that
have already bitten:

- No Termux API, and `am` is usually unavailable, so nothing can hand a URL to
  the Android browser. `--serve` exists to route around this: proot shares the
  Android network stack, so Chrome can reach `127.0.0.1:8800`. Prefer improving
  `--serve` over adding more opener heuristics.
- Debian 12+ enforces PEP 668, so install deps with
  `apt install python3-requests python3-bs4`, not pip.
- Bind the server to `127.0.0.1` only.

## Working on this

- Keep it one file. If it genuinely outgrows that, split by layer above, but
  the default answer is no. `tools/` is not an exception to this: it holds
  things that are not the CLI, currently only `mkgif.py`, which renders the
  README's tutorial GIF. Those frames are drawn rather than screenshotted
  because headless Firefox cannot rasterise under proot (SWGL fails to map a
  framebuffer), so if a page's design changes the GIF does not follow by
  itself - rerun it. It draws the Bandcamp embed as an empty block on purpose:
  the real tracklist lives inside the iframe, and inventing song titles to
  fill the space would put made-up data in a tutorial.
- The MA parsers were written against MA's markup but have **never been
  verified against live responses**, because MA has been behind a Cloudflare
  challenge throughout. If a listing comes back empty rather than raising,
  suspect the parser first - a markup change looks exactly like a band with
  nothing listed. The Kill-Town parsers, by contrast, were checked against the
  live site: 14 editions, 258 bands.
- Failure modes should degrade, not crash. "No similar artists", "no Bandcamp
  link" and "band page 404s" are normal states, not errors - plenty of bands
  hit one or both.
- Two build warnings mean different things and both are worth reading. `0tr`
  means the resolved release streams nothing (an announcement or a vinyl-only
  page); the fix is to hand-pin a direct `/album/` URL in the lineup file.
  `MISMATCH` means the release is credited to someone else, which is usually a
  label root. `same_artist` folds diacritics first, so `BØLZER` vs `Bolzer` no
  longer cries wolf - if it flags now, look.
- A dead release URL can be trimmed to its artist root and re-resolved: the
  host was already listed by whoever published the link, so that is not
  discovery. Guessing a *different* host to see if it exists is, and rule 6
  forbids it - report the band as unresolved and let a human pin the URL.
- No dependencies beyond `requests` and `beautifulsoup4`.
- Before changing request behaviour, check it against the Hard Rules above.
- This file is the project's memory of *why*. If a rule here stops you from
  doing something, say so rather than quietly routing around it.
