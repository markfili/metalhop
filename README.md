# metalhop

A single-file Python CLI for walking the Metal Archives similar-artist graph and
playing what it finds on Bandcamp — plus a build mode that turns a fixed lineup
into a standalone listening page that needs no runtime at all.

Personal project, non-commercial, open source. Live queries only; nothing about
Metal Archives is cached or written to disk. See `CLAUDE.md` for the design
constraints, which are deliberate and not up for casual relaxation.

## The two modes

**Hopping (`metalhop.py`)** — search a band, see its Bandcamp link, pick a
similar artist, repeat. Requires Metal Archives to be reachable.

> Metal Archives currently sits behind a Cloudflare managed challenge and returns
> `403 Cf-Mitigated: challenge` to any plain `requests` client. This mode cannot
> work while that is on; a browser passes the challenge silently, a script can't.

**Lineup pages (`--list` / `--build`)** — resolve a fixed list of bands to
Bandcamp embeds once, and write a self-contained HTML page. Touches Metal
Archives zero times, so it works regardless of the above.

```bash
python3 metalhop.py --list killtown-2026.txt --build index.html
```

Resolution costs at most two Bandcamp fetches per band, rate-limited to one per
second, so ~36 bands takes about a minute. Album IDs are permanent, so the page
keeps working indefinitely without rebuilding.

## The lineup file

`Name | Country | Bandcamp URL`, one per line, `-` for no Bandcamp.

```
Massacre   | United States | https://massacre3.bandcamp.com/album/necrolution
Dead Void  | Denmark       | https://deadvoid.bandcamp.com/
```

An artist root resolves to that artist's **newest** release, which is often a
single or a teaser. A direct `/album/` or `/track/` URL pins that exact release
and costs one fetch instead of two — prefer it. Direct URLs are also what make
label-hosted links safe, since a label root resolves to whatever that label put
out most recently, which is a different band entirely.

The build verifies each resolved release against the band name you asked for and
prints `MISMATCH` when they disagree. It also prints track count, runtime, and
how many tracks Bandcamp won't stream — a release showing `0tr` resolved to an
announcement page and will load an empty player.

## Viewing the built page

It is one self-contained file. Any of these work:

```bash
python3 -m http.server 8800 --bind 127.0.0.1   # then http://127.0.0.1:8800/
```

- straight from `file://`
- from GitHub Pages (see below)

Playback is Bandcamp's own embedded player in an iframe. The page provides
prev/next band and a stop button; play, pause and track selection are Bandcamp's
controls inside the embed. There is no autoplay across bands — the embed is
cross-origin, so nothing here can detect that a record finished.

Bands you've heard are remembered in `localStorage`, per page.

## GitHub Pages

The page is static and self-contained, so Pages needs no build step:

1. Push this repo to GitHub.
2. Settings → Pages → Source: *Deploy from a branch*, branch `main`, folder `/`.
3. It appears at `https://<user>.github.io/<repo>/`.

`.nojekyll` is present so Pages serves the files verbatim. Bandcamp sets no
`X-Frame-Options` and no `frame-ancestors`, so the embeds work from any origin,
and Pages is HTTPS so there's no mixed-content problem.

**Before making the repo public**, set `CONTACT` at the top of `metalhop.py` to a
real address. The User-Agent is meant to identify who is making the requests, and
a placeholder defeats the point.

## Requirements

`requests` and `beautifulsoup4`, nothing else.

```bash
apt install python3-requests python3-bs4    # Debian 12+ enforces PEP 668; don't use pip
```

## What this deliberately does not do

No caching of Metal Archives data, no audio proxying or downloading, no removal
of rate limiting, no Bandcamp search or discovery scraping. The reasoning is in
`CLAUDE.md`.
