import requests
import re
import json
import time
import schedule
from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
URL = "https://www.walottery.com/Scratch/Explorer.aspx"

def fetch_games():
    print("Fetching data from walottery.com...")
    try:
        r = requests.get(URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"ERROR: {e}")
        return []
    m = re.search(r"all:\s*JSON\.parse\('(.*?)'\)", r.text, re.DOTALL)
    if not m:
        print("ERROR: Could not find game data in page.")
        return []
    raw = m.group(1).replace("\\'", "'").replace('\\"', '"')
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"JSON error: {e}")
        return []
    games = data.get("Games", [])
    print(f"Found {len(games)} games.")
    return games

def compute_roi(game):
    cost = game.get("Cost", 0)
    if not cost:
        return -999.0
    prizes = game.get("Prizes", [])
    tickets_str = game.get("TicketsPrinted", "0").replace(",", "")
    try:
        tickets = int(tickets_str)
    except:
        tickets = 0
    ev = 0.0
    for p in prizes:
        prize_str = p.get("PrizeAmount","0").replace("$","").replace(",","")
        try:
            prize_val = float(prize_str)
        except:
            continue
        remaining = p.get("PrizesRemainingNumber", 0)
        total = p.get("TotalPrizesNumber", 0)
        if tickets > 0 and remaining > 0:
            prob = remaining / tickets
        elif tickets > 0 and total > 0:
            prob = total / tickets
        else:
            continue
        ev += prize_val * prob
    if ev == 0:
        return -999.0
    return round((ev / cost - 1.0) * 100.0, 2)

def top_prize_info(game):
    prizes = game.get("Prizes", [])
    if not prizes:
        return "?", 0, 0, 0.0
    best_val = 0
    best = None
    for p in prizes:
        prize_str = p.get("PrizeAmount","0").replace("$","").replace(",","")
        try:
            val = float(prize_str)
        except:
            continue
        if val > best_val:
            best_val = val
            best = p
    if not best:
        return "?", 0, 0, 0.0
    total     = best.get("TotalPrizesNumber", 0)
    remaining = best.get("PrizesRemainingNumber", 0)
    pct       = (remaining / total * 100.0) if total > 0 else 0.0
    if best_val >= 1_000_000:
        label = f"${best_val/1_000_000:.1f}M"
    elif best_val >= 1_000:
        label = f"${int(best_val/1000)}K"
    else:
        label = f"${int(best_val)}"
    return label, remaining, total, round(pct, 1)

def score(game):
    """
    Combined score that factors in both ROI and top prize availability.
    Top prize % remaining acts as a multiplier on the ROI quality:
      - 100% remaining  -> full score
      - 50% remaining   -> slight penalty
      - 15% remaining   -> heavy penalty
      - 0% (GONE)       -> forced to bottom (avoid tier)
    This means a ticket with great ROI but depleted top prizes
    will naturally rank below a ticket with decent ROI and fresh prizes.
    """
    roi = game.get("_roi", -999)
    pct = game.get("_top_pct", 0)

    if roi == -999:
        return -9999

    # Prize availability multiplier (0.0 to 1.0)
    if pct == 0:
        multiplier = 0.0       # GONE — no top prize value at all
    elif pct <= 15:
        multiplier = 0.3       # Nearly gone — heavy penalty
    elif pct <= 30:
        multiplier = 0.6       # Getting low — moderate penalty
    elif pct <= 50:
        multiplier = 0.8       # Below half — small penalty
    else:
        multiplier = 1.0       # Plenty left — no penalty

    # ROI is negative; we want less-negative = better
    # Shift to positive for math, apply multiplier, shift back
    shifted    = roi + 100     # e.g. -30 becomes 70, -90 becomes 10
    adjusted   = shifted * multiplier
    final      = adjusted - 100

    return round(final, 2)

def status_tag(game):
    pct = game.get("_top_pct", 0)
    roi = game.get("_roi", -999)
    avg = game.get("_avg", -75)
    std = game.get("_std", 17)

    if pct == 0:
        return "⛔ AVOID   "
    elif pct <= 15:
        return "⚠️  DEPLETED"
    elif roi > avg + 1.5 * std and pct > 50:
        return "★  BEST BET"
    elif roi > avg + 1.5 * std:
        return "★  GOOD    "
    elif roi > avg:
        return "↑  ABOVE   "
    else:
        return "·  AVERAGE "

