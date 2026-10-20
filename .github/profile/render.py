"""Renders every panel in assets/ for the profile README, in light and dark.

Static panels (header, project cards) only change when this file does. Stats panels
are rebuilt from the GitHub GraphQL API each run; set GH_TOKEN to a token that can
read your private repos, or they only cover public ones.

Text is outlined from IBM Plex (fetch it into fonts/ first, see the workflow) so the
SVGs look the same in every browser. Icons: Lucide (ISC), Simple Icons (CC0), Devicon (MIT).

Layout: full-width panels are 1200 units, half-width ones 600, and both keep INSET
units clear on each side. Placed side by side at width="50%" with nothing between
them, two half panels line up exactly under a full one with a 2 * INSET gap.
"""
import base64
import datetime as dt
import json
import math
import os
import re
import sys
import urllib.request
from collections import Counter

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "assets")
INSET = 8
PAD = INSET + 28  # content edge inside a panel

FONTS = {k: TTFont(os.path.join(HERE, "fonts", f)) for k, f in {
    "sans": "IBMPlexSans-Regular.ttf", "bold": "IBMPlexSans-SemiBold.ttf",
    "mono": "IBMPlexMono-Regular.ttf"}.items()}

THEMES = {
    "dark": dict(fg="#e6edf3", muted="#9198a1", line="#30363d", card="#151b23", grid="#21262d",
                 accent="#3fb950", accent_soft="#2b5a36", tint_fill=.1, tint_line=.4,
                 series=["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"], other="#6e7681"),
    "light": dict(fg="#1f2328", muted="#59636e", line="#d1d9e0", card="#f6f8fa", grid="#e6eaef",
                  accent="#1a7f37", accent_soft="#aedbbb", tint_fill=.07, tint_line=.35,
                  series=["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"], other="#8c959f"),
}
# Colour follows the language, not its rank, so a reshuffle never repaints the survivors.
# Slot order passes the palette validator for adjacent pairs in both themes.
LANG_SLOT = {"Java": 0, "Python": 1, "TypeScript": 2, "Rust": 3, "Lua": 4, "Kotlin": 5}


# ---------------------------------------------------------------- drawing helpers

def measure(s, font, size):
    f = FONTS[font]
    cmap, hmtx = f.getBestCmap(), f["hmtx"]
    return sum(hmtx[cmap.get(ord(c), cmap[ord("?")])][0] for c in s) * size / f["head"].unitsPerEm


def text(s, x, y, font, size, fill, anchor="start"):
    """Outlined text as one <path>; y is the baseline."""
    f = FONTS[font]
    cmap, gs, hmtx = f.getBestCmap(), f.getGlyphSet(), f["hmtx"]
    k = size / f["head"].unitsPerEm
    if anchor == "end":
        x -= measure(s, font, size)
    elif anchor == "middle":
        x -= measure(s, font, size) / 2
    pen, cx = SVGPathPen(gs), 0
    for c in s:
        g = cmap.get(ord(c), cmap[ord("?")])
        gs[g].draw(TransformPen(pen, (k, 0, 0, -k, x + cx * k, y)))
        cx += hmtx[g][0]
    return f'<path fill="{fill}" d="{pen.getCommands()}"/>'


def wrap(s, font, size, width):
    lines, cur = [], ""
    for w in s.split():
        t = f"{cur} {w}".strip()
        if cur and measure(t, font, size) > width:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + [cur]


_ICONS = {}

def icon(name, x, y, size, color=None):
    """Square icon at (x, y). Lucide icons are stroked in `color`, Simple Icons filled
    with it; icons that carry their own colours (color=None) keep them."""
    if name not in _ICONS:
        src = open(os.path.join(HERE, "icons", f"{name}.svg")).read()
        src = re.sub(r"<!--.*?-->|<title>.*?</title>", "", src, flags=re.S)
        box = float(re.search(r'viewBox="0 0 ([\d.]+)', src).group(1))
        inner = re.search(r"<svg[^>]*>(.*)</svg>", src, re.S).group(1).strip()
        _ICONS[name] = (inner, box, "lucide" in src)
    inner, box, stroked = _ICONS[name]
    if color is None:
        paint = ""
    elif stroked:
        paint = f'fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    else:
        paint = f'fill="{color}"'
    return f'<g transform="translate({x} {y}) scale({size / box})" {paint}>{inner}</g>'


