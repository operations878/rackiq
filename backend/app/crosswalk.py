"""Customer Master crosswalk — fuzzy entity resolution / de-duplication.

The single most important hygiene job: the same customer shows up under different
spellings and ids ("RIVERSIDE FUEL", "Riverside Fuel Dist", "riverside  fuel"). This module
clusters the distinct customer keys in an upload (optionally aided by a name column),
proposes *merge groups* with a confidence score, and — once the user confirms — persists the
variant → master decision in a **crosswalk** that survives every future upload. Applying the
crosswalk rewrites the customer key to its resolved master id, so every downstream metric
reads one canonical entity.

The matcher reuses Data Studio's string similarity (sequence ratio + substring bonus + token
overlap), so it behaves consistently with the column fuzzy-matcher.
"""

from __future__ import annotations

from .ingest import _norm, _similarity


def normalize(key: object) -> str:
    return _norm(key)


def similarity(a: str, b: str) -> float:
    return _similarity(_norm(a), _norm(b))


def _combined_similarity(ka: str, na: str | None, kb: str, nb: str | None) -> float:
    """Best similarity across the key and (optional) name signals of two variants."""
    signals_a = [s for s in (ka, na) if s]
    signals_b = [s for s in (kb, nb) if s]
    best = 0.0
    for sa in signals_a:
        for sb in signals_b:
            best = max(best, similarity(sa, sb))
    return best


# ---- Union-find -----------------------------------------------------------------
class _DSU:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


DEFAULT_THRESHOLD = 0.84


