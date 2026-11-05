#!/usr/bin/env python3
"""
TankTrack price pipeline - builds fuel-prices.json for the TankTrack Android app without any API key.

Sources (public pages, fetched around Ghana's pricing windows):
  1. GOIL's own price notice          https://www.goil.com.gh/new-fuel-prices/
  2. MyJoyOnline "fuel-prices" tag    (Star Oil, Shell, TotalEnergies announce via news media)
  3. NPA ex-pump price floor PDF      (official minimum; falls back to the previous floor)

Output is the exact schema TankTrack accepts (FuelPrices.parse rules, schemaVersion=1):
  currency GHS, unit per_litre, prices for the six OMCs, asOf dates, optional npaFloor.

Companies not seen in this run are carried forward from the previous file with their old
asOf date - like the app treats hand-typed unchanged prices. Nothing is invented: a price
is written only when a source states it.
"""
import json
import re
import sys
import urllib.request
import urllib.error
import zlib
from datetime import date, datetime, timedelta, timezone

OMCS = ["Shell", "TotalEnergies", "MISA Energy", "So Energy", "GOIL", "Star Oil"]
MIN_PRICE, MAX_PRICE = 3.0, 60.0
UA = "TankTrackPrices/1.0 (personal fuel-tracking app; github.com/BlackChyna)"
GOIL_URL = "https://www.goil.com.gh/new-fuel-prices/"
MJO_TAG = "https://www.myjoyonline.com/tag/fuel-prices/"
OUT = "fuel-prices.json"
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            if attempt == 2:
                print("fetch failed: %s (%s)" % (url, e))
                return None
    return None