def mix(a, b, amount):
    """Blend hex colour b into a by amount (0..1)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(ca, cb))


def panel(w, h, t, tint=None):
    """Panel background; a tint washes the fill and border faintly toward one hue."""
    fill, stroke = t["card"], t["line"]
    if tint:
        fill, stroke = mix(fill, tint, t["tint_fill"]), mix(stroke, tint, t["tint_line"])
    return (f'<rect x="{INSET + .5}" y=".5" width="{w - 2 * INSET - 1}" height="{h - 1}" rx="10" '
            f'fill="{fill}" stroke="{stroke}"/>')


def save(name, w, h, body, label):
    os.makedirs(OUT, exist_ok=True)
    label = label.replace("&", "&amp;").replace('"', "&quot;")
    with open(os.path.join(OUT, name), "w") as f:
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
                f'role="img" aria-label="{label}"><title>{label}</title>{body}</svg>')


def column(x, base, w, h, fill, r=3):
    """A bar anchored to the baseline with rounded top corners."""
    if h <= 0:
        return ""
    r = min(r, w / 2, h)
    return (f'<path fill="{fill}" d="M{x:.1f} {base}V{base - h + r:.1f}Q{x:.1f} {base - h:.1f} {x + r:.1f} {base - h:.1f}'
            f'H{x + w - r:.1f}Q{x + w:.1f} {base - h:.1f} {x + w:.1f} {base - h + r:.1f}V{base}Z"/>')


def nice_max(v):
    if v <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag


def compact(n):
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


# ---------------------------------------------------------------- static panels

def header(th):
    t = THEMES[th]
    W, H = 1200, 200
    p = [text("Ryn", INSET, 96, "bold", 68, t["fg"]),
         icon("graduation-cap", INSET + 2, 124, 22, t["muted"]),
         text("Computer science student", INSET + 34, 143, "sans", 23, t["muted"])]
    # a quiet stepped block silhouette on the right
    s, gap, cols, rows = 22, 6, 9, 5
    x0, y0 = W - INSET - cols * (s + gap) + gap, 24
    heights = [1, 2, 2, 3, 3, 4, 3, 4, 5]
    lit = {(5, 3), (7, 3), (8, 4)}
    for c in range(cols):
        for r in range(rows):
            x, y = x0 + c * (s + gap), y0 + r * (s + gap)
            level = rows - 1 - r
            if level < heights[c]:
                col = t["accent"] if (c, level) in lit else t["line"]
                p.append(f'<rect x="{x}" y="{y}" width="{s}" height="{s}" rx="3" fill="{col}"/>')
            else:
                p.append(f'<rect x="{x + .5}" y="{y + .5}" width="{s - 1}" height="{s - 1}" rx="3" fill="none" stroke="{t["grid"]}"/>')
    p.append(f'<rect x="{INSET}" y="{H - 1}" width="{W - 2 * INSET}" height="1" fill="{t["line"]}"/>')
    save(f"header-{th}.svg", W, H, "".join(p), "Ryn. Computer science student.")


CARDS = [  # slug, width, title, description, minecraft version, link host, tint
    ("chud", 1200, "CHUD",
     "Keeps your commissions, powder, pickaxe ability cooldown and mining stats on screen, with waypoints "
     "and a profit tracker built in. Every HUD can be moved, scaled and recoloured from /chud.",
     "Fabric 26.2", "modrinth", "#3f6fd0"),
    ("fungus", 600, "Fungus",
     "Base for client-side Fabric mods. Comes with viewmodel tweaks, block highlight, and scoreboard "
     "and potion icon hiders.",
     "Fabric 26.1.2", "github", "#c0507a"),
    ("replenish", 600, "ReplenishSingle",
     "SkyBlock's Replenish enchant for singleplayer. Broken crops replant on the next tick, so you can "
     "test farm designs offline.",
     "Fabric", "github", "#8fbf3f"),
]


CARD_SIZE, CARD_LEAD = 23, 32
ICON_W = 150  # CHUD's logo on its card


def text_width(slug, W):
    return W - 2 * PAD - (ICON_W + 120 if slug == "chud" else 0)


def card_lines(slug, W, desc):
    return wrap(desc, "sans", CARD_SIZE, text_width(slug, W))[:3]


def art_chud(W, H, t, tint, th):
    """CHUD's own logo, centred in the space left of the link icons."""
    with open(os.path.join(HERE, "icons", "chud.png"), "rb") as f:
        data = base64.b64encode(f.read()).decode()
    x, y = W - PAD - 80 - ICON_W, (H - ICON_W) / 2
    return (f'<image x="{x}" y="{y:.0f}" width="{ICON_W}" height="{ICON_W}" '
            f'preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,{data}"/>')


