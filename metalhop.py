#!/usr/bin/env python3
"""
metalhop - hop through the Metal Archives similar-artist graph, play on Bandcamp.
Built for UserLAnd / proot Debian on Android.

Live queries only. Nothing is cached or written to disk.

Install (Debian session):
    apt update && apt install -y python3 python3-requests python3-bs4

Usage:
    python3 metalhop.py                    # terminal only, prints URLs
    python3 metalhop.py --serve            # local page at http://127.0.0.1:8800
    python3 metalhop.py --embed bathory    # local page with real Bandcamp embed

Why --serve: proot has no Android intent bridge, so nothing here can reliably
hand a URL to your phone's browser. Instead the script serves a tiny page on
localhost; open it once in Chrome and it follows along as you navigate in the
terminal.
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing deps. Run: apt install python3-requests python3-bs4")

BASE = "https://www.metal-archives.com"
CONTACT = "you@example.com"          # <- put a real contact here
UA = f"metalhop/0.2 (personal, non-commercial; {CONTACT})"
MA_INTERVAL = 1.5                    # seconds between MA requests
BC_INTERVAL = 1.0                    # seconds between Bandcamp requests

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept": "*/*"})

_last = {"ma": 0.0, "bc": 0.0}
_lock = threading.Lock()
CURRENT = {"band": None, "url": None, "embed": None, "meta": "", "rev": 0}


# ---------- plumbing ----------

class Blocked(RuntimeError):
    """The host refused us in a way retrying won't fix (Cloudflare, rate cap)."""


def get(url, host="ma", **kwargs):
    """Rate-limited GET, per-host budget."""
    interval = MA_INTERVAL if host == "ma" else BC_INTERVAL
    wait = interval - (time.monotonic() - _last[host])
    if wait > 0:
        time.sleep(wait)
    headers = {"X-Requested-With": "XMLHttpRequest"} if host == "ma" else {}
    resp = session.get(url, timeout=20, headers=headers, **kwargs)
    _last[host] = time.monotonic()
    if resp.status_code == 429:
        raise Blocked("Rate limited (HTTP 429). Slow down or come back later.")
    # Metal Archives now sits behind a Cloudflare bot challenge. A client using
    # only `requests` can't solve the JS challenge, so this is fatal, not a
    # transient error to retry against (retrying just hammers them).
    if resp.status_code == 403 and (
            resp.headers.get("cf-mitigated") == "challenge"
            or resp.headers.get("Server", "").lower() == "cloudflare"):
        raise Blocked(
            "Metal Archives is behind a Cloudflare challenge (HTTP 403); a "
            "script using only `requests` can't pass it. Open "
            f"{BASE} in a browser to check it's up, then try again later. "
            "This is MA's protection kicking in, not a bug in metalhop.")
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp


def clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def open_url(url):
    """Best effort under proot. Returns True if something was launched."""
    for cmd in (os.environ.get("BROWSER"), "xdg-open",
                "sensible-browser", "x-www-browser"):
        if cmd and shutil.which(cmd):
            subprocess.Popen([cmd, url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
    # Long shot: some proot setups expose the Android activity manager.
    for am in ("/system/bin/am", "am"):
        if os.path.exists(am) or shutil.which(am):
            try:
                subprocess.run([am, "start", "-a", "android.intent.action.VIEW",
                                "-d", url], timeout=5, check=True,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                return True
            except Exception:
                pass
    return False


# ---------- metal archives ----------

def search_bands(query):
    resp = get(f"{BASE}/search/ajax-band-search/",
               params={"field": "name", "query": query})
    out = []
    for row in resp.json().get("aaData", []):
        m = re.search(r'/bands/[^/"]+/(\d+)"[^>]*>(.*?)</a>', row[0])
        if not m:
            continue
        out.append({
            "id": m.group(1),
            "name": clean(m.group(2)),
            "genre": clean(row[1]) if len(row) > 1 else "",
            "country": clean(row[2]) if len(row) > 2 else "",
        })
    return out


def similar_bands(band_id):
    resp = get(f"{BASE}/band/ajax-recommendations/id/{band_id}")
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for tr in soup.find_all("tr"):
        if not str(tr.get("id", "")).startswith("recRow_"):
            continue
        a = tr.find("a", href=re.compile(r"/bands/"))
        m = re.search(r"/bands/[^/]+/(\d+)", a["href"]) if a else None
        if not m:
            continue
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        out.append({
            "id": m.group(1),
            "name": a.get_text(strip=True),
            "country": cells[1] if len(cells) > 1 else "",
            "genre": cells[2] if len(cells) > 2 else "",
            "score": cells[3] if len(cells) > 3 else "",
        })
    return out


def bandcamp_links(band_id):
    resp = get(f"{BASE}/link/ajax-list/type/band/id/{band_id}")
    soup = BeautifulSoup(resp.text, "html.parser")
    found, seen = [], set()
    for a in soup.find_all("a", href=True):
        if "bandcamp.com" in a["href"] and a["href"] not in seen:
            seen.add(a["href"])
            found.append(a["href"])
    return found


# ---------- bandcamp embed (opt-in) ----------

def parse_release(page_text):
    """Pull the release identity out of a Bandcamp album/track page.

    Reads the data-tralbum blob rather than the og:video meta tag: plenty of
    real release pages carry no og:video at all, and on those the old code
    returned None, which the caller could not tell apart from "no Bandcamp
    link". data-tralbum is HTML-escaped JSON, so it needs unescaping first.
    """
    m = re.search(r'data-tralbum="([^"]+)"', page_text)
    if not m:
        return None
    try:
        d = json.loads(html.unescape(m.group(1)))
    except ValueError:
        return None
    cur = d.get("current") or {}
    # item_type is the full word ("album"/"track"); tolerate the abbreviation.
    kind = {"a": "album", "t": "track"}.get(d.get("item_type"), d.get("item_type"))
    ident = cur.get("id") or d.get("id")
    if kind not in ("album", "track") or not ident:
        return None
    # trackinfo also carries file['mp3-128'], a direct stream URL. We only ever
    # read what is playable and how long it is; serving that audio ourselves is
    # the one thing hard rule 2 exists to prevent.
    tracks = d.get("trackinfo") or []
    playable = sum(1 for t in tracks if t.get("file"))
    seconds = sum(t.get("duration") or 0 for t in tracks)
    return {
        "kind": kind,
        "id": ident,
        "title": cur.get("title"),
        "artist": d.get("artist") or cur.get("artist"),
        "url": d.get("url"),
        "tracks": len(tracks),
        "playable": playable,
        "minutes": round(seconds / 60) if seconds else 0,
        # tracklist=true lets Bandcamp render and gate the songs itself, which
        # is the only correct source of truth for what will actually play.
        "embed": (f"https://bandcamp.com/EmbeddedPlayer/v=2/{kind}={ident}"
                  f"/size=large/tracklist=true/artwork=small/"),
    }


def resolve_release(url):
    """Resolve an artist root OR a direct release URL to a playable release.

    Direct /album/ and /track/ URLs cost one fetch and pin that exact release,
    which is what makes label-hosted links usable: a label's root would
    otherwise resolve to whichever band the label released most recently.
    """
    if re.search(r"/(album|track)/", url):
        return parse_release(get(url, host="bc").text)
    root = re.match(r"(https?://[^/]+)", url)
    if not root:
        return None
    page = get(root.group(1) + "/music", host="bc")
    # Artists with a single release 303 straight to it; we already have it.
    if re.search(r"/(album|track)/", page.url):
        return parse_release(page.text)
    m = re.search(r'href="(/(?:album|track)/[^"?]+)"', page.text)
    if not m:
        return None
    return parse_release(get(root.group(1) + m.group(1), host="bc").text)


def resolve_embed(artist_url):
    """Embed URL only, for callers that don't care about the rest."""
    rel = resolve_release(artist_url)
    return rel["embed"] if rel else None


# ---------- local page ----------

PAGE = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>metalhop</title>
<style>
 body{background:#111;color:#ddd;font:16px/1.5 system-ui,sans-serif;margin:0;padding:20px}
 h1{font-size:20px;margin:0 0 4px} .m{color:#888;font-size:14px;margin-bottom:16px}
 iframe{width:100%;max-width:400px;height:472px;border:0}
 a{color:#c33} .idle{color:#666;padding:40px 0}
</style>
<div id=app><div class=idle>waiting for a band...</div></div>
<script>
let rev=-1;
async function tick(){
 try{
  const s = await (await fetch('/state.json')).json();
  if(s.rev===rev) return;
  rev = s.rev;
  document.getElementById('app').innerHTML = s.band
   ? `<h1>${s.band}</h1><div class=m>${s.meta||''}</div>` +
     (s.embed ? `<iframe src="${s.embed}" seamless></iframe>` : '') +
     (s.url ? `<p><a href="${s.url}" target=_blank>open on Bandcamp &rarr;</a></p>`
            : '<p class=m>no Bandcamp link listed</p>')
   : '<div class=idle>waiting for a band...</div>';
 }catch(e){}
}
tick(); setInterval(tick, 2000);
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/state.json"):
            with _lock:
                body = json.dumps(CURRENT).encode()
            ctype = "application/json"
        else:
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_server(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def publish(band=None, url=None, embed=None, meta=""):
    with _lock:
        CURRENT.update(band=band, url=url, embed=embed, meta=meta,
                       rev=CURRENT["rev"] + 1)


# ---------- lineup lists -> standalone page ----------

# A resolved album id never changes, so a built page keeps working with no
# Python behind it: open it over file://, or serve the directory with
# `python3 -m http.server 8800 --bind 127.0.0.1` and reach it from Chrome.

BUILT_PAGE = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
 :root{color-scheme:dark}
 body{background:#111;color:#ddd;font:16px/1.5 system-ui,sans-serif;margin:0;
      padding:16px;max-width:760px;margin-inline:auto}
 h1{font-size:19px;margin:0 0 2px} .sub{color:#777;font-size:13px;margin-bottom:14px}
 #player{position:sticky;top:0;background:#111;padding:8px 0 12px;z-index:2}
 #now{font-size:15px;margin-bottom:6px}
 #now b{color:#fff} #now span{color:#888}
 iframe{width:100%;max-width:400px;height:600px;border:0;display:block}
 .gated{color:#a55;font-style:italic}
 .none{color:#666;padding:24px 0}
 .bar{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
 button{background:#1c1c1c;color:#ddd;border:1px solid #333;border-radius:4px;
        padding:8px 14px;font:inherit;cursor:pointer}
 button:hover{background:#262626} button:disabled{opacity:.35;cursor:default}
 ol{list-style:none;padding:0;margin:0;border-top:1px solid #222}
 li{display:flex;align-items:center;gap:10px;padding:9px 4px;
    border-bottom:1px solid #222;cursor:pointer}
 li:hover{background:#181818}
 li.on{background:#1e1414;box-shadow:inset 3px 0 0 #c33}
 li.heard .nm{color:#666;text-decoration:line-through}
 .nm{flex:1;min-width:0} .nm b{font-weight:600}
 .cy{color:#777;font-size:12px} .rel{color:#666;font-size:12px;display:block}
 .dead .nm b{color:#665} .tick{color:#555;font-size:15px;width:18px;text-align:center}
 a{color:#c33}
</style>
<h1>__TITLE__</h1>
<div class=sub>__SUB__</div>
<div id=player><div id=now class=none>pick a band below</div></div>
<div class=bar>
 <button id=prev>&larr; prev band</button>
 <button id=next>next band &rarr;</button>
 <button id=stop>stop</button>
 <button id=mark>mark heard</button>
 <button id=unheard>next unheard</button>
</div>
<ol id=list></ol>
<script>
const BANDS = __DATA__;
const KEY = '__KEY__';
let heard = new Set(JSON.parse(localStorage.getItem(KEY) || '[]'));
let cur = -1;

const listEl = document.getElementById('list');
const nowEl  = document.getElementById('now');
const player = document.getElementById('player');

function save(){ localStorage.setItem(KEY, JSON.stringify([...heard])); }

// Bandcamp gates some tracks on some releases; the embed shows them greyed.
// Say so up front so a half-streamable record isn't a surprise mid-listen.
function meta(b){
 if(!b.embed) return 'no Bandcamp listed';
 const bits = [b.release || ''];
 if(b.tracks) bits.push(`${b.tracks} tracks`, `${b.minutes} min`);
 const gated = (b.tracks || 0) - (b.playable || 0);
 let s = bits.filter(Boolean).join(' \\u00b7 ');
 if(gated > 0) s += ` \\u00b7 <span class=gated>${gated} not streamable</span>`;
 return s;
}

function render(){
 listEl.innerHTML = '';
 BANDS.forEach((b, i) => {
  const li = document.createElement('li');
  li.className = (i === cur ? 'on ' : '') + (heard.has(b.name) ? 'heard ' : '')
               + (b.embed ? '' : 'dead');
  li.innerHTML = `<span class=tick>${heard.has(b.name) ? '&check;' : ''}</span>`
   + `<span class=nm><b>${b.name}</b> <span class=cy>${b.country}</span>`
   + `<span class=rel>${meta(b)}</span></span>`;
  li.onclick = () => play(i);
  listEl.appendChild(li);
 });
}

function play(i){
 const b = BANDS[i];
 if(!b) return;
 cur = i;
 if(b.embed){
  // Only rebuild the iframe when the band actually changes, so re-rendering
  // the list never restarts playback.
  nowEl.className = '';
  nowEl.innerHTML = `<b>${b.name}</b> <span>&mdash; ${meta(b)}</span>`
   + (b.url ? ` <a href="${b.url}" target=_blank rel=noopener>open &rarr;</a>` : '');
  let f = document.getElementById('f');
  if(!f || f.dataset.src !== b.embed){
   if(f) f.remove();
   f = document.createElement('iframe');
   f.id = 'f'; f.dataset.src = b.embed; f.src = b.embed;
   f.setAttribute('seamless','');
   player.appendChild(f);
  }
 } else {
  nowEl.className = 'none';
  nowEl.textContent = b.name + ' \\u2014 no Bandcamp listed, nothing to play';
  const f = document.getElementById('f'); if(f) f.remove();
 }
 render();
 listEl.children[i].scrollIntoView({block:'nearest'});
}

function step(d){
 let i = cur;
 for(let n = 0; n < BANDS.length; n++){
  i = (i + d + BANDS.length) % BANDS.length;
  if(BANDS[i].embed){ play(i); return; }
 }
}

document.getElementById('next').onclick = () => step(1);
document.getElementById('prev').onclick = () => step(-1);
// Playback lives inside a cross-origin iframe we cannot command, so "stop"
// means destroying the player. Pause and track-skip are Bandcamp's own
// controls, inside the embed.
document.getElementById('stop').onclick = () => {
 const f = document.getElementById('f');
 if(f) f.remove();
 nowEl.className = 'none';
 nowEl.textContent = cur >= 0 ? 'stopped \\u2014 ' + BANDS[cur].name : 'stopped';
};
document.getElementById('mark').onclick = () => {
 if(cur < 0) return;
 const n = BANDS[cur].name;
 heard.has(n) ? heard.delete(n) : heard.add(n);
 save(); render();
};
document.getElementById('unheard').onclick = () => {
 const i = BANDS.findIndex(b => b.embed && !heard.has(b.name));
 if(i >= 0) play(i); else alert('all heard');
};
document.onkeydown = e => {
 if(e.key === 'n') step(1);
 if(e.key === 'p') step(-1);
 if(e.key === 's') document.getElementById('stop').click();
};
render();
</script>"""


def load_list(path):
    """Read a `Name | Country | URL` lineup file. `-` means no Bandcamp."""
    entries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            name = parts[0]
            country = parts[1] if len(parts) > 2 else ""
            url = parts[-1] if len(parts) > 1 else ""
            entries.append({"name": name, "country": country,
                            "url": "" if url in ("-", "") else url})
    return entries


def resolve_list(entries):
    """Resolve every entry, verifying the release really is that band's.

    The artist check is the point: a label root resolves to whatever that label
    put out last, which is silently plausible and completely wrong.
    """
    out = []
    for i, e in enumerate(entries, 1):
        rec = {"name": e["name"], "country": e["country"], "embed": None,
               "release": None, "url": None, "note": None,
               "tracks": 0, "playable": 0, "minutes": 0}
        if not e["url"]:
            rec["note"] = "no bandcamp listed"
            print(f"  {i:2}/{len(entries)} {e['name']:<20} -")
        else:
            try:
                rel = resolve_release(e["url"])
            except Exception as exc:
                rel, rec["note"] = None, f"{type(exc).__name__}: {exc}"
            if rel:
                rec.update(embed=rel["embed"], release=rel["title"],
                           url=rel["url"] or e["url"], tracks=rel["tracks"],
                           playable=rel["playable"], minutes=rel["minutes"])
                got = (rel["artist"] or "").strip()
                want = e["name"].strip()
                if want.lower() not in got.lower() and got.lower() not in want.lower():
                    rec["note"] = f"artist mismatch: page says {got!r}"
                gated = rel["tracks"] - rel["playable"]
                print(f"  {i:2}/{len(entries)} {e['name']:<20} {got} - {rel['title']}"
                      f"  [{rel['tracks']}tr {rel['minutes']}min"
                      f"{f', {gated} NOT STREAMABLE' if gated else ''}]"
                      f"{'   <-- MISMATCH' if rec['note'] else ''}")
            else:
                rec["note"] = rec["note"] or "could not resolve"
                print(f"  {i:2}/{len(entries)} {e['name']:<20} FAILED ({rec['note']})")
        out.append(rec)
    return out


def build_page(records, out_path, title, sub):
    key = "metalhop:" + re.sub(r"\W+", "-", title.lower()).strip("-")
    page = (BUILT_PAGE
            .replace("__DATA__", json.dumps(records, ensure_ascii=False))
            .replace("__TITLE__", html.escape(title))
            .replace("__SUB__", html.escape(sub))
            .replace("__KEY__", key))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return out_path


# ---------- ui ----------

def choose(prompt, items, render):
    for i, item in enumerate(items, 1):
        print(f"  {i:2}. {render(item)}")
    while True:
        raw = input(f"\n{prompt} ").strip().lower()
        if raw in ("q", "b", "s", ""):
            return raw or "s"
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return int(raw) - 1
        print("  ?")


def show_band(band, args):
    meta = " | ".join(b for b in (band.get("genre"), band.get("country")) if b)
    print(f"\n=== {band['name']} ===")
    if meta:
        print(f"    {meta}")

    try:
        links = bandcamp_links(band["id"])
    except Exception as e:
        print(f"    (links unavailable: {e})")
        links = []

    url = links[0] if links else None
    embed = None
    if url:
        print(f"    {url}")
        if args.embed:
            try:
                embed = resolve_embed(url)
            except Exception as e:
                print(f"    (embed lookup failed: {e})")
    else:
        print("    no Bandcamp link listed on MA")

    publish(band["name"], url, embed, meta)

    if url and not args.serve:
        if input("\nOpen in browser? [y/N] ").strip().lower() == "y":
            if not open_url(url):
                print("  no opener available - copy the URL above,"
                      " or rerun with --serve")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("band", nargs="*", help="band name to start from")
    ap.add_argument("--serve", action="store_true", help="local player page")
    ap.add_argument("--embed", action="store_true",
                    help="resolve a real Bandcamp embed (implies --serve, "
                         "costs 2 extra requests to Bandcamp per band)")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--list", metavar="FILE",
                    help="lineup file of `Name | Country | Bandcamp URL` lines")
    ap.add_argument("--build", metavar="OUT.html",
                    help="with --list: write a standalone page and exit")
    ap.add_argument("--title", help="title for the built page")
    args = ap.parse_args()
    args.serve = args.serve or args.embed

    if args.list:
        entries = load_list(args.list)
        title = args.title or os.path.basename(args.list).rsplit(".", 1)[0]
        with open(args.list, encoding="utf-8") as fh:
            first = fh.readline().strip()
        if not args.title and first.startswith("#"):
            title = first.lstrip("# ").split(" - ")[0].strip() or title
        print(f"resolving {len(entries)} bands "
              f"(~{len(entries) * 2 * BC_INTERVAL:.0f}s at the Bandcamp rate limit)\n")
        records = resolve_list(entries)
        ok = sum(1 for r in records if r["embed"])
        bad = [r for r in records if r["note"] and r["embed"]]
        print(f"\n  {ok}/{len(records)} playable"
              + (f", {len(bad)} need checking" if bad else ""))
        if args.build:
            sub = (f"{ok} of {len(records)} bands playable - "
                   "tap a band, tap next when the record ends")
            print("  wrote " + build_page(records, args.build, title, sub))
        return

    if args.serve:
        start_server(args.port)
        print(f"player page: http://127.0.0.1:{args.port}"
              f"   (open in your Android browser)\n")

    print("metalhop - Ctrl-C to quit.\n")
    stack = []
    query = " ".join(args.band).strip()

    while True:
        try:
            if not stack:
                if not query:
                    query = input("Band name: ").strip()
                if not query:
                    return
                # Clear before searching: if the search fails we must not loop
                # straight back and re-fire the same query every MA_INTERVAL.
                q, query = query, ""
                results = search_bands(q)
                if not results:
                    print("  nothing found\n")
                    continue
                print()
                pick = choose("pick # (Enter to search again, q quit):", results,
                              lambda b: f"{b['name']} - {b['country']} - {b['genre'][:48]}")
                if pick == "q":
                    return
                if not isinstance(pick, int):
                    continue
                stack.append(results[pick])

            band = stack[-1]
            show_band(band, args)

            sims = similar_bands(band["id"])
            if not sims:
                print("\n  no similar artists listed - dead end.")
                stack.pop()
                if stack:
                    print("  (back to previous band)\n")
                continue

            print(f"\nSimilar to {band['name']}:")
            pick = choose("pick # (b back, s new search, q quit):", sims,
                          lambda b: f"{b['name']:<28} {b['country']:<12} "
                                    f"{b['genre'][:34]}  +{b['score']}")
            if pick == "q":
                return
            if pick == "b":
                stack.pop()
                continue
            if pick == "s":
                stack.clear()
                continue
            stack.append(sims[pick])

        except KeyboardInterrupt:
            print()
            return
        except Blocked as e:
            # Nothing here can get past a Cloudflare challenge or a rate cap, so
            # stop rather than spin. Exiting is also the polite thing to do.
            print(f"\n  {e}\n")
            return
        except Exception as e:
            print(f"\n  error: {e}\n")
            if stack:
                stack.pop()


if __name__ == "__main__":
    main()
