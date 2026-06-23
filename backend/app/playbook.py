"""Sales Playbook (Blueprint G) — per-archetype plays, regime cheat-sheets, morning routine.

One source of truth for both the in-app Playbook page (``GET /api/playbook``) and the
generated ``docs/playbook.md``. The per-archetype plays line up with the standing posture in
``scoring_config.ARCHETYPE_POSTURE`` and the regime behaviour in ``regime_config``.
"""

from __future__ import annotations

from .regime_config import REGIME_AXES
from .scoring_config import ARCHETYPE_POSTURE, ARCHETYPES

# Per-archetype field plays. Keys: say, call_when, quote, terms, avoid.
ARCHETYPE_PLAYS: dict[str, dict[str, str]] = {
    "Anchor Base-Load": {
        "say": "“You're our priority supply — let's lock a program that rewards how steady you are.”",
        "call_when": "Quarterly business review cadence; immediately if a lift slips its usual window.",
        "quote": "Contract/program pricing with a thin loyalty spread; never the spot-of-the-day.",
        "terms": "Standard terms — they've earned them. Reward reliability, don't re-underwrite.",
        "avoid": "Don't shop them on price or leave them unallocated in a tight market — they are your floor.",
    },
    "Flex Buyer": {
        "say": "“When you're ready to move, we'll have a sharp number — tell us your window.”",
        "call_when": "When inventory is long or the market is falling and you need to place volume.",
        "quote": "Dynamic to rack; capture upside when they lean in, stay flexible.",
        "terms": "Standard terms.",
        "avoid": "Don't build your base plan around them — their timing is erratic by nature.",
    },
    "Premium Spot": {
        "say": "“We can guarantee the gallons today — here's the number for certainty.”",
        "call_when": "When supply is tight or the market is rising/volatile — that's when they pay.",
        "quote": "Hold the premium. They buy availability, not price; discounting leaves money on the table.",
        "terms": "Prepay / tight terms are acceptable and appropriate.",
        "avoid": "Don't discount to 'win' the deal — you'll train a premium buyer to haggle.",
    },
    "Price Shopper": {
        "say": "“If we get long, you'll be the first call with a sharp number.”",
        "call_when": "Only to fill troughs — long inventory or a falling market.",
        "quote": "Thin, opportunistic numbers only; never chase, never your scarce gallons.",
        "terms": "Prepay; minimize credit.",
        "avoid": "Don't allocate tight supply or premium slots to them — lowest priority.",
    },
    "Surplus Absorber": {
        "say": "“We've got length to move — can you take an extra parcel at a clearing price?”",
        "call_when": "The moment inventory runs long or you're tank-constrained.",
        "quote": "Clearing price for length — this protects working capital and frees tankage.",
        "terms": "Prepay preferred.",
        "avoid": "Don't sell them scarce gallons in a tight market — that's value destroyed.",
    },
    "Scarcity Buyer": {
        "say": "“Supply's tight — we can secure your gallons, here's the number.”",
        "call_when": "When the market is rising or supply is tight; they buy on availability.",
        "quote": "Premium in tight markets; monetize the scarcity.",
        "terms": "Tighter terms when you're short.",
        "avoid": "Don't dump length on them cheap — they'll pay up when it's scarce.",
    },
    "Weather-Triggered": {
        "say": "“Cold snap coming — want to pre-build before the rush and the price?”",
        "call_when": "Ahead of HDD spikes; pre-season hedge conversations.",
        "quote": "Pre-season hedge offers; premium during cold snaps.",
        "terms": "Seasonal credit watch — exposure swells in winter.",
        "avoid": "Don't assume summer cadence continues into winter — pre-build capacity.",
    },
    "Credit Drag": {
        "say": "“Let's set you up on prepay so supply is never the question.”",
        "call_when": "Before allocating any volume; immediately when credit tightens.",
        "quote": "Price in the carry; no discounts to a credit risk.",
        "terms": "Shorten terms / prepay; cap exposure hard.",
        "avoid": "Don't gate on volume — gate on credit. Don't extend more rope.",
    },
    "Operationally Expensive": {
        "say": "“We can sharpen your price if we consolidate into fewer, larger loads.”",
        "call_when": "When rack capacity is constrained; during route/schedule planning.",
        "quote": "Add a small-order / handling surcharge; reward consolidation.",
        "terms": "Standard; tie better pricing to fewer, larger orders.",
        "avoid": "Don't let small rush orders clog the rack when slots are scarce.",
    },
    "Strategic Platform": {
        "say": "“Where are you growing next? We want to grow the program with you.”",
        "call_when": "Proactively and often — these are expansion conversations.",
        "quote": "Invest spread for growth / cross-sell; play the long game.",
        "terms": "Flexible to deepen the relationship.",
        "avoid": "Don't nickel-and-dime — you're buying a franchise, not a load.",
    },
    "Backup-Only": {
        "say": "“We're your fallback — when your primary's short, we're one call away.”",
        "call_when": "When you're long and need an outlet; otherwise let them come to you.",
        "quote": "Spot premium; you are their fallback, price accordingly.",
        "terms": "Prepay.",
        "avoid": "Don't commit supply or build forecasts on them — surplus only.",
    },
    "Contract Candidate": {
        "say": "“You're steady enough to earn program pricing — let's lock it in.”",
        "call_when": "Now — especially before a tight market makes the volume harder to hold.",
        "quote": "Offer a term deal to lock steady volume in exchange for a small spread.",
        "terms": "Contract terms; volume commitment in exchange for priority.",
        "avoid": "Don't leave them on spot — a competitor's contract offer can take them.",
    },
}