def run_job():
    now = datetime.now().strftime("%A %b %d, %Y  %I:%M %p")
    W   = 80
    SEP = "=" * W
    DIV = "-" * W

    games = fetch_games()
    if not games:
        print("No data. Try again later.")
        return

    for g in games:
        g["_roi"] = compute_roi(g)
        lbl, rem, tot, pct = top_prize_info(g)
        g["_top_label"]     = lbl
        g["_top_remaining"] = rem
        g["_top_total"]     = tot
        g["_top_pct"]       = pct

    valid = [g for g in games if g["_roi"] != -999]

    rois = [g["_roi"] for g in valid]
    avg  = sum(rois) / len(rois)
    std  = (sum((r - avg)**2 for r in rois) / len(rois)) ** 0.5

    # Store avg/std on each game for status_tag
    for g in valid:
        g["_avg"] = avg
        g["_std"] = std
        g["_score"] = score(g)

    # Sort by combined score (ROI + prize availability)
    valid.sort(key=lambda g: g["_score"], reverse=True)

    def row(rank, g):
        name  = g.get("GameName","?")[:28]
        cost  = f"${g.get('Cost','?')}"
        odds  = g.get("OverallOdds","N/A")
        roi   = f"{g['_roi']:+.1f}%"
        lbl   = g["_top_label"]
        rem   = g["_top_remaining"]
        tot   = g["_top_total"]
        pct   = g["_top_pct"]
        prize = f"{lbl} {rem}/{tot} ({pct}%)"
        tag   = status_tag(g)
        return f"  {rank:<4} {cost:<5} {odds:<13} {roi:<9} {prize:<22} {tag}  {name}"

    hdr = f"  {'#':<4} {'$':<5} {'Odds':<13} {'ROI':<9} {'Top Prize (left)':<22} {'Status':<13} Game Name"

    lines = []
    lines.append("")
    lines.append(SEP)
    lines.append("  WA LOTTERY SCRATCH RANKINGS  —  Scored by ROI + Prize Availability")
    lines.append(f"  {now}")
    lines.append(f"  Avg ROI: {avg:+.1f}%  |  Std Dev: {std:.1f}%  |  Games: {len(valid)}")
    lines.append(SEP)

    # ── BEST BETS ──────────────────────────────────────────────
    best = [g for g in valid if status_tag(g).startswith("★  BEST")]
    if best:
        lines.append("")
        lines.append(f"  ★  BEST BETS  —  High ROI + Top Prizes Still Available")
        lines.append(DIV)
        lines.append(hdr)
        lines.append(DIV)
        for i, g in enumerate(best, 1):
            lines.append(row(i, g))
        lines.append(DIV)

    # ── AVOID LIST ─────────────────────────────────────────────
    avoid = [g for g in valid if status_tag(g).startswith("⛔") or status_tag(g).startswith("⚠️")]
    if avoid:
        lines.append("")
        lines.append(f"  ⛔  SKIP THESE  —  Top Prizes Gone or Nearly Gone")
        lines.append(DIV)
        lines.append(hdr)
        lines.append(DIV)
        for g in avoid:
            lines.append(row("--", g))
        lines.append(DIV)

    # ── FULL RANKED LIST ───────────────────────────────────────
    lines.append("")
    lines.append(f"  ALL {len(valid)} TICKETS  —  Ranked by Combined Score")
    lines.append(DIV)
    lines.append(hdr)
    lines.append(DIV)
    for i, g in enumerate(valid, 1):
        lines.append(row(i, g))
    lines.append("")
    lines.append(SEP)

    output = "\n".join(lines)
    print(output)

    with open("wa_lottery_results.txt", "w", encoding="utf-8") as f:
        f.write(output)
    print("  Saved to wa_lottery_results.txt")

run_job()
schedule.every().day.at("08:00").do(run_job)
schedule.every().day.at("20:00").do(run_job)
print("  Scheduler active — 8 AM and 8 PM daily. Keep open.")
while True:
    schedule.run_pending()
    time.sleep(30)
