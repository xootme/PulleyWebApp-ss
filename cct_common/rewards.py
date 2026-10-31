"""
rewards.py — the rewards program's rules (0.37.0; the owner's rules of
2026-10-30, CCT_Marketing's rewards/Rewards_Plan.md, Controller campaign
2026-10-30-rewards-paid-first.md). All of it is behind the `rewards`
business setting, off until the owner turns it on after the Terms say so.

- Bonus tokens, not discounts: the pack's price and checkout stay as they
  are and the bonus comes with the purchase, as its own ledger row (kind
  "bonus", a free kind, ref "bonus:<provider>:<order id>"), in the same
  write as the purchase, so exactly once (payments.Payments.complete).
  Bonus = the pack's tokens × the percentage, rounded down.
- +25% on the first purchase: the account has no purchase before this one
  (one before the switch date counts), and the card or PayPal account (its
  payer key: a hash of Stripe's card fingerprint or PayPal's payer id) has
  never had a first-purchase bonus on any account. No key (Stripe Link, a
  lookup that failed): the account check alone.
- +10% after every 200 paid tokens spent, +20% at every 1,000 instead. The
  count is the paid tokens downloads used from the switch date, refunded
  ones left out (TokenStore._split). One reward is held at a time: a better
  one replaces it, an equal or lesser one isn't added. It's used on the next
  pack, whatever its size. The first-purchase bonus and a held reward never
  stack: the bigger is used, and a held reward that wasn't stays.
- Nothing about the held reward is stored: it's worked out from the ledger
  (the milestones each spend crossed) and the orders (which bonus each
  purchase used, payments.bonus_rule).
- Paid tokens are used first for spends from the switch date: the first
  time the setting was turned on (settings.setting_since), kept for good.
- A refund or chargeback takes back the same share of the purchase's bonus
  (payments.Payments.refund, record_admin_refund), even if that takes the
  balance below zero, as a chargeback's tokens already can.
"""
from __future__ import annotations

import hashlib
from typing import Optional

FIRST_PCT = 25
STEP_TOKENS, STEP_PCT = 200, 10        # every 200 paid tokens spent
BIG_TOKENS, BIG_PCT = 1000, 20         # every 1,000, in place of the +10%
RULES = {"first": FIRST_PCT, "10": STEP_PCT, "20": BIG_PCT}


def rewards_on(path) -> bool:
    from .settings import setting
    return setting(path, "rewards") is True