# Regime cheat-sheets: per axis state, the standing instruction for the floor.
REGIME_CHEATSHEET: dict[str, dict[str, dict[str, str]]] = {
    "inventory": {
        "long": {"do": "Call Surplus Absorbers, Price Shoppers, Flex & Backup buyers to place length at a clearing price.",
                 "dont": "Don't hold out for premium — carrying length costs you daily."},
        "balanced": {"do": "Service your anchors, keep the base humming, work contract candidates.",
                     "dont": "Don't chase marginal spot volume that adds friction."},
        "tight": {"do": "Ration to anchors and Premium/Scarcity buyers; monetize availability.",
                  "dont": "Don't burn scarce gallons on price shoppers or backup-only accounts."},
        "tank_constrained": {"do": "Drain length fast — biggest, cleanest outlets first.",
                             "dont": "Don't take small rush orders that don't move the needle."},
    },
    "market": {
        "rising": {"do": "Hold premiums, offer to lock forward, lean on Scarcity & Weather buyers.",
                   "dont": "Don't discount into a rising tape — you're giving away the move."},
        "falling": {"do": "Move volume now; thin quotes to Price Shoppers & Flex buyers.",
                    "dont": "Don't sit on length waiting for a bounce that may not come."},
        "flat": {"do": "Run the standard book; work the relationship and contract plays.",
                 "dont": "Don't manufacture urgency that isn't there."},
        "volatile": {"do": "Reward stability — lock contract candidates and anchors, hold premium for certainty.",
                     "dont": "Don't over-commit to one direction; keep optionality."},
    },
    "capacity": {
        "ample": {"do": "Take the volume — even smaller loads are fine.",
                  "dont": "Don't add surcharges that scare off easy volume."},
        "normal": {"do": "Standard scheduling.", "dont": "—"},
        "constrained": {"do": "Favor big, clean loads; consolidate Operationally-Expensive accounts.",
                        "dont": "Don't let small rush orders eat scarce rack slots."},
    },
    "credit": {
        "easy": {"do": "Compete on terms where it wins steady volume.",
                 "dont": "Don't over-extend just because credit is cheap today."},
        "normal": {"do": "Standard credit posture.", "dont": "—"},
        "tight": {"do": "Gate Credit-Drag accounts to prepay; protect capital before chasing gallons.",
                  "dont": "Don't extend terms to win marginal volume."},
    },
}

MORNING_ROUTINE: list[dict[str, str]] = [
    {"step": "Set the regime",
     "detail": "Open the Daily Operating Dashboard. Set today's inventory, market, capacity, and "
               "credit regime. Everything re-ranks instantly to match the day you're actually in."},
    {"step": "Read Today's Actions",
     "detail": "The top panel is your stack-ranked worklist — the highest-impact move per account. "
               "Work it top-down; each row tells you the action, the why-now, and the expected impact."},
    {"step": "Clear the alerts",
     "detail": "Scan Credit Alerts and Churn Alerts. Gate or shorten terms on credit risks before you "
               "allocate; call the fading/overdue accounts before they're gone."},
    {"step": "Place or protect inventory",
     "detail": "Inventory Actions tells you whether today is a 'move length' day or a 'protect supply' "
               "day. Work Pricing Opportunities alongside it — raise where they pay, quote thin to fill."},
    {"step": "Advance the franchise",
     "detail": "Before lunch, touch one Strategic Account, one Contract Candidate, and one Discount "
               "Opportunity. These are the plays that compound — don't let the day's noise crowd them out."},
    {"step": "Spot-check a scorecard",
     "detail": "Pull up one account's scorecard. Confirm the recommended action and read the flip-side "
               "line so you know how your plan changes if the regime turns."},
]