def art_fungus(W, H, t, tint, th):
    """A mushroom standing in the bottom-right corner."""
    cx, base, r = W - PAD - 52, H - 22, 52
    cap = base - 44  # underside of the cap
    spot = mix(t["card"], tint, t["tint_fill"])
    return (f'<g opacity="{.22 if th == "dark" else .28}" fill="{tint}">'
            f'<rect x="{cx - 15}" y="{cap - 4}" width="30" height="{base - cap + 4}" rx="9"/>'
            f'<path d="M{cx - r} {cap}A{r} {r * .82} 0 0 1 {cx + r} {cap}Q{cx + r} {cap + 8} {cx + r - 8} {cap + 8}'
            f'H{cx - r + 8}Q{cx - r} {cap + 8} {cx - r} {cap}Z"/>'
            f'<circle cx="{cx - 22}" cy="{cap - 18}" r="8" fill="{spot}"/>'
            f'<circle cx="{cx + 6}" cy="{cap - 34}" r="6" fill="{spot}"/>'
            f'<circle cx="{cx + 28}" cy="{cap - 12}" r="7" fill="{spot}"/></g>')


def art_replenish(W, H, t, tint, th):
    """Two stalks of wheat in the bottom-right corner."""
    p = [f'<g opacity="{.24 if th == "dark" else .3}">']
    for dx, s in [(-118, 64), (-66, 78)]:
        p.append(icon("wheat", W - PAD + dx, H - s - 18, s, tint))
    return "".join(p) + "</g>"


ART = {"chud": art_chud, "fungus": art_fungus, "replenish": art_replenish}


def card(slug, W, title, desc, version, host, tint, th):
    t = THEMES[th]
    size, lead = CARD_SIZE, CARD_LEAD
    lines = card_lines(slug, W, desc)
    # cards that share a row share a height
    rows = max(len(card_lines(sl, w, d)) for sl, w, _, d, *_ in CARDS if w == W)
    H = 100 + (rows - 1) * lead + 80
    if slug == "chud":
        H = max(H, ICON_W + 70)
    clip = f"clip-{slug}-{th}"
    p = [panel(W, H, t, tint),
         f'<clipPath id="{clip}"><rect x="{INSET + 1}" y="1" width="{W - 2 * INSET - 2}" height="{H - 2}" rx="9"/></clipPath>',
         f'<g clip-path="url(#{clip})">{ART[slug](W, H, t, tint, th)}</g>',
         text(title, PAD, 58, "bold", 28, t["fg"]),
         icon(host, W - PAD - 54, 34, 24, t["muted"]), icon("arrow-up-right", W - PAD - 22, 35, 22, t["muted"])]
    for i, line in enumerate(lines):
        p.append(text(line, PAD, 100 + i * lead, "sans", size, t["muted"]))
    fy = H - 34
    p += [icon("java", PAD, fy - 18, 22), text("Java", PAD + 30, fy, "mono", 16, t["muted"]),
          icon("box", PAD + 92, fy - 17, 20, t["muted"]), text(version, PAD + 120, fy, "mono", 16, t["muted"])]
    save(f"{slug}-{th}.svg", W, H, "".join(p), f"{title}: {desc}")


# ---------------------------------------------------------------- stats

def gql(query, token):
    req = urllib.request.Request("https://api.github.com/graphql", json.dumps({"query": query}).encode(),
                                 {"Authorization": f"bearer {token}", "User-Agent": "profile-render"})
    with urllib.request.urlopen(req) as r:
        out = json.load(r)
    if "errors" in out:
        sys.exit(f"GraphQL error: {out['errors']}")
    return out["data"]