def payer_key(kind: str, raw) -> Optional[str]:
    """What an order keeps of the card or PayPal account that paid: a hash,
    never the value itself. kind: "stripe-card" or "paypal-payer"."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    return hashlib.sha256(f"cct-rewards:{kind}:{raw}".encode()).hexdigest()


def bonus_for(tokens: int, pct: int) -> int:
    return int(tokens) * int(pct) // 100


def milestone_pct(n: int) -> int:
    """The reward for the nth milestone (n × 200 paid tokens spent)."""
    return BIG_PCT if (n * STEP_TOKENS) % BIG_TOKENS == 0 else STEP_PCT


def _bonus_rows(db, account_id: str) -> list:
    """(ledger id, rule) of each bonus this account's purchases had, in order.
    Empty without a bonus row, so a database no purchase reached needs no payments table."""
    if not db.execute("SELECT 1 FROM ledger WHERE account_id = ? AND kind = 'bonus' LIMIT 1",
                      (account_id,)).fetchone():
        return []
    return db.execute(
        "SELECT l.id, p.bonus_rule FROM ledger l JOIN payments p "
        "ON l.ref = 'bonus:' || p.provider || ':' || p.order_id "
        "WHERE l.account_id = ? AND l.kind = 'bonus' ORDER BY l.id", (account_id,)).fetchall()


def state(db, tokens, account_id: str, since: float) -> dict:
    """{"held", "paid_spent", "next"}: the reward held now (10, 20 or None),
    the paid tokens spent since the switch, and the next milestone that
    would beat what's held ({"at", "pct"}; None while +20% is held)."""
    split = tokens._split(db, account_id, since)
    events = [(sid, "spend", n) for sid, _, n in split["spends"]]
    events += [(r["id"], "bonus", r["bonus_rule"]) for r in _bonus_rows(db, account_id)]
    held, spent = None, 0
    for _, what, x in sorted(events, key=lambda e: e[0]):
        if what == "spend":
            for n in range(spent // STEP_TOKENS + 1, (spent + x) // STEP_TOKENS + 1):
                held = max(held or 0, milestone_pct(n))
            spent += x
        elif x in ("10", "20"):
            held = None                                  # used on that pack
    nxt = None
    if held != BIG_PCT:
        n = spent // STEP_TOKENS + 1
        while held is not None and milestone_pct(n) <= held:
            n += 1
        nxt = {"at": n * STEP_TOKENS, "pct": milestone_pct(n)}
    return {"held": held, "paid_spent": spent, "next": nxt}


def first_eligible(db, account_id: str, order_ref: str, key: Optional[str]) -> bool:
    """No purchase on this account before this one, and the payer key has
    never had a first-purchase bonus on any account."""
    earlier = db.execute("SELECT 1 FROM ledger WHERE account_id = ? AND kind = 'purchase' AND ref <> ? LIMIT 1",
                         (account_id, order_ref)).fetchone()
    if earlier:
        return False
    if key is None:
        return True
    had = db.execute("SELECT 1 FROM payments WHERE payer_key = ? AND bonus_rule = 'first' LIMIT 1",
                     (key,)).fetchone()
    return not had


def decide(db, tokens, account_id: str, order_ref: str, key: Optional[str], since: float) -> tuple:
    """(rule, pct) for this purchase: "first", "10", "20", or (None, 0)."""
    held = state(db, tokens, account_id, since)["held"]
    first = FIRST_PCT if first_eligible(db, account_id, order_ref, key) else 0
    if not first and not held:
        return None, 0
    return ("first", first) if first >= (held or 0) else (str(held), held)


def bonus_detail(rule: str, bonus: int, provider: str, order_id: str) -> str:
    why = {"first": "first purchase", "10": f"{STEP_TOKENS} paid tokens used",
           "20": f"{BIG_TOKENS:,} paid tokens used"}[rule]
    return f"{bonus} bonus tokens: {why} ({provider} {order_id})"


# ── step 6: what the pages say (0.51.0; the owner's words of 2026-10-30) ──
# Shown only while the rewards setting is on: the buy page and buy panel
# above the packs and on each pack, the "Your tokens" dialog and the account
# page, and the line after paying. No receipt email of our own.

FREE_NOTE = "including bonus tokens: they're used after the ones you buy, and can't be refunded."
FIRST_SMALL = "Once per account and once per card or PayPal account."


def offer(db, tokens, account_id: str, since) -> dict:
    """What a pack would come with now: {"pct", "first"}. The first-purchase
    +25% while the account has no purchase (the card's own check comes at
    payment, so it can't be promised); else a held reward; else pct 0."""
    if not db.execute("SELECT 1 FROM ledger WHERE account_id = ? AND kind = 'purchase' LIMIT 1",
                      (account_id,)).fetchone():
        return {"pct": FIRST_PCT, "first": True}
    held = state(db, tokens, account_id, since)["held"] if since is not None else None
    return {"pct": held or 0, "first": False}


def offer_lines(o: dict) -> list:
    """Above the packs: [line, small print or None], or [] with nothing to offer."""
    if o["first"]:
        return [f"Your first pack comes with {FIRST_PCT}% extra tokens.", FIRST_SMALL]
    if o["pct"]:
        return [f"Your next pack comes with {o['pct']}% extra tokens.", None]
    return []


def progress_lines(st: dict) -> list:
    """"Bonus waiting: …" and the progress toward the next reward that beats
    what's held (none while +20% is held)."""
    lines = []
    if st["held"]:
        lines.append(f"Bonus waiting: {st['held']}% extra tokens on your next pack.")
    if st["next"]:
        at, pct = st["next"]["at"], st["next"]["pct"]
        step = BIG_TOKENS if st["held"] else STEP_TOKENS
        lines.append(f"{st['paid_spent'] - (at - step):,} of {step:,} paid tokens used "
                     f"toward a {pct}% bonus on your next pack.")
    return lines


def after_paying(db, order: dict) -> dict:
    """For the line after paying: {"bonus": n} and, when the card or PayPal
    account had already had its first-purchase bonus on this account's first
    purchase, "first_used": True."""
    if not order or order.get("status") not in ("completed", "refunded"):
        return {}
    bonus = int(order.get("bonus_tokens") or 0)
    if bonus or not order.get("payer_key"):
        return {"bonus": bonus}
    earlier = db.execute("SELECT 1 FROM ledger WHERE account_id = ? AND kind = 'purchase' AND ref <> ? LIMIT 1",
                         (order["account_id"], f"{order['provider']}:{order['order_id']}")).fetchone()
    used = not earlier and bool(db.execute("SELECT 1 FROM payments WHERE payer_key = ? AND bonus_rule = 'first' "
                                           "AND order_id <> ? LIMIT 1",
                                           (order["payer_key"], order["order_id"])).fetchone())
    return {"bonus": 0, **({"first_used": True} if used else {})}


def thanks(credited: int, d: dict) -> str:
    """The line after paying (the buy page's own script says the same)."""
    if d.get("bonus"):
        return f"Thank you — {credited:,} tokens added, plus {d['bonus']:,} bonus tokens."
    if d.get("first_used"):
        return (f"Thank you — {credited:,} tokens added. This card or PayPal account has already had its "
                "first-purchase bonus.")
    return f"Thank you — {credited:,} tokens added."
