"""Generates Acme Robotics' analytics data (CSV) for the BI agent: customers, products, invoice
lines and the sales pipeline, January to September 2026.

    python3 seed/acme/analytics/generate.py

Deterministic (fixed seed). It agrees with the documents in seed/acme/corpus:
- invoice lines per month and product line add up, to the euro, to
  finance-reports/monthly-revenue-by-product-2026.xlsx (FleetOS 1,520 k€ in September, ...),
  and per quarter to the "Revenue by Product" sheet of q3-2026-financial-summary.xlsx (FleetOS
  30.3% of Q3 revenue); void lines exist and are not revenue
- Bluefin Freight is the largest customer, FleetOS at 2.3 M€ annual recurring revenue
- Nordlicht Grocers won in Q3 with 25 AMR-200 (first 10 delivered in September)
- Harbor Logistics lost on price; Kestrel Retail has an open proposal, 40 AMR-500 for 1.1 M€
- AMR-500 growth comes from heavy-duty deals closed in Q2 and delivered in Q3
- FleetOS grows every month; AMR-200 customers move to FleetOS Plus after the summer release
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "data"
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]
# finance-reports/monthly-revenue-by-product-2026.xlsx, thousands of euros
TOTALS = {
    "AMR-200": [1180, 1240, 1350, 1310, 1330, 1370, 1400, 1360, 1490],
    "AMR-500": [1490, 1530, 1740, 1690, 1730, 1780, 1810, 1830, 2010],
    "FleetOS": [1050, 1090, 1130, 1180, 1230, 1280, 1350, 1430, 1520],
}
DIEGO, DAN = "diego.alvarez@acme.test", "dan@acme.test"

PRODUCTS = [
    # product_id, name, product_line, list_price_eur, billing
    ("AMR-200", "AMR-200 compact robot", "AMR-200", 18500, "one-off"),
    ("AMR-500", "AMR-500 heavy-duty robot", "AMR-500", 27500, "one-off"),
    ("FLEETOS-STD", "FleetOS Standard (per robot, monthly)", "FleetOS", 95, "monthly"),
    ("FLEETOS-PLUS", "FleetOS Plus (per robot, monthly)", "FleetOS", 140, "monthly"),
]

# name, segment, region, country, owner, first invoice month (1-9; 0 = prospect), weights
# (AMR-200, AMR-500, FleetOS): the customer's usual share of each line's deliveries.
CUSTOMERS = [
    ("Bluefin Freight", "3PL", "Benelux", "NL", DIEGO, 1, (0.8, 2.6, 0)),  # FleetOS: fixed, below
    ("Polaris Parcel", "Parcel", "DACH", "DE", DIEGO, 1, (0.6, 1.2, 1.0)),
    ("Meridian Auto Parts", "Manufacturing", "DACH", "DE", DIEGO, 1, (0.3, 1.0, 0.9)),
    ("Ostara Pharma Logistics", "Healthcare", "Benelux", "BE", DIEGO, 1, (0.7, 0.8, 0.9)),
    ("Castell Home Retail", "Retail", "Iberia", "ES", DIEGO, 1, (1.0, 0.6, 0.8)),
    ("Northgate 3PL", "3PL", "UK & Ireland", "GB", DIEGO, 1, (0.6, 1.1, 1.1)),
    ("Rhine Valley Foods", "Grocery", "DACH", "DE", DIEGO, 1, (1.2, 0.4, 0.8)),
    ("Atlas Parcel Hub", "Parcel", "France", "FR", DIEGO, 1, (0.5, 0.9, 0.9)),
    ("Granite Home Improvement", "Retail", "UK & Ireland", "GB", DIEGO, 2, (0.9, 0.5, 0.7)),
    ("Corvid Industrial", "Manufacturing", "Nordics", "SE", DIEGO, 1, (0.4, 0.7, 0.6)),
    ("Fjordline Cold Chain", "Grocery", "Nordics", "NO", DAN, 1, (1.0, 0.3, 0.7)),
    ("Tagus Distribution", "3PL", "Iberia", "PT", DAN, 1, (0.9, 0.5, 0.7)),
    ("Alder & Finch Retail", "Retail", "UK & Ireland", "GB", DAN, 1, (1.1, 0.2, 0.6)),
    ("Lumen Electronics", "Manufacturing", "Benelux", "NL", DAN, 1, (0.8, 0.4, 0.6)),
    ("Brightwater Beverages", "Grocery", "France", "FR", DAN, 1, (0.9, 0.3, 0.6)),
    ("Pioneer Medical Supply", "Healthcare", "DACH", "AT", DAN, 1, (0.7, 0.3, 0.5)),
    ("Orchard Fresh", "Grocery", "Benelux", "NL", DAN, 3, (0.9, 0.2, 0.5)),
    ("Calder Fashion Logistics", "Retail", "UK & Ireland", "GB", DAN, 1, (0.8, 0.3, 0.5)),
    ("Sable Paper Products", "Manufacturing", "Nordics", "FI", DAN, 2, (0.6, 0.3, 0.4)),
    ("Juniper Books", "Retail", "DACH", "DE", DAN, 4, (0.7, 0.1, 0.4)),
    ("Vantage Spare Parts", "Manufacturing", "France", "FR", DAN, 1, (0.6, 0.4, 0.5)),
    ("Nordlicht Grocers", "Grocery", "Nordics", "DK", DAN, 9, (0, 0, 0)),  # Q3 win: below
    ("Kestrel Retail", "Retail", "Benelux", "BE", DAN, 0, (0, 0, 0)),  # prospect
    ("Harbor Logistics", "3PL", "UK & Ireland", "GB", DIEGO, 0, (0, 0, 0)),  # lost
]
# AMR-500 heavy-duty deliveries in Q3 from deals closed in Q2 (extra weight, July to September).
Q3_HEAVY_DUTY = {"Bluefin Freight": 2.2, "Polaris Parcel": 2.0, "Meridian Auto Parts": 1.6}
BLUEFIN_FLEETOS = [150_000, 154_000, 158_000, 163_000, 168_000, 174_000, 191_670, 191_670, 191_670]
NORDLICHT_SEPTEMBER = {"AMR-200": 185_000, "FleetOS": 2_400}  # first 10 of 25 robots


def split(total: int, weights: dict[str, float], rng: random.Random) -> dict[str, int]:
    """Whole euros per customer, summing exactly to total."""
    noisy = {c: w * rng.uniform(0.8, 1.2) for c, w in weights.items() if w > 0}
    scale = total / sum(noisy.values())
    amounts = {c: int(w * scale) for c, w in noisy.items()}
    largest = max(amounts, key=lambda c: amounts[c])
    amounts[largest] += total - sum(amounts.values())
    return amounts


def main() -> None:
    rng = random.Random(2026)
    OUT.mkdir(parents=True, exist_ok=True)
    ids = {c[0]: f"C{i:03d}" for i, c in enumerate(CUSTOMERS, start=1)}
    since = {c[0]: c[5] for c in CUSTOMERS}

    lines: list[dict[str, object]] = []
    invoice_no = 0

    def bill(customer: str, month: str, product: str, amount: int, status: str = "") -> None:
        nonlocal invoice_no
        invoice_no += 1
        price = next(p[3] for p in PRODUCTS if p[0] == product)
        monthly = product.startswith("FLEETOS")
        day = 28 if monthly else rng.randint(3, 26)
        issued = date.fromisoformat(f"{month}-{day:02d}")
        if not status:
            # Paid within 30-45 days; September and some late-August invoices are still open.
            status = "paid" if issued + timedelta(days=rng.randint(30, 45)) < date(2026, 10, 31) else "open"
        lines.append({
            "invoice_line_id": f"INV-{invoice_no:05d}",
            "invoice_date": issued.isoformat(),
            "customer_id": ids[customer],
            "product_id": product,
            "quantity": max(1, round(amount / (price * (1 if not monthly else 1)))) if not monthly else max(1, round(amount / price)),
            "amount_eur": amount,
            "status": status,
        })

    for m, month in enumerate(MONTHS, start=1):
        active = [c for c in CUSTOMERS if 0 < c[5] <= m]
        for line_index, line in enumerate(("AMR-200", "AMR-500", "FleetOS")):
            total = TOTALS[line][m - 1] * 1000
            if line == "FleetOS":
                total -= BLUEFIN_FLEETOS[m - 1]
                bill("Bluefin Freight", month, "FLEETOS-PLUS", BLUEFIN_FLEETOS[m - 1])
            if month == "2026-09" and line in NORDLICHT_SEPTEMBER:
                amount = NORDLICHT_SEPTEMBER[line]
                total -= amount
                bill("Nordlicht Grocers", month, "AMR-200" if line == "AMR-200" else "FLEETOS-STD", amount)
            weights: dict[str, float] = {}
            for name, *_rest, w in active:
                weight = w[line_index]
                if line == "AMR-500" and m >= 7:
                    weight += Q3_HEAVY_DUTY.get(name, 0)
                if line == "FleetOS":
                    weight *= 1 + 0.06 * (m - 1)  # installed base grows
                    if name == "Bluefin Freight":
                        weight = 0
                elif line == "AMR-500" and m >= 7 and name in Q3_HEAVY_DUTY:
                    pass  # the Q2 heavy-duty deals deliver every month of Q3
                elif rng.random() < 0.35:  # hardware is delivered in batches, not every month
                    weight = 0
                weights[name] = weight
            for customer, amount in split(total, weights, rng).items():
                if line == "FleetOS":
                    # After the summer release (July), AMR-200-heavy customers move to Plus.
                    plus = m >= 7 and CUSTOMERS[[c[0] for c in CUSTOMERS].index(customer)][6][0] >= 0.8
                    bill(customer, month, "FLEETOS-PLUS" if plus else "FLEETOS-STD", amount)
                elif amount > 120_000 and rng.random() < 0.5:  # two deliveries
                    first = int(amount * rng.uniform(0.35, 0.65))
                    bill(customer, month, line, first)
                    bill(customer, month, line, amount - first)
                else:
                    bill(customer, month, line, amount)
        # A few cancelled (void) invoices, re-issued correctly: never revenue.
        if m in (2, 5, 8):
            customer = rng.choice([c[0] for c in active if c[0] != "Bluefin Freight"])
            bill(customer, month, "AMR-200", rng.randint(30, 60) * 1000, status="void")

    # Check against the workbook before writing anything.
    line_of = {p[0]: p[2] for p in PRODUCTS}
    for m, month in enumerate(MONTHS, start=1):
        for line, totals in TOTALS.items():
            got = sum(
                int(r["amount_eur"])  # type: ignore[call-overload]
                for r in lines
                if str(r["invoice_date"]).startswith(month)
                and line_of[str(r["product_id"])] == line
                and r["status"] != "void"
            )
            assert got == totals[m - 1] * 1000, (month, line, got)

    def write(name: str, header: list[str], rows: list[dict[str, object]]) -> None:
        with (OUT / name).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)

    write(
        "customers.csv",
        ["customer_id", "name", "segment", "region", "country", "account_owner", "customer_since", "status"],
        [
            {
                "customer_id": ids[name], "name": name, "segment": segment, "region": region,
                "country": country, "account_owner": owner,
                "customer_since": f"{MONTHS[first - 1]}-01" if first else "",
                "status": "customer" if first else "prospect",
            }
            for name, segment, region, country, owner, first, _ in CUSTOMERS
        ],
    )  # fmt: skip
    write(
        "products.csv",
        ["product_id", "name", "product_line", "list_price_eur", "billing"],
        [dict(zip(["product_id", "name", "product_line", "list_price_eur", "billing"], p, strict=True)) for p in PRODUCTS],
    )  # fmt: skip
    write(
        "invoice_lines.csv",
        ["invoice_line_id", "invoice_date", "customer_id", "product_id", "quantity", "amount_eur", "status"],
        lines,
    )  # fmt: skip

    # Sales pipeline. Q3 closed only Nordlicht (won) and Harbor (lost), as in the win/loss notes.
    deals: list[dict[str, object]] = []

    def deal(customer: str, name: str, line: str, amount: int, stage: str, owner: str,
             created: str, close: str, reason: str = "") -> None:  # fmt: skip
        deals.append({
            "opportunity_id": f"OPP-{len(deals) + 1:03d}", "customer_id": ids[customer], "name": name,
            "product_line": line, "amount_eur": amount, "stage": stage, "owner": owner,
            "created_date": created, "close_date": close, "loss_reason": reason,
        })  # fmt: skip

    deal("Bluefin Freight", "Bluefin heavy-duty expansion", "AMR-500", 1_650_000, "won", DIEGO, "2026-02-10", "2026-05-29")
    deal("Polaris Parcel", "Polaris hub automation", "AMR-500", 1_320_000, "won", DIEGO, "2026-01-20", "2026-06-12")
    deal("Meridian Auto Parts", "Meridian line-side delivery", "AMR-500", 940_000, "won", DIEGO, "2026-03-02", "2026-06-25")
    deal("Nordlicht Grocers", "Nordlicht chilled DC (25 AMR-200)", "AMR-200", 462_500, "won", DAN, "2026-05-14", "2026-08-21")
    deal("Harbor Logistics", "Harbor Logistics AMR-500 fleet", "AMR-500", 1_480_000, "lost", DIEGO, "2026-04-08", "2026-09-17", "price")
    deal("Kestrel Retail", "Kestrel Antwerp pilot and rollout (40 AMR-500)", "AMR-500", 1_100_000, "proposal", DAN, "2026-08-04", "2026-12-15")
    for name, owner, first, _w in ((c[0], c[4], c[5], c[6]) for c in CUSTOMERS if 0 < c[5] < 9):
        if rng.random() < 0.55:
            line = rng.choice(["AMR-200", "AMR-500", "FleetOS"])
            amount = rng.randint(8, 60) * 10_000 if line != "FleetOS" else rng.randint(4, 18) * 10_000
            stage = rng.choice(["qualified", "proposal", "negotiation", "won", "lost"])
            created = date(2026, rng.randint(1, 6), rng.randint(1, 28))
            if stage in ("won", "lost"):  # closed before Q3, so the Q3 notes stay accurate
                close = date(2026, rng.randint(max(created.month, 1), 6), 28)
            else:
                close = date(2026, rng.randint(10, 12), rng.randint(1, 28))
            reason = rng.choice(["price", "timing", "feature gap", "competitor"]) if stage == "lost" else ""
            deal(name, f"{name.split()[0]} {line} {'expansion' if line != 'FleetOS' else 'upgrade'}", line, amount, stage, owner, created.isoformat(), close.isoformat(), reason)
    write(
        "opportunities.csv",
        ["opportunity_id", "customer_id", "name", "product_line", "amount_eur", "stage", "owner", "created_date", "close_date", "loss_reason"],
        deals,
    )  # fmt: skip
    revenue = sum(int(r["amount_eur"]) for r in lines if r["status"] != "void")  # type: ignore[call-overload]
    print(f"{len(lines)} invoice lines, {len(deals)} opportunities, revenue {revenue:,} EUR -> {OUT}")


if __name__ == "__main__":
    main()