def fetch():
    token = os.environ.get("GH_TOKEN") or sys.exit("GH_TOKEN is not set")
    return gql("""{ viewer {
      contributionsCollection { contributionCalendar { totalContributions
        weeks { contributionDays { date contributionCount } } } }
      repositories(first: 100, ownerAffiliations: OWNER, isFork: false) { totalCount
        nodes { languages(first: 20, orderBy: {field: SIZE, direction: DESC}) { edges { size node { name } } } } }
    } }""", token)["viewer"]


def summarize(d):
    cal = d["contributionsCollection"]["contributionCalendar"]
    weeks = [[(dt.date.fromisoformat(x["date"]), x["contributionCount"]) for x in w["contributionDays"]]
             for w in cal["weeks"]]
    days = [x for w in weeks for x in w]
    longest = run = 0
    for _, n in days:
        run = run + 1 if n else 0
        longest = max(longest, run)
    current, tail = 0, days[::-1]
    if tail and tail[0][1] == 0:  # today may just not have started yet
        tail = tail[1:]
    for _, n in tail:
        if not n:
            break
        current += 1
    weekday = [0] * 7
    for day, n in days:
        weekday[day.weekday()] += n
    langs, counted = Counter(), 0
    for repo in d["repositories"]["nodes"]:
        edges = repo["languages"]["edges"]
        total = sum(e["size"] for e in edges)
        if total:
            counted += 1
            for e in edges:
                langs[e["node"]["name"]] += e["size"] / total
    return dict(total=cal["totalContributions"], weeks=weeks, active=sum(1 for _, n in days if n),
                longest=longest, current=current, weekday=weekday, repos=d["repositories"]["totalCount"],
                langs={k: v / counted for k, v in langs.items()}, counted=counted)


def activity(s, th):
    t = THEMES[th]
    W, H = 1200, 380
    p = [panel(W, H, t)]
    tiles = [("git-commit-horizontal", f"{s['total']:,}", "contributions, past year"),
             ("calendar-days", str(s["active"]), "days with activity"),
             ("flame", f"{s['longest']}d", f"longest streak, {s['current']}d now"),
             ("folder-git-2", str(s["repos"]), "repositories, most private")]
    tw = (W - 2 * PAD) / len(tiles)
    for i, (ic, value, label) in enumerate(tiles):
        x = PAD + i * tw
        p += [icon(ic, x, 32, 20, t["muted"]), text(value, x, 92, "bold", 34, t["fg"]),
              text(label, x, 118, "sans", 15, t["muted"])]
    p.append(f'<rect x="{PAD}" y="146" width="{W - 2 * PAD}" height="1" fill="{t["grid"]}"/>')
    p.append(text("Contributions per week, last 12 months", PAD, 180, "sans", 15, t["muted"]))
    weekly = [sum(n for _, n in w) for w in s["weeks"]]
    base, top, x0, x1 = 334, 206, PAD, W - PAD - 36  # room on the right for tick labels
    ymax = nice_max(max(weekly))
    for frac in (0.5, 1):
        y = base - (base - top) * frac
        p += [f'<rect x="{x0}" y="{y:.1f}" width="{x1 - x0}" height="1" fill="{t["grid"]}"/>',
              text(compact(int(ymax * frac)), W - PAD, y + 5, "mono", 13, t["muted"], anchor="end")]
    p.append(f'<rect x="{x0}" y="{base}" width="{x1 - x0}" height="1" fill="{t["line"]}"/>')
    p.append(text("0", W - PAD, base + 5, "mono", 13, t["muted"], anchor="end"))
    slot = (x1 - x0) / len(weekly)
    peak = weekly.index(max(weekly))
    last_label = -9
    for i, (w, n) in enumerate(zip(s["weeks"], weekly)):
        x = x0 + i * slot + 1
        p.append(column(x, base, slot - 2, (base - top) * n / ymax, t["accent"] if i == peak else t["accent_soft"]))
        if i > 0 and i - last_label >= 3 and any(d.day == 1 for d, _ in w):
            month = next(d for d, _ in w if d.day == 1)
            p.append(text(month.strftime("%b"), x, base + 26, "mono", 13, t["muted"]))
            last_label = i
    # peak label hangs left of the bar on the right half so it stays inside the plot
    px = x0 + peak * slot + slot / 2
    py = base - (base - top) * weekly[peak] / ymax - 8
    label = f"{weekly[peak]} in the week of {s['weeks'][peak][0][0].strftime('%b %-d')}"
    p.append(text(label, px, py, "mono", 13, t["fg"], anchor="end" if px > W / 2 else "start"))
    save(f"activity-{th}.svg", W, H, "".join(p),
         f"{s['total']} contributions in the last year across {s['active']} days; longest streak {s['longest']} days.")


