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
2. **Never proxy or download audio.** Playback happens only via Bandcamp's own
   iframe embed or by opening a Bandcamp page in the user's browser. Extracting
   stream URLs and piping them through our own player would make us the
   infringer, against the labels and artists rather than against Bandcamp.
3. **Never remove the rate limiting.** MA is a small ad-free independent site
   running on donations. The realistic failure mode of this project is an IP
   ban from hammering them, not a lawsuit.
4. **Keep the User-Agent descriptive and the contact real.** `CONTACT` at the
   top of the file must be a working address.
5. **No monetization, no ads, no app-store distribution.** Those are the two
   things that reliably attract complaints. Personal + open source is the whole
   safety margin.
6. **Don't add Bandcamp search or discovery scraping.** Bandcamp's official API
   is sales-reporting only for artists/labels and exposes no public search, so
   anything else means hitting their internal endpoints against their ToS. The
   artistâ   path (two fetches per band) is the maximum acceptable Bandcamp contact, and
   it stays opt-in.

## Architecture

Everything lives in `metalhop.py`, deliberately. Four layers:

- **`get(url, host=...)`** â  budgets (`MA_INTERVAL`, `BC_INTERVAL`), shared session, 429 handling. All new
  requests must go through it.
- **MA scrapers** â  hits one AJAX endpoint and returns plain dicts.
- **Bandcamp embed resolver** â- **Local player page** â  static HTML page plus a `/state.json` endpoint. The CLI calls `publish()` to
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

Bandcamp embed resolution: artist root + `/music`, take the first
`/album/` or `/track/` href, then read `og:video` from that page.

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
  the default answer is no.
- The parsers were written against MA's markup but have **never been verified
  against live responses**. If a listing comes back empty rather than raising,
  suspect the parser first â- Failure modes should degrade, not crash. "No similar artists" and "no
  Bandcamp link" are normal states, not errors â  bands hit one or both.
- No dependencies beyond `requests` and `beautifulsoup4`.
- Before changing request behaviour, check it against the Hard Rules above.
