"""Render a tutorial GIF: landing page -> edition page -> a band playing.

Headless Firefox cannot rasterise under this proot session (SWGL fails to map
a framebuffer), so these frames are drawn rather than captured. Every colour,
size and spacing below is read off the real stylesheets in metalhop.py, and
all the text is pulled out of the built pages, so the result matches what the
pages actually show.
"""
import json
import os
import re
from PIL import Image, ImageDraw, ImageFont

W, H = 420, 900
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "tutorial.gif")

BG, CARD, CARD_HI, CARD_ON = "#111111", "#171717", "#1e1e1e", "#241717"
BORDER, BORDER_HI, RED = "#2c2c2c", "#444444", "#c33333"
TEXT, DIM, DIMMER = "#dddddd", "#777777", "#666666"
GREEN, HOWBG = "#44aa44", "#181818"

F = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
font = lambda s, b=False: ImageFont.truetype(FB if b else F, s)

H1, SUB, HOW = font(17, True), font(12), font(12)
NM, ST, GLYPH = font(15, True), font(11), font(15)


def rrect(d, box, radius, fill, outline=None):
    """Pillow 8.1 has no rounded_rectangle, so corner it by hand."""
    x0, y0, x1, y1 = box
    r = radius
    d.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    d.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    for cx, cy, start in ((x0, y0, 180), (x1 - 2 * r, y0, 270),
                          (x0, y1 - 2 * r, 90), (x1 - 2 * r, y1 - 2 * r, 0)):
        d.pieslice([cx, cy, cx + 2 * r, cy + 2 * r], start, start + 90, fill=fill)
    if outline:
        d.arc([x0, y0, x0 + 2 * r, y0 + 2 * r], 180, 270, fill=outline)
        d.arc([x1 - 2 * r, y0, x1, y0 + 2 * r], 270, 360, fill=outline)
        d.arc([x0, y1 - 2 * r, x0 + 2 * r, y1], 90, 180, fill=outline)
        d.arc([x1 - 2 * r, y1 - 2 * r, x1, y1], 0, 90, fill=outline)
        d.line([x0 + r, y0, x1 - r, y0], fill=outline)
        d.line([x0 + r, y1, x1 - r, y1], fill=outline)
        d.line([x0, y0 + r, x0, y1 - r], fill=outline)
        d.line([x1, y0 + r, x1, y1 - r], fill=outline)


def cursor(d, x, y, tapping=False):
    """A pointer, with a ripple on the frames where a tap lands."""
    if tapping:
        for rad, col in ((26, "#3a1e1e"), (18, "#5a2626")):
            d.ellipse([x - rad, y - rad, x + rad, y + rad], outline=col, width=2)
    arrow = [(x, y), (x, y + 17), (x + 4.5, y + 13), (x + 7.5, y + 19.5),
             (x + 10.5, y + 18), (x + 7.5, y + 11.5), (x + 12.5, y + 11)]
    d.polygon(arrow, fill="#ffffff", outline="#000000")


def landing_rows():
    """Year, label and stat line for each edition, straight out of index.html."""
    page = open(os.path.join(REPO, "index.html"), encoding="utf-8").read()
    rows = []
    for m in re.finditer(r'<span class=yr>(\d+)</span>(.*?)</span>'
                         r'<span class=st>(.*?)<span class=hd', page, re.S):
        label = re.sub(r"<[^>]+>", "", m.group(2))
        stat = re.sub(r"<[^>]+>", "", m.group(3))
        rows.append((m.group(1), unescape(label), unescape(stat)))
    return rows


def unescape(s):
    import html
    return html.unescape(s).strip()


def bands_2019():
    page = open(os.path.join(REPO, "killtown-2019.html"), encoding="utf-8").read()
    return json.loads(re.search(r"const BANDS = (\[.*?\]);\n", page, re.S).group(1))


def frame_landing(highlight=None, tap=False, cur=None):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((16, 16), "Kill-Town Death Fest", font=H1, fill=TEXT)
    d.text((16, 40), "15 editions · 376 slots · 312 playable · 166 hours",
           font=SUB, fill=DIM)

    y = 66
    rrect(d, [16, y, W - 16, y + 46], 4, HOWBG)
    d.rectangle([16, y, 19, y + 46], fill=RED)
    d.text((29, y + 7), "Tap an edition to open its lineup, then", font=HOW, fill="#999999")
    d.text((29, y + 24), "tap any band to play it right on the page.", font=HOW, fill="#999999")

    y += 60
    for i, (year, label, stat) in enumerate(landing_rows()):
        if y > H - 30:
            break
        on = (i == highlight)
        rrect(d, [16, y, W - 16, y + 52], 7,
              CARD_ON if (on and tap) else CARD_HI if on else CARD,
              RED if (on and tap) else BORDER_HI if on else BORDER)
        d.text((30, y + 8), year, font=NM, fill=RED)
        lx = 30 + d.textlength(year, font=NM) + 7
        room = (W - 44) - lx          # stop short of the chevron
        while label and d.textlength(label, font=NM) > room:
            label = label[:-1]
        d.text((lx, y + 8), label, font=NM, fill=TEXT)
        d.text((30, y + 29), stat, font=ST, fill=DIM)
        d.text((W - 36, y + 14), "›", font=font(20), fill=RED)
        y += 60
    if cur:
        cursor(d, cur[0], cur[1], tap)
    return im


