"""SQL query functions backing the API (keeps routes thin).

Every analytical rollup that depends on optional data first checks the capability matrix,
so the API itself honors "capabilities flex with the data provided": margin / DSO fields
come back ``null`` when the underlying fields are absent.
"""

from __future__ import annotations

from .. import capabilities, db, schema


def get_summary(con) -> dict:
    n_customers = db.row_count(con, schema.CUSTOMERS)
    n_lifts = db.row_count(con, schema.LIFTS)
    terminals = [r[0] for r in con.execute(
        "SELECT DISTINCT terminal FROM lifts WHERE terminal IS NOT NULL ORDER BY 1").fetchall()]
    products = [r[0] for r in con.execute(
        "SELECT DISTINCT product FROM lifts WHERE product IS NOT NULL ORDER BY 1").fetchall()]
    rng = con.execute("SELECT min(lift_datetime), max(lift_datetime) FROM lifts").fetchone()
    total = con.execute("SELECT coalesce(sum(net_gallons), 0) FROM lifts").fetchone()[0]
    return {
        "connected": n_customers > 0,
        "customers": n_customers,
        "lifts": n_lifts,
        "terminals": terminals,
        "products": products,
        "date_range": {
            "start": str(rng[0].date()) if rng[0] else None,
            "end": str(rng[1].date()) if rng[1] else None,
        },
        "total_net_gallons": round(float(total), 1),
        "profile": db.get_meta(con, "profile", "empty"),
        "generated_at": db.get_meta(con, "generated_at"),
        "last_import": {
            "filename": db.get_meta(con, "last_import_filename"),
            "table": db.get_meta(con, "last_import_table"),
            "at": db.get_meta(con, "last_import_at"),
        },
        "quarantine_total": sum(db.quarantine_counts(con).values()),
        "crosswalk_total": len(db.get_crosswalk(con)),
    }


def get_schema(con) -> dict:
    presence = capabilities.field_presence(con)
    fields = []
    for f in schema.CANONICAL_FIELDS:
        p = presence.get(f.name, {})
        fields.append({
            "name": f.name, "table": f.table, "dtype": f.dtype.value,
            "required": f.required, "description": f.description,
            "present": p.get("present", False), "coverage": p.get("coverage", 0.0),
            "nonnull": p.get("nonnull", 0), "applicable": p.get("applicable", 0),
        })
    req = schema.required_field_names()
    opt = schema.optional_field_names()
    return {
        "fields": fields,
        "tables": schema.ALL_TABLES,
        "counts": {"required": len(req), "optional": len(opt), "total": len(schema.CANONICAL_FIELDS)},
    }


def get_customers(con) -> dict:
    caps = capabilities.compute_capabilities(con)
    enabled = {f["key"]: f["enabled"] for f in caps["features"]}
    margin_on = enabled.get("margin_analysis", False)
    dso_on = enabled.get("dso", False)

    base = con.execute("""
        SELECT c.customer_id, c.name, c.archetype, c.home_terminal,
               count(l.customer_id)              AS lift_count,
               coalesce(sum(l.net_gallons), 0)   AS total_net_gallons,
               coalesce(avg(l.net_gallons), 0)   AS avg_gallons_per_lift,
               max(l.lift_datetime)              AS last_lift
        FROM customers c
        LEFT JOIN lifts l USING (customer_id)
        GROUP BY 1, 2, 3, 4
        ORDER BY total_net_gallons DESC
    """).fetchall()

    margin_map, dso_map = {}, {}
    if margin_on:
        for cid, m in con.execute("""
            SELECT customer_id,
                   sum((unit_price - unit_cost) * net_gallons) / nullif(sum(net_gallons), 0)
            FROM lifts WHERE unit_price IS NOT NULL AND unit_cost IS NOT NULL
            GROUP BY 1
        """).fetchall():
            margin_map[cid] = m
    if dso_on:
        for cid, d in con.execute("""
            SELECT customer_id, avg(date_diff('day', invoice_date, paid_date))
            FROM invoices WHERE paid_date IS NOT NULL
            GROUP BY 1
        """).fetchall():
            dso_map[cid] = d

    out = []
    for r in base:
        cid = r[0]
        m = margin_map.get(cid)
        d = dso_map.get(cid)
        out.append({
            "customer_id": cid, "name": r[1], "archetype": r[2], "home_terminal": r[3],
            "lift_count": int(r[4]),
            "total_net_gallons": round(float(r[5]), 1),
            "avg_gallons_per_lift": round(float(r[6]), 1),
            "last_lift": str(r[7].date()) if r[7] else None,
            "avg_margin_per_gal": round(float(m), 4) if m is not None else None,
            "dso_days": round(float(d), 1) if d is not None else None,
        })
    return {"customers": out, "count": len(out), "margin_enabled": margin_on, "dso_enabled": dso_on}


def get_market_prices(con, product=None) -> dict:
    if db.row_count(con, schema.MARKET) == 0:
        return {"product": product, "available": False, "products": [], "points": []}
    products = [r[0] for r in con.execute(
        "SELECT DISTINCT product FROM market_prices ORDER BY 1").fetchall()]
    if product is None or product not in products:
        product = products[0]
    rows = con.execute("""
        SELECT price_date, avg(market_price), avg(nyh_basis), avg(street_rack)
        FROM market_prices WHERE product = ?
        GROUP BY price_date ORDER BY price_date
    """, [product]).fetchall()
    points = [{"date": str(r[0]), "market_price": round(float(r[1]), 4),
               "nyh_basis": round(float(r[2]), 4), "street_rack": round(float(r[3]), 4)}
              for r in rows]
    return {"product": product, "available": True, "products": products, "points": points}


def get_monthly_volume(con) -> dict:
    rows = con.execute("""
        SELECT strftime(lift_datetime, '%Y-%m') AS month, sum(net_gallons)
        FROM lifts GROUP BY 1 ORDER BY 1
    """).fetchall()
    return {"points": [{"month": r[0], "net_gallons": round(float(r[1]), 1)} for r in rows]}