def languages(s, th):
    t = THEMES[th]
    W, H = 600, 300
    p = [panel(W, H, t), icon("code-xml", PAD, 30, 22, t["muted"]),
         text("Languages", PAD + 32, 49, "bold", 21, t["fg"]),
         text(f"Across {s['counted']} repos, each weighted equally", PAD, 80, "sans", 15, t["muted"])]
    ranked = sorted(s["langs"].items(), key=lambda kv: -kv[1])
    shown = [(k, v) for k, v in ranked if k in LANG_SLOT][:6]
    other = 1 - sum(v for _, v in shown)
    rows = shown + ([("Other", other)] if other > 0.005 else [])
    color = lambda k: t["series"][LANG_SLOT[k]] if k in LANG_SLOT else t["other"]
    x, y, bw, bh = PAD, 104, W - 2 * PAD, 16
    p.append(f'<clipPath id="bar-{th}"><rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="4"/></clipPath><g clip-path="url(#bar-{th})">')
    cx = x
    for i, (k, v) in enumerate(rows):
        w = bw * v
        gap = 2 if i < len(rows) - 1 else 0
        p.append(f'<rect x="{cx:.1f}" y="{y}" width="{max(w - gap, 0):.1f}" height="{bh}" fill="{color(k)}"/>')
        cx += w
    p.append("</g>")
    colw = (W - 2 * PAD) / 2
    for i, (k, v) in enumerate(rows):
        lx = PAD + (i // 4) * colw
        ly = 158 + (i % 4) * 34
        p += [f'<rect x="{lx}" y="{ly - 11}" width="12" height="12" rx="3" fill="{color(k)}"/>',
              text(k, lx + 22, ly, "sans", 17, t["fg"]),
              text(f"{v * 100:.1f}%", lx + colw - 32, ly, "mono", 15, t["muted"], anchor="end")]
    save(f"languages-{th}.svg", W, H, "".join(p),
         "Languages: " + ", ".join(f"{k} {v * 100:.0f}%" for k, v in rows))


def weekdays(s, th):
    t = THEMES[th]
    W, H = 600, 300
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    vals = s["weekday"]
    p = [panel(W, H, t), icon("flame", PAD, 30, 22, t["muted"]),
         text("Busiest days", PAD + 32, 49, "bold", 21, t["fg"]),
         text("Contributions by weekday, last 12 months", PAD, 80, "sans", 15, t["muted"])]
    base, top = 250, 128
    ymax = max(vals) or 1
    slot = (W - 2 * PAD) / 7
    peak = vals.index(max(vals))
    p.append(f'<rect x="{PAD}" y="{base}" width="{W - 2 * PAD}" height="1" fill="{t["line"]}"/>')
    for i, v in enumerate(vals):
        x = PAD + i * slot + 8
        bw = slot - 16
        h = (base - top) * v / ymax
        p.append(column(x, base, bw, h, t["accent"] if i == peak else t["accent_soft"], r=4))
        p.append(text(compact(v), x + bw / 2, base - h - 8, "mono", 13, t["fg"] if i == peak else t["muted"], anchor="middle"))
        p.append(text(names[i], x + bw / 2, base + 24, "mono", 13, t["muted"], anchor="middle"))
    save(f"weekdays-{th}.svg", W, H, "".join(p),
         "Contributions by weekday: " + ", ".join(f"{n} {v}" for n, v in zip(names, vals)))


def main():
    stats = summarize(fetch()) if "--static" not in sys.argv else None
    for th in THEMES:
        header(th)
        for c in CARDS:
            card(*c, th)
        if stats:
            activity(stats, th)
            languages(stats, th)
            weekdays(stats, th)


if __name__ == "__main__":
    main()
