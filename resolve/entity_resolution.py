"""Stage 2: fuzzy-match addresses/phones/devices/IPs into canonical entity ids.

No LLM — every decision here is a deterministic string/number rule so the output is
reproducible and auditable. This module turns the messy raw attribute tables written by
``data/generate.py`` (addresses.csv, phones.csv, devices.csv, cards.csv, ips.csv) into
canonical entity ids: rows that refer to "the same real-world thing" collapse onto one id,
so Stage 3 (``graph/build.py``) can draw a shared-attribute edge between two customers by
just comparing canonical ids instead of raw strings.

--------------------------------------------------------------------------
STRATEGY PER ATTRIBUTE TYPE
--------------------------------------------------------------------------
address   Normalize text (lowercase, expand abbreviations like "Apt"/"Flat" to one
          token, strip landmark clauses and punctuation noise), then fuzzy-match with
          rapidfuzz token-based similarity. To keep this both fast (no O(n^2) over the
          whole table) and *precise* (the actual goal here), candidates are only ever
          compared within the same (city, pincode) block -- two addresses in different
          cities or different pincodes are never merged, no matter how similar the text
          looks. Within a block, pairs above a high similarity threshold are unioned via
          union-find so near-duplicate clusters of size > 2 collapse transitively.
phone     Parsed with ``phonenumbers`` (default region IN, since the synthetic numbers
          are Indian mobiles with/without +91, spaces, hyphens, or a leading 0) and
          normalized to E.164. Identical E.164 -> identical canonical id.
device    Normalized (trim/lowercase) exact match. Device fingerprints are opaque
          tokens in this dataset -- fuzzy-matching them would just invite accidental
          collisions between unrelated devices, so this is intentionally exact.
card      Same treatment as device: normalized exact match (masked PAN or UPI VPA).
ip        Bucketed into /24 subnets (first three octets of the IPv4 address). This is
          a deliberately weak/coarse signal -- see edge_weights.ip_subnet in
          config.yaml -- since many unrelated people can share a /24.

--------------------------------------------------------------------------
WHY BLOCK BY (city, pincode) FOR ADDRESSES
--------------------------------------------------------------------------
Fuzzy text similarity alone over-merges: "Apt 4B, MG Road" in Bengaluru and an
unrelated "Apt 4B, MG Road" in a different city/pincode would score high on pure token
similarity but are obviously not the same place. Blocking by (city, pincode) first, and
only fuzzy-matching *within* a block, keeps recall on genuine near-duplicates (typos,
"Apt" vs "Apartment", added landmark text -- all of which happen inside one pincode)
while refusing to merge across neighborhoods/cities on text similarity alone. This is
the main lever for precision described in PLAN.md Stage 2's exit test.

--------------------------------------------------------------------------
OUTPUT
--------------------------------------------------------------------------
Writes CSVs under ``data/resolved/``, one per attribute type, each mapping the
original row id to a canonical entity id plus the customer_id it belongs to (the join
key Stage 3 needs to attach edges to customer nodes):
  resolved_addresses.csv  address_id, customer_id, address_entity_id, normalized_text
  resolved_phones.csv     phone_id, customer_id, phone_entity_id, e164
                    (read this CSV with dtype=str -- pandas will otherwise infer
                    e164 as int64 and silently drop the leading "+", corrupting
                    the number; phone_entity_id is the safe join key regardless)
  resolved_devices.csv    device_row_id, customer_id, device_entity_id
  resolved_cards.csv      card_row_id, customer_id, card_entity_id
  resolved_ips.csv        ip_id, customer_id, ip_entity_id, subnet_24

Run: ``python resolve/entity_resolution.py`` (or ``make resolve``) from the repo root.
Reads ``random_seed``-free deterministic input from ``data/*.csv`` (Stage 1 output) and
config thresholds from the ``entity_resolution`` block of ``config.yaml`` if present,
else built-in defaults. Prints a self-check: a handful of merged near-duplicate address
pairs and a handful of distinct pairs that were correctly kept apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import phonenumbers
import yaml
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = DATA_DIR / "resolved"
CONFIG_PATH = ROOT / "config.yaml"

DEFAULT_PHONE_REGION = "IN"
DEFAULT_ADDRESS_SIMILARITY_THRESHOLD = 88.0

# Tokens that carry no identity signal for an address match -- landmarks describe
# *nearby* things, not the address itself, and free-standing punctuation/spacing
# differences are exactly the messiness Stage 1 injects on purpose.
_LANDMARK_RE = re.compile(
    r"\b(near|opp\.?|opposite|behind|next to)\b.*$", flags=re.IGNORECASE
)
_PUNCT_RE = re.compile(r"[.,]")
_WHITESPACE_RE = re.compile(r"\s+")

# Normalize every spelling variant of an apartment/flat designator to one token so
# "Apt", "Apartment", "Flat", "Flat No.", "Apt No." all compare equal. Order matters:
# longer phrases first so "flat no" collapses before the bare "flat" rule would fire.
# Includes single-character-typo variants ("flt", "apratment") since Stage 1 injects
# exactly that kind of noise on ~3% of address strings.
_ABBREV_MAP = {
    "apartment": "apt",
    "apratment": "apt",
    "aprtment": "apt",
    "apt no": "apt",
    "flat no": "apt",
    "flt no": "apt",
    "flat": "apt",
    "flt": "apt",
}

# The room/unit/flat number (e.g. "1A-7", "2B-11", "Room 101") is the single strongest
# distinguishing token in an address -- two listings on the same street with the same
# building name but a *different* unit number are different homes (e.g. a hostel's
# "Room 101" vs "Room 106"), no matter how similar the surrounding text reads. Pull it
# out so it can be required to match (independent of the general fuzzy text score)
# before two addresses are ever allowed to merge.
_UNIT_RE = re.compile(r"\b(\d{1,4}[a-z]?-\d{1,3}|\d{2,4})\b")


# ---------------------------------------------------------------------------
# Union-Find (disjoint set) -- used to transitively merge near-duplicate addresses
# ---------------------------------------------------------------------------

class UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


# ---------------------------------------------------------------------------
# Address normalization + fuzzy matching
# ---------------------------------------------------------------------------

def normalize_address_text(line1: str, line2: str) -> str:
    """Collapse formatting noise so near-duplicate addresses compare equal-ish."""
    text = f"{line1} {line2}".lower()
    text = _LANDMARK_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    for old, new in _ABBREV_MAP.items():
        text = text.replace(old, new)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def extract_unit_token(line1: str) -> str | None:
    """Pull the room/flat/unit number out of an address line, e.g. "1A-7" out of
    "Apt 1A-7, Kumar Residency" or "101" out of "Room 101, Gupta Nagar". Returns
    None if no such token is found (nothing to gate on -- falls back to text-only
    comparison)."""
    match = _UNIT_RE.search(line1.lower())
    return match.group(1) if match else None


def _units_compatible(unit_a: str | None, unit_b: str | None) -> bool:
    """Two addresses are only mergeable if their unit tokens match, or one/both
    couldn't be extracted (nothing to contradict on) -- but never when both were
    extracted and differ. A single-character digit typo (Stage 1's `_typo` helper)
    is tolerated via a high fuzzy-ratio check on the token itself.

    Note: a missing token round-trips through a pandas column as float NaN, not
    Python None (``pd.Series.apply`` on an all-object column with some ``None``
    values still boxes them as NaN), so this must check with ``pd.isna`` rather
    than ``is None`` -- otherwise a NaN silently falls through to ``fuzz.ratio``
    and produces a bogus low score, incorrectly blocking a real merge.
    """
    if pd.isna(unit_a) or pd.isna(unit_b):
        return True
    if unit_a == unit_b:
        return True
    return fuzz.ratio(unit_a, unit_b) >= 80.0


def resolve_addresses(
    df: pd.DataFrame, threshold: float = DEFAULT_ADDRESS_SIMILARITY_THRESHOLD
) -> tuple[pd.DataFrame, list[tuple[str, str, float]]]:
    """Fuzzy-match addresses into canonical entity ids.

    Blocks by (city, pincode) so fuzzy matching only ever compares addresses that
    are already known to be in the same postal area -- see module docstring. Within
    a block, every pair is scored with rapidfuzz's token_sort_ratio (order-independent
    token comparison, robust to "Apt 4B, MG Road" vs "MG Road, Apt 4B" reordering).
    A pair is only unioned when it clears ``threshold`` AND its extracted unit/room/
    flat numbers are compatible (see ``_units_compatible``) -- without that gate,
    two different rooms in the same hostel ("Room 101, Gupta Nagar" vs "Room 106,
    Gupta Nagar") score high on surrounding-text similarity alone and would
    incorrectly merge, which is exactly the over-merge failure mode to avoid.

    Returns the resolved dataframe plus a log of (id_a, id_b, score) for every pair
    that was merged, for the self-check report.
    """
    df = df.copy()
    df["normalized_text"] = [
        normalize_address_text(r.line1, r.line2) for r in df.itertuples()
    ]
    df["unit_token"] = df["line1"].apply(extract_unit_token)

    uf = UnionFind()
    merge_log: list[tuple[str, str, float]] = []

    block_cols = ["city", "pincode"]
    for _, block in df.groupby(block_cols, sort=False):
        ids = block["address_id"].tolist()
        texts = block["normalized_text"].tolist()
        units = block["unit_token"].tolist()
        n = len(ids)
        for i in range(n):
            for j in range(i + 1, n):
                if uf.find(ids[i]) == uf.find(ids[j]):
                    continue  # already merged transitively, skip rescoring
                if not _units_compatible(units[i], units[j]):
                    continue
                score = fuzz.token_sort_ratio(texts[i], texts[j])
                if score >= threshold:
                    uf.union(ids[i], ids[j])
                    merge_log.append((ids[i], ids[j], score))

    # Canonical id = a short hash of the union-find root, stable across reruns.
    roots = {addr_id: uf.find(addr_id) for addr_id in df["address_id"]}
    root_to_entity = {
        root: f"ADDR_ENT{idx:06d}"
        for idx, root in enumerate(sorted(set(roots.values())), start=1)
    }
    df["address_entity_id"] = df["address_id"].map(roots).map(root_to_entity)
    return df[["address_id", "customer_id", "address_entity_id", "normalized_text"]], merge_log


# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------

def normalize_phone(raw_number: str, region: str = DEFAULT_PHONE_REGION) -> str | None:
    """Parse a messy raw phone string into E.164. Returns None if unparseable."""
    try:
        parsed = phonenumbers.parse(raw_number, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def resolve_phones(df: pd.DataFrame, region: str = DEFAULT_PHONE_REGION) -> pd.DataFrame:
    df = df.copy()
    df["e164"] = df["raw_number"].apply(lambda n: normalize_phone(n, region))
    # Malformed numbers isolate to their own singleton entity (id derived from the row
    # itself) rather than being dropped -- Stage 1's failure-handling rule (PLAN.md
    # §10): bad rows isolate, the pipeline continues.
    fallback = "UNRESOLVED_" + df["phone_id"]
    key = df["e164"].fillna(fallback)
    entity_ids = {
        k: f"PHONE_ENT{idx:06d}" for idx, k in enumerate(sorted(key.unique()), start=1)
    }
    df["phone_entity_id"] = key.map(entity_ids)
    return df[["phone_id", "customer_id", "phone_entity_id", "e164"]]


# ---------------------------------------------------------------------------
# Device / card: normalized exact match
# ---------------------------------------------------------------------------

def _normalize_token(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def resolve_devices(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    key = df["device_id"].apply(_normalize_token)
    entity_ids = {
        k: f"DEVICE_ENT{idx:06d}" for idx, k in enumerate(sorted(key.unique()), start=1)
    }
    df["device_entity_id"] = key.map(entity_ids)
    return df[["device_row_id", "customer_id", "device_entity_id"]]


def resolve_cards(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    key = df["card_id"].apply(_normalize_token)
    entity_ids = {
        k: f"CARD_ENT{idx:06d}" for idx, k in enumerate(sorted(key.unique()), start=1)
    }
    df["card_entity_id"] = key.map(entity_ids)
    return df[["card_row_id", "customer_id", "card_entity_id"]]


# ---------------------------------------------------------------------------
# IP -> /24 subnet bucketing
# ---------------------------------------------------------------------------

def to_subnet_24(ip_address: str) -> str | None:
    parts = str(ip_address).split(".")
    if len(parts) != 4:
        return None
    return ".".join(parts[:3])


def resolve_ips(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Trust the ip_address column, not the pre-computed subnet_24 column -- deriving
    # it ourselves means this module has no hidden dependency on Stage 1's internals
    # and still works if fed a raw ip_address from anywhere else.
    subnet = df["ip_address"].apply(to_subnet_24)
    fallback = "UNRESOLVED_" + df["ip_id"]
    key = subnet.fillna(fallback)
    entity_ids = {
        k: f"IP_ENT{idx:06d}" for idx, k in enumerate(sorted(key.unique()), start=1)
    }
    df["ip_entity_id"] = key.map(entity_ids)
    df["subnet_24"] = subnet
    return df[["ip_id", "customer_id", "ip_entity_id", "subnet_24"]]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class ResolutionResult:
    addresses: pd.DataFrame
    phones: pd.DataFrame
    devices: pd.DataFrame
    cards: pd.DataFrame
    ips: pd.DataFrame
    address_merge_log: list[tuple[str, str, float]] = field(default_factory=list)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def run(data_dir: Path = DATA_DIR) -> ResolutionResult:
    config = load_config()
    er_cfg = config.get("entity_resolution", {}) or {}
    address_threshold = er_cfg.get(
        "address_similarity_threshold", DEFAULT_ADDRESS_SIMILARITY_THRESHOLD
    )
    phone_region = er_cfg.get("phone_default_region", DEFAULT_PHONE_REGION)

    addresses_df = pd.read_csv(data_dir / "addresses.csv", dtype=str)
    phones_df = pd.read_csv(data_dir / "phones.csv", dtype=str)
    devices_df = pd.read_csv(data_dir / "devices.csv", dtype=str)
    cards_df = pd.read_csv(data_dir / "cards.csv", dtype=str)
    ips_df = pd.read_csv(data_dir / "ips.csv", dtype=str)

    resolved_addresses, merge_log = resolve_addresses(addresses_df, threshold=address_threshold)
    resolved_phones = resolve_phones(phones_df, region=phone_region)
    resolved_devices = resolve_devices(devices_df)
    resolved_cards = resolve_cards(cards_df)
    resolved_ips = resolve_ips(ips_df)

    return ResolutionResult(
        addresses=resolved_addresses,
        phones=resolved_phones,
        devices=resolved_devices,
        cards=resolved_cards,
        ips=resolved_ips,
        address_merge_log=merge_log,
    )


def write_csvs(result: ResolutionResult, out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    result.addresses.to_csv(out_dir / "resolved_addresses.csv", index=False)
    result.phones.to_csv(out_dir / "resolved_phones.csv", index=False)
    result.devices.to_csv(out_dir / "resolved_devices.csv", index=False)
    result.cards.to_csv(out_dir / "resolved_cards.csv", index=False)
    result.ips.to_csv(out_dir / "resolved_ips.csv", index=False)


# ---------------------------------------------------------------------------
# Self-check: print merged near-duplicate pairs + distinct pairs kept separate
# ---------------------------------------------------------------------------

def print_self_check(result: ResolutionResult, addresses_df: pd.DataFrame, n: int = 5) -> None:
    """Print the pairs that matter most for judging precision: near-duplicates that
    were genuinely *fuzzy* (score < 100, i.e. text actually differed) and got merged,
    plus the closest-scoring distinct pairs that were correctly kept apart -- these
    are the hardest negatives, so they're the most convincing evidence against
    over-merging."""
    text_by_id = dict(zip(addresses_df["address_id"], addresses_df.apply(
        lambda r: f"{r.line1} | {r.line2} | {r.city} {r.pincode}", axis=1
    )))

    print("=" * 72)
    print("Stage 2 — entity resolution self-check")
    print("=" * 72)

    n_raw = len(result.addresses)
    n_entities = result.addresses["address_entity_id"].nunique()
    print(f"Addresses: {n_raw} raw rows -> {n_entities} canonical entities "
          f"({n_raw - n_entities} merged away)")
    print()

    fuzzy_merges = [m for m in result.address_merge_log if m[2] < 100.0]
    exact_merges = [m for m in result.address_merge_log if m[2] >= 100.0]
    print(f"-- {min(n, len(fuzzy_merges))} example MERGED near-duplicate pairs "
          f"(fuzzy match, text actually differs) --")
    if not fuzzy_merges:
        print("  (none in this run -- all merges were exact-text duplicates)")
    for a, b, score in sorted(fuzzy_merges, key=lambda t: t[2])[:n]:
        print(f"  [{score:5.1f}] {a}: {text_by_id[a]}")
        print(f"          {b}: {text_by_id[b]}")
    print(f"  ({len(exact_merges)} additional merges were exact-text duplicates, not shown)")

    print()
    print(f"-- {n} closest-scoring DISTINCT pairs kept separate (hardest negatives -- "
          f"same city+pincode block, highest similarity score below the "
          f"{DEFAULT_ADDRESS_SIMILARITY_THRESHOLD} threshold) --")
    entity_by_id = dict(zip(result.addresses["address_id"], result.addresses["address_entity_id"]))
    text_by_addr_id = dict(zip(result.addresses["address_id"], result.addresses["normalized_text"]))
    near_misses: list[tuple[str, str, float]] = []
    for _, block in addresses_df.groupby(["city", "pincode"], sort=False):
        ids = block["address_id"].tolist()
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if entity_by_id[a] != entity_by_id[b]:
                    score = fuzz.token_sort_ratio(text_by_addr_id[a], text_by_addr_id[b])
                    near_misses.append((a, b, score))
    for a, b, score in sorted(near_misses, key=lambda t: -t[2])[:n]:
        print(f"  [{score:5.1f}] {a}: {text_by_id[a]}")
        print(f"          {b}: {text_by_id[b]}")
    if not near_misses:
        print("  (no same-block distinct-entity pairs found in this dataset)")

    print()
    print(f"Phones:  {len(result.phones)} raw rows -> "
          f"{result.phones['phone_entity_id'].nunique()} canonical entities")
    print(f"Devices: {len(result.devices)} raw rows -> "
          f"{result.devices['device_entity_id'].nunique()} canonical entities")
    print(f"Cards:   {len(result.cards)} raw rows -> "
          f"{result.cards['card_entity_id'].nunique()} canonical entities")
    print(f"IPs:     {len(result.ips)} raw rows -> "
          f"{result.ips['ip_entity_id'].nunique()} canonical /24 subnet entities")
    print("=" * 72)


def main() -> None:
    addresses_df = pd.read_csv(DATA_DIR / "addresses.csv", dtype=str)
    result = run()
    write_csvs(result)
    print_self_check(result, addresses_df)
    print(f"\nResolved CSVs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