def build_playbook(present_archetypes: list[str] | None = None) -> dict:
    """Assemble the playbook payload. If ``present_archetypes`` is given, archetype plays are
    tagged ``present`` so the UI can foreground the ones actually in the book."""
    present = set(present_archetypes or ARCHETYPES)
    plays = []
    for a in ARCHETYPES:
        plays.append({
            "archetype": a, "present": a in present,
            "posture": ARCHETYPE_POSTURE.get(a, {}),
            "play": ARCHETYPE_PLAYS.get(a, {}),
        })
    cheats = []
    for axis, cfg in REGIME_AXES.items():
        cheats.append({
            "axis": axis, "label": cfg["label"],
            "states": [{"state": s, "label": sc["label"], "hint": sc["hint"],
                        **REGIME_CHEATSHEET.get(axis, {}).get(s, {})}
                       for s, sc in cfg["states"].items()],
        })
    return {
        "archetypes": plays,
        "present_archetypes": sorted(present),
        "regime_cheatsheet": cheats,
        "morning_routine": MORNING_ROUTINE,
    }


def render_markdown(pb: dict | None = None) -> str:
    """Render the playbook to Markdown for ``docs/playbook.md``."""
    pb = pb or build_playbook()
    lines: list[str] = []
    lines.append("# RackIQ Sales Playbook")
    lines.append("")
    lines.append("> Auto-generated from the scoring archetypes and the V1 regime matrix "
                 "(`uv run rackiq-export-playbook`). The same content powers the in-app "
                 "**Playbook** page. Keep the customer scorecards and the Daily Operating "
                 "Dashboard open alongside this.")
    lines.append("")

    # Morning routine first — it's the daily on-ramp.
    lines.append("## Morning routine — work the day in six moves")
    lines.append("")
    for i, step in enumerate(pb["morning_routine"], 1):
        lines.append(f"{i}. **{step['step']}.** {step['detail']}")
    lines.append("")

    # Per-archetype plays.
    lines.append("## Archetype plays")
    lines.append("")
    lines.append("For each archetype: what to say, when to call, what to quote, what terms to "
                 "require, and what *not* to do.")
    lines.append("")
    for entry in pb["archetypes"]:
        a = entry["archetype"]
        play = entry["play"]
        posture = entry["posture"]
        lines.append(f"### {a}")
        lines.append("")
        if play.get("say"):
            lines.append(f"- **What to say:** {play['say']}")
        if play.get("call_when"):
            lines.append(f"- **When to call:** {play['call_when']}")
        if play.get("quote"):
            lines.append(f"- **What to quote:** {play['quote']}")
        if play.get("terms"):
            lines.append(f"- **What terms to require:** {play['terms']}")
        if play.get("avoid"):
            lines.append(f"- **What NOT to do:** {play['avoid']}")
        if posture:
            lines.append(f"- **Standing posture:** pricing — {posture.get('pricing', '—')} "
                         f"Terms — {posture.get('terms', '—')} "
                         f"Allocation — {posture.get('allocation', '—')}")
        lines.append("")

    # Regime cheat-sheets.
    lines.append("## Regime cheat-sheets")
    lines.append("")
    lines.append("When the regime selector flips, so does the plan. Read these as "
                 "\"if X → do Y\".")
    lines.append("")
    for axis in pb["regime_cheatsheet"]:
        lines.append(f"### {axis['label']}")
        lines.append("")
        for st in axis["states"]:
            do = st.get("do", "—")
            dont = st.get("dont", "—")
            lines.append(f"- **{st['label']}** — _{st['hint']}_")
            lines.append(f"  - Do: {do}")
            if dont and dont != "—":
                lines.append(f"  - Don't: {dont}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Generated by RackIQ. Edit `backend/app/playbook.py` to change the plays.*")
    lines.append("")
    return "\n".join(lines)