def text_of(raw):
    if raw is None:
        return ""
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def visible_text(html):
    h = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    # headings, paragraphs, list items and rows are line separators - headlines must stay separate lines
    h = re.sub(r"</?(h[1-6]|p|li|tr|title)\b[^>]*>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = h.replace("&#8211;", "-").replace("&#8373;", "GH").replace("&#162;", "c")
    h = re.sub(r"[ \t]+", " ", h)
    return h


PRICE_RE = re.compile(r"([0-9]{1,3}\.[0-9]{1,2})")
GRADE_PETROL = re.compile(r"petrol|super xp\b(?! ?95)", re.I)
GRADE_DIESEL = re.compile(r"diesel", re.I)
GRADE_PREMIUM = re.compile(r"ron ?95|premium|super xp 95|super 95", re.I)
FORESIGHT = re.compile(r"could|expect|predict|likely|COPEC|COMAC|may hit|set to|could hit", re.I)


def companies_in(sentence):
    found = []
    aliases = [
        ("GOIL", r"\bGOIL\b|Ghana Oil"),
        ("Star Oil", r"Star\s?Oil"),
        ("TotalEnergies", r"TotalEnergies|\bTotal\b"),
        ("Shell", r"\bShell\b"),
        ("MISA Energy", r"\bMISA\b"),
        ("So Energy", r"\bSo Energy\b|SoEn"),
    ]
    for omc, alias in aliases:
        if omc not in found and re.search(alias, sentence, re.I):
            found.append(omc)
    return found


def price_for_grade(sentence, grade_re):
    """A price numerically adjacent to a fuel-grade keyword in one sentence, else None."""
    best = None
    for m in PRICE_RE.finditer(sentence):
        around = sentence[max(0, m.start() - 14): m.end() + 4]
        if not re.search(r"GH|¢|c\b", around, re.I):
            continue
        window = sentence[max(0, m.start() - 70): m.end() + 30]
        if grade_re.search(window):
            if best is None or m.start() < best[1]:
                best = (float(m.group(1)), m.start())
    return best[0] if best else None


def valid(p):
    try:
        return p is not None and MIN_PRICE <= float(p) <= MAX_PRICE
    except (TypeError, ValueError):
        return False


def parse_effective_date(txt):
    m = re.search(r"EFFECTIVE\s+\w+,\s*(\d{1,2})\w{0,2}\s+([A-Za-z]+)\s+(\d{4})", txt, re.I)
    if not m:
        return None
    day, mon, yr = int(m.group(1)), m.group(2).capitalize(), int(m.group(3))
    if mon in MONTHS:
        return "%04d-%02d-%02d" % (yr, MONTHS.index(mon) + 1, day)
    return None


def scrape_goil():
    html = text_of(fetch(GOIL_URL))
    if not html:
        return None
    txt = visible_text(html)
    prices = {}
    for label, grade in [("Super XP 95", "premium"), ("Diesel XP", "diesel"), ("Super XP", "petrol")]:
        pm = re.search(
            re.escape(label) + r"\s*[-:]\s*[Gg][Hh][Cc]?\s*([0-9]{1,3}(?:\.[0-9]{1,2})?)", txt)
        if pm:
            v = float(pm.group(1))
            if valid(v):
                prices[grade] = v
    if not prices:
        return None
    return {"prices": prices, "asOf": parse_effective_date(txt)}


def harvest_sentences(txt, when, found):
    for chunk in re.split(r"(?<=[.!?])\s+|\n", txt):
        if not chunk or len(chunk) > 400 or FORESIGHT.search(chunk):
            continue
        for omc in companies_in(chunk):
            grades = {}
            for name, gre in (("petrol", GRADE_PETROL), ("diesel", GRADE_DIESEL), ("premium", GRADE_PREMIUM)):
                v = price_for_grade(chunk, gre)
                if valid(v):
                    grades[name] = v
            if grades and omc not in found:
                found[omc] = {"grades": grades, "asOf": when}


def scrape_news():
    html = text_of(fetch(MJO_TAG))
    if not html:
        return {}, None
    found = {}
    floor = {}
    when = None
    m = re.search(r"datePublished[\"']?\s*[:=]\s*[\"'](\d{4}-\d{2}-\d{2})", html)
    if m:
        when = m.group(1)
    alltext = visible_text(html)
    harvest_sentences(alltext, when, found)
    harvest_floor(alltext, when, floor)
    for url in re.findall(r'href="(https://www\.myjoyonline\.com/[a-z0-9\-]{15,}/)"', html)[:4]:
        art = text_of(fetch(url))
        if not art:
            continue
        w = None
        pm = re.search(r'property="article:published_time" content="(\d{4}-\d{2}-\d{2})', art)
        pm = pm or re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', art)
        if pm:
            w = pm.group(1)
        txt = visible_text(art)
        harvest_sentences(txt, w, found)
        harvest_floor(txt, w, floor)
    out = {}
    for omc, d in found.items():
        out[omc] = {"prices": d["grades"], "asOf": d["asOf"]}
    return out, (floor or None)


def harvest_floor(txt, when, floor):
    """NPA minimum/floor amounts in news language, e.g. 'NPA lowered the minimum petrol price ... to GH15.39'."""
    for chunk in re.split(r"(?<=[.!?])\s+|\n", txt):
        if "NPA" not in chunk or not re.search(r"minimum|floor", chunk, re.I):
            continue
        for grade_name, gre in (("petrol", GRADE_PETROL), ("diesel", GRADE_DIESEL)):
            ints = re.search(r"(?:minimum|floor)[^.;]{0,80}?" + gre.pattern + r"[^.;]{0,120}?GH[^0-9]{0,3}([0-9]{1,2}\.[0-9]{1,2})", chunk, re.I)
            if not ints:
                # "petrol floor", amount at the end of the sentence
                ints = re.search(gre.pattern + r"[^.;]{0,80}?(?:minimum|floor)[^.;]{0,120}?GH[^0-9]{0,3}([0-9]{1,2}\.[0-9]{1,2})", chunk, re.I)
            if ints and ints.group(1) and valid(float(ints.group(1))):
                floor[grade_name] = float(ints.group(1))
                floor["_asOf"] = when or floor.get("_asOf")


def scrape_npa_floor(today, prev_floor, news_floor=None):
    """Try the predictable NPA floor PDF name for the current window; else keep the previous floor.
    Priority: this window's PDF > the previous window's PDF > this run's news > the previous file."""
    if today.day <= 15:
        start, end = today.replace(day=1), today.replace(day=15)
    else:
        start = today.replace(day=16)
        end = (date(today.year, today.month % 12 + 1, 1) if today.month < 12
               else date(today.year + 1, 1, 1)) - timedelta(days=1)

    def ordinal(d):
        if 10 <= d % 100 <= 20:
            suf = "th"
        else:
            suf = {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
        return "%d%s" % (d, suf)

    name = ("Ex-Pump-Price-Floors-for-%s-%s-%s-%d"
            % (ordinal(start.day), ordinal(end.day), MONTHS[start.month - 1], start.year))
    # if this window's PDF is not up yet, the previous window's PDF is the last known official floor
    prev_end = start - timedelta(days=1)
    prev_start = prev_end.replace(day=16)
    candidates = []
    for (st, en) in ((start, end), (prev_start, prev_end)):
        base = ("Ex-Pump-Price-Floors-for-%s-%s-%s-%d"
                % (ordinal(st.day), ordinal(en.day), MONTHS[st.month - 1], st.year))
        for suffix in ("", "-1", ".1"):
            candidates.append((st, en, "https://npa.gov.gh/wp-content/uploads/%d/%02d/%s%s.pdf"
                               % (st.year, st.month, base, suffix)))
    raw = None
    for (wst, wen, url) in candidates:
        raw = fetch(url)
        if raw:
            start, end = wst, wen
            break
    petrol = diesel = None
    if raw:
        try:
            parts = []
            for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
                try:
                    parts.append(zlib.decompress(m.group(1)))
                except Exception:
                    pass
            t = b" ".join(parts).decode("latin-1", "replace")
            pm = re.search(r"Petrol\s+([0-9]+\.[0-9]{1,2})", t)
            dm = re.search(r"Diesel\s+([0-9]+\.[0-9]{1,2})", t)
            if pm and valid(float(pm.group(1))):
                petrol = float(pm.group(1))
            if dm and valid(float(dm.group(1))):
                diesel = float(dm.group(1))
        except Exception as e:
            print("npa pdf parse failed:", e)
    fl = {}
    for source in (prev_floor or {}, news_floor or {}):
        for k in ("petrol", "diesel"):
            fl.setdefault(k, source.get(k))
    return {
        "windowStart": start.isoformat(),
        "windowEnd": end.isoformat(),
        "petrol": petrol if valid(petrol) else fl.get("petrol"),
        "diesel": diesel if valid(diesel) else fl.get("diesel"),
    }


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    prev = {}
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        prev = {}

    prev_prices = {p["omc"]: p for p in prev.get("prices", [])}
    prev_floor = prev.get("npaFloor")

    board = {}
    asof = {}

    g = scrape_goil()
    if g:
        board["GOIL"] = g["prices"]
        asof["GOIL"] = g["asOf"] or iso_now()[:10]
        print("GOIL:", g["prices"], "asOf", asof["GOIL"])
    else:
        print("GOIL: not available this run")

    news_data, news_floor = scrape_news()
    for omc, d in news_data.items():
        if omc == "GOIL" and "GOIL" in board:
            continue  # the official site wins
        board[omc] = d["prices"]
        asof[omc] = d["asOf"] or iso_now()[:10]
        print(omc, "news:", d["prices"], "asOf", asof[omc])

    merged = []
    for omc in OMCS:
        prices = None
        if omc in board:
            cand = {k: board[omc][k] for k in ("petrol", "diesel", "premium")
                    if k in board[omc] and valid(board[omc][k])}
            if cand:
                prices = cand
        if prices is None:
            old = prev_prices.get(omc)
            if old and any(valid(old.get(k)) for k in ("petrol", "diesel", "premium")):
                prices = {k: old[k] for k in ("petrol", "diesel", "premium") if valid(old.get(k))}
                asof.setdefault(omc, old.get("asOf"))
        if not prices:
            continue
        merged.append({
            "omc": omc,
            "petrol": prices.get("petrol"),
            "diesel": prices.get("diesel"),
            "premium": prices.get("premium"),
            "asOf": asof.get(omc) or iso_now()[:10],
        })

    if not merged:
        print("ERROR: no usable prices; refusing to overwrite the existing file")
        sys.exit(1)

    floor = scrape_npa_floor(date.today(), prev_floor, news_floor=news_floor)
    if not (floor.get("petrol") or floor.get("diesel")):
        floor = None

    out = {
        "schemaVersion": 1,
        "effectiveDate": iso_now()[:10],
        "source": "TankTrack price pipeline (goil.com.gh, myjoyonline.com, npa.gov.gh)",
        "currency": "GHS",
        "unit": "per_litre",
        "example": False,
        "prices": merged,
    }
    if floor:
        out["npaFloor"] = floor

    # self-check: mirror the app's strict FuelPrices.parse rules before writing
    assert out["effectiveDate"] == date.today().isoformat()
    assert out["currency"] == "GHS" and out["unit"] == "per_litre" and out["example"] is False
    assert len({p["omc"] for p in merged}) == len(merged) and all(p["omc"] in OMCS for p in merged)
    iso = re.compile(r"\d{4}-\d{2}-\d{2}")
    for p in merged:
        for k in ("petrol", "diesel", "premium"):
            if p[k] is not None:
                assert MIN_PRICE <= p[k] <= MAX_PRICE, p
        assert iso.fullmatch(str(p["asOf"])), p
    if out.get("npaFloor"):
        for k in ("petrol", "diesel"):
            v = out["npaFloor"][k]
            assert v is None or MIN_PRICE <= v <= MAX_PRICE

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("WROTE", OUT, "with", len(merged), "companies")

    stamp = iso_now().replace(":", "").replace("-", "")
    try:
        with open("history/%s.json" % stamp, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

    cedi = "GH\u20b5"
    rows = ["| Company | Petrol | Diesel | Premium 95 | Dated |", "|---|---|---|---|---|"]
    for p in merged:
        cells = [("%s %.2f" % (cedi, p[k])) if p[k] is not None else "-" for k in ("petrol", "diesel", "premium")]
        rows.append("| %s | %s | %s | %s | %s |" % (p["omc"], cells[0], cells[1], cells[2], p["asOf"]))
    table = "\n".join(rows) + "\n"
    if floor:
        table += ("\n\nNPA minimum (official floor), %s to %s: petrol %s, diesel %s.\n" % (
            floor["windowStart"], floor["windowEnd"],
            "%s %.2f" % (cedi, floor["petrol"]) if floor.get("petrol") else "-",
            "%s %.2f" % (cedi, floor["diesel"]) if floor.get("diesel") else "-"))
    with open("PRICES.md", "w", encoding="utf-8") as f:
        f.write("# Latest prices in the TankTrack file\n\n"
                "Generated automatically by the scraper - do not edit.\n\n" + table)


if __name__ == "__main__":
    main()