def propose(con, keys_with_counts: dict[str, int], names: dict[str, str] | None = None,
            threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Cluster the upload's distinct customer keys into proposed merge groups.

    ``keys_with_counts`` maps each distinct (stripped) customer key to its row count in the
    upload. ``names`` optionally maps a key to a display/customer-name string used as a
    second matching signal. Existing confirmed crosswalk entries pre-seed clusters and act
    as match anchors; 'rejected' keys are pinned as singletons (never re-proposed).
    """
    from . import db  # local import to avoid a cycle

    names = names or {}
    existing = db.get_crosswalk(con)  # variant_key -> {master_id, master_name, status, ...}
    confirmed = {k: v for k, v in existing.items() if v.get("status") == "confirmed"}
    rejected = {k for k, v in existing.items() if v.get("status") == "rejected"}

    keys = list(keys_with_counts.keys())
    dsu = _DSU()
    for k in keys:
        dsu.find(k)

    # Seed: confirmed variants of the same master belong together (anchor node per master).
    for vk, info in confirmed.items():
        mid = info.get("master_id") or vk
        dsu.union(f"master::{mid}", vk)

    # Edges between upload keys (skip rejected — they stay singletons / unmergeable).
    open_keys = [k for k in keys if k not in rejected]
    sims: dict[tuple[str, str], float] = {}
    for i in range(len(open_keys)):
        for j in range(i + 1, len(open_keys)):
            a, b = open_keys[i], open_keys[j]
            s = _combined_similarity(a, names.get(a), b, names.get(b))
            if s >= threshold:
                dsu.union(a, b)
                sims[(a, b)] = s

    # Edges from upload keys to existing confirmed variants (join an established master).
    for a in open_keys:
        for vk, info in confirmed.items():
            if vk == a:
                continue
            s = _combined_similarity(a, names.get(a), vk, info.get("master_name"))
            if s >= threshold:
                dsu.union(a, vk)
                sims[tuple(sorted((a, vk)))] = s

    # Gather components.
    comps: dict[str, list[str]] = {}
    for k in keys:
        comps.setdefault(dsu.find(k), []).append(k)
    # Also pull in confirmed variants whose root matches a component (for context).
    for vk in confirmed:
        root = dsu.find(vk)
        if root in comps and vk not in comps[root]:
            comps[root].append(vk)

    groups: list[dict] = []
    n_resolved = 0
    n_new_singletons = 0

    for root, members in comps.items():
        in_file = [m for m in members if m in keys_with_counts]
        # Decide the master for this component.
        master_id, master_name, master_source = _choose_master(members, confirmed, keys_with_counts, names)

        # How many in-file members already point to this exact master (no action needed)?
        already = [m for m in in_file
                   if confirmed.get(m, {}).get("master_id") == master_id]
        n_resolved += len(already)

        actionable_members = [m for m in in_file]
        if len(members) <= 1:
            # Brand-new, unmatched key → its own master, no decision needed.
            if not confirmed.get(members[0], {}).get("master_id"):
                n_new_singletons += 1
            continue
        # Skip groups where every in-file member is already confirmed to this master.
        if in_file and all(m in already for m in in_file):
            continue
        if not actionable_members:
            continue

        member_rows = []
        for m in members:
            sim_to_master = 1.0 if m == master_id else _combined_similarity(
                m, names.get(m), master_id, master_name)
            member_rows.append({
                "key": m,
                "name": names.get(m) or confirmed.get(m, {}).get("master_name") or m,
                "count": int(keys_with_counts.get(m, 0)),
                "in_file": m in keys_with_counts,
                "already_confirmed": confirmed.get(m, {}).get("master_id") == master_id,
                "similarity": round(sim_to_master, 3),
            })
        member_rows.sort(key=lambda r: (-r["count"], -r["similarity"], r["key"]))
        confidence = round(
            sum(r["similarity"] for r in member_rows) / len(member_rows), 3)

        groups.append({
            "group_id": master_id,
            "master_id": master_id,
            "master_name": master_name,
            "confidence": confidence,
            "from_existing": master_source == "existing",
            "members": member_rows,
        })

    groups.sort(key=lambda g: (-len(g["members"]), -g["confidence"]))

    return {
        "groups": groups,
        "n_distinct_keys": len(keys),
        "n_groups": len(groups),
        "n_resolved": n_resolved,
        "n_new_singletons": n_new_singletons,
        "threshold": threshold,
        "crosswalk_size": len(existing),
    }


def _choose_master(members: list[str], confirmed: dict, counts: dict[str, int],
                   names: dict[str, str]) -> tuple[str, str, str]:
    """Pick (master_id, master_name, source) for a component."""
    # Prefer an established master id already used by a confirmed member.
    for m in members:
        info = confirmed.get(m)
        if info and info.get("master_id"):
            return info["master_id"], info.get("master_name") or info["master_id"], "existing"
    # Otherwise choose the most-evidenced in-file variant (rows, then longest, then name).
    in_file = [m for m in members if m in counts]
    pool = in_file or members
    rep = sorted(pool, key=lambda m: (-int(counts.get(m, 0)), -len(str(m)), str(m)))[0]
    return rep, (names.get(rep) or rep), "new"


def confirm_groups(con, groups: list[dict], rejected_keys: list[str], now: str) -> dict:
    """Persist confirm/reject decisions into the crosswalk.

    ``groups`` is a list of {master_id, master_name, confidence?, members:[variant_key,...]}.
    Every member is written as a confirmed variant → master. ``rejected_keys`` are written as
    'rejected' singletons (mapped to themselves) so they are not re-proposed.
    """
    from . import db

    entries: list[dict] = []
    for g in groups:
        master_id = (g.get("master_id") or "").strip()
        if not master_id:
            continue
        master_name = (g.get("master_name") or master_id).strip()
        conf = g.get("confidence")
        for member in g.get("members", []):
            vk = member if isinstance(member, str) else member.get("key")
            if not vk:
                continue
            entries.append({
                "variant_key": str(vk).strip(),
                "master_id": master_id,
                "master_name": master_name,
                "confidence": conf,
                "status": "confirmed",
                "source": "manual",
                "updated_at": now,
            })
    for vk in rejected_keys or []:
        vk = str(vk).strip()
        if not vk:
            continue
        entries.append({
            "variant_key": vk, "master_id": vk, "master_name": vk,
            "confidence": None, "status": "rejected", "source": "manual", "updated_at": now,
        })
    n = db.upsert_crosswalk_entries(con, entries)
    return {"written": n, "crosswalk_size": len(db.get_crosswalk(con))}


def apply_to_frame(df, key_col: str, con) -> tuple[object, int, list[dict]]:
    """Rewrite ``key_col`` to its resolved master id using confirmed crosswalk entries.

    Returns (df, n_rows_remapped, rewrites) where ``rewrites`` lists the distinct
    variant→master substitutions performed (for the audit log).
    """
    from . import db

    if key_col not in df.columns:
        return df, 0, []
    confirmed = {k: v for k, v in db.get_crosswalk(con).items()
                 if v.get("status") == "confirmed" and v.get("master_id")}
    if not confirmed:
        return df, 0, []

    df = df.copy()
    rewrites: dict[tuple[str, str], int] = {}

    def _resolve(v):
        if v is None:
            return v
        key = str(v).strip()
        info = confirmed.get(key)
        if info and info["master_id"] != key:
            rewrites[(key, info["master_id"])] = rewrites.get((key, info["master_id"]), 0) + 1
            return info["master_id"]
        return v

    df[key_col] = df[key_col].map(_resolve)
    n_remapped = sum(rewrites.values())
    rewrite_list = [{"from": k[0], "to": k[1], "rows": n} for k, n in rewrites.items()]
    return df, n_remapped, rewrite_list


def master_names(con) -> dict[str, str]:
    """master_id -> master_name for confirmed crosswalk entries (seeds the customers dim)."""
    out: dict[str, str] = {}
    from . import db
    for vk, info in db.get_crosswalk(con).items():
        if info.get("status") == "confirmed" and info.get("master_id"):
            out.setdefault(info["master_id"], info.get("master_name") or info["master_id"])
    return out