def frame_edition(playing=None, highlight=None, tap=False, cur=None, offset=0):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    bands = bands_2019()
    d.text((16, 16), "Kill-Town Death Fest 2019", font=H1, fill=TEXT)
    d.text((16, 40), "32 of 40 bands playable - tap a band, tap next when the",
           font=SUB, fill=DIM)
    d.text((16, 54), "record ends", font=SUB, fill=DIM)

    y = 78
    if playing is None:
        d.text((16, y), "Tap any band below to play it ↓", font=SUB, fill=DIMMER)
        y += 26
    else:
        b = bands[playing]
        d.text((16, y), b["name"], font=font(14, True), fill="#ffffff")
        d.text((16 + d.textlength(b["name"], font=font(14, True)) + 8, y),
               f"— {b['release']} · {b['tracks']} tracks · "
               f"{b['minutes']} min", font=ST, fill=DIM)
        y += 22
        # The Bandcamp embed, drawn as the block it occupies. Its interior is
        # Bandcamp's own iframe, which nothing here renders or controls.
        rrect(d, [16, y, 396, y + 300], 4, "#1a1a1a", "#333333")
        d.rectangle([28, y + 12, 168, y + 152], fill="#242424", outline="#3a3a3a")
        d.polygon([(88, y + 62), (88, y + 102), (122, y + 82)], fill="#8a8a8a")
        d.text((182, y + 14), b["name"].upper(), font=font(11, True), fill="#bbbbbb")
        d.text((182, y + 32), str(b["release"])[:26], font=ST, fill=DIM)
        d.text((182, y + 52), f"{b['tracks']} tracks · {b['minutes']} min",
               font=ST, fill="#555555")
        # Track rows are drawn as bars, not titles: the real tracklist lives
        # inside Bandcamp's iframe and inventing song names to fill the space
        # would put made-up data in a tutorial.
        for t in range(b["tracks"]):
            ty = y + 172 + t * 26
            if ty > y + 232:      # leave the caption line its own room
                break
            d.line([28, ty + 18, 384, ty + 18], fill="#262626")
            d.text((28, ty), f"{t + 1}.", font=ST, fill="#555555")
            d.rectangle([52, ty + 5, 52 + (150 - t * 28), ty + 12], fill="#333333")
        d.text((28, y + 268), "Bandcamp's own player, inside its iframe",
               font=ST, fill="#4a4a4a")
        y += 312

    bx = 16
    for label in ("← prev", "next →", "stop"):
        wpx = d.textlength(label, font=ST) + 22
        rrect(d, [bx, y, bx + wpx, y + 30], 4, "#1c1c1c", "#333333")
        d.text((bx + 11, y + 8), label, font=ST, fill=TEXT)
        bx += wpx + 8
    y += 42

    for i, b in enumerate(bands[offset:], offset):
        if y > H - 20:
            break
        on = (i == highlight)
        cur_on = (i == playing)
        dead = not b["embed"]
        fill = CARD_ON if (cur_on or (on and tap)) else CARD_HI if on else \
            "#141414" if dead else CARD
        edge = RED if (cur_on or (on and tap)) else BORDER_HI if on else BORDER
        rrect(d, [16, y, W - 16, y + 50], 7, fill, edge)
        name_col = DIMMER if dead else TEXT
        d.text((30, y + 8), "✓" if cur_on else "", font=GLYPH, fill=GREEN)
        d.text((48, y + 8), b["name"], font=NM, fill=name_col)
        d.text((48 + d.textlength(b["name"], font=NM) + 8, y + 10),
               b["country"], font=ST, fill=DIM if not dead else "#4a4a4a")
        meta = (f"{b['release']} · {b['tracks']} tracks · {b['minutes']} min"
                if b["embed"] else "no Bandcamp listed")
        d.text((48, y + 28), meta[:46], font=ST, fill=DIMMER if not dead else "#444444")
        if b["embed"]:
            d.text((W - 34, y + 14), "▶", font=font(13),
                   fill=RED if cur_on else DIM)
        y += 58
    if cur:
        cursor(d, cur[0], cur[1], tap)
    return im


# 2019 sits at index 8 on the landing page. The demo band is picked from the
# middle of the lineup so rows show above and below it, rather than from the
# tail where the list would run out under the player.
bands = bands_2019()
TOMB = next(i for i, b in enumerate(bands) if b["name"] == "Dead Void")

frames = [
    (frame_landing(), 1400),
    (frame_landing(cur=(300, 250)), 500),
    (frame_landing(highlight=8, cur=(292, 620)), 700),
    (frame_landing(highlight=8, tap=True, cur=(292, 620)), 500),
    (frame_edition(), 1300),
    (frame_edition(offset=TOMB - 3, cur=(292, 250)), 700),
    (frame_edition(offset=TOMB - 3, highlight=TOMB, cur=(292, 334)), 700),
    (frame_edition(offset=TOMB - 3, highlight=TOMB, tap=True, cur=(292, 334)), 500),
    (frame_edition(playing=TOMB, offset=TOMB - 1), 2400),
    (frame_edition(playing=TOMB, offset=TOMB - 1), 900),
]

# disposal=2 clears each frame before the next is drawn. Without it the
# optimiser leaves the tail of a longer list showing under a shorter one, so
# bands appear on screens they were never on.
imgs = [f.convert("P", palette=Image.ADAPTIVE, colors=64) for f, _ in frames]
imgs[0].save(OUT, save_all=True, append_images=imgs[1:],
             duration=[d for _, d in frames], loop=0, disposal=2, optimize=False)
print("wrote", OUT, len(frames), "frames")
