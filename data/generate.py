"""Stage 1: generate synthetic customers, orders, events, and ground truth
(legit + fraud rings + benign look-alikes).

Run: ``python data/generate.py`` (or ``make data``) from the repo root. Reads
``random_seed`` and the ``data_gen`` block from ``config.yaml``; writes CSVs into
``data/``. Every run with the same seed is byte-for-byte identical.

--------------------------------------------------------------------------
WHY THIS FILE EXISTS / WHAT MAKES THE METRICS TRUSTWORTHY
--------------------------------------------------------------------------
Every later stage (entity resolution, graph, community detection, ring
scoring, the agent) is graded against ``ground_truth.csv``. If this generator
is either (a) too easy — rings are the only clusters that share attributes —
or (b) too hard — benign clusters are indistinguishable from rings on every
signal — the whole project's precision/recall numbers are meaningless. So
this module is deliberately built around one design rule:

    Attribute-sharing (same device/address/card/IP/pincode) is NOT the
    fraud signal. Attribute-sharing + coordinated BEHAVIOR (loss-causing
    events clustered in a burst, on freshly-created accounts) is the
    fraud signal.

Concretely:
  * Benign clusters (families, an office IP block, a hostel pincode, a
    couple sharing a card) share high-signal attributes -- sometimes even a
    device or a card, exactly like a ring would -- but their event rates
    (returns/chargebacks/COD-refusals) are drawn from the SAME low baseline
    distribution as an ordinary independent legit customer, and their
    account-creation dates are spread out normally, not bursted.
  * Fraud rings share high-signal attributes AND have event rates drawn
    from an elevated distribution AND were created in a tight burst window.
    Some rings additionally farm one promo code.
  * Every legit account (whether or not it belongs to a benign cluster) is
    labeled "legit" in ground_truth.csv. Only ring accounts get a ring_id.
    This means a detector that flags "shares a device" alone will produce
    visible false positives on the office/family/hostel clusters -- which
    is exactly the trap Stage 5+ has to avoid, by design.

--------------------------------------------------------------------------
ENTITIES / CSVs EMITTED (all under data/)
--------------------------------------------------------------------------
  customers.csv    customer_id, created_at, ring_id (blank if legit), cluster_tag
  devices.csv      device_id, customer_id  (an account can reuse a device_id
                    row-per-customer -- the same device_id string appearing
                    under multiple customer_id rows IS the shared-device signal)
  addresses.csv    address_id, customer_id, line1, line2, city, state, pincode
                    (line1/line2 carry realistic messiness: abbreviations,
                    typos, "Apt" vs "Apartment", extra landmark text)
  cards.csv        card_id, customer_id, instrument_type (card|upi), masked
  phones.csv       phone_id, customer_id, raw_number (messy formatting)
  ips.csv          ip_id, customer_id, ip_address, subnet_24
  orders.csv       order_id, customer_id, order_date, amount, promo_code,
                    payment_status
  events.csv       event_id, order_id, customer_id, event_type
                    (return|chargeback|cod_refusal), event_date
  ground_truth.csv account_id, label   (label = ring_id string, or "legit")

customer_id is the join key across every file (an "account" == a customer
here; Stage 1 does not model multiple accounts per person -- that emergent
identity linkage is exactly what Stages 2-3 are supposed to discover from
the shared device/address/card/phone/ip attributes).

--------------------------------------------------------------------------
BENIGN LOOK-ALIKE CLUSTERS (the honesty traps -- all label "legit")
--------------------------------------------------------------------------
  * families:      3-6 customers share one address (and ~40% of families
                    also share one device_id -- e.g. a shared home tablet).
                    Surnames match; phone numbers differ.
  * office cluster: ~30 customers share an IP /24 subnet (their session_ip
                    all falls in the same office building's block) but have
                    distinct addresses/devices/cards scattered across the city.
  * hostel/PG:      many customers share one pincode (a hostel's postal code)
                    but distinct addresses (different room lines), devices,
                    cards.
  * a couple:       2 customers share one card_id (joint account) but have
                    distinct addresses/devices/phones... no wait, a couple
                    plausibly shares an address too, so they do -- but only
                    2 people, low volume, baseline event rates.

--------------------------------------------------------------------------
FRAUD RINGS (12-18, count/sizes from config.yaml data_gen block)
--------------------------------------------------------------------------
Each ring is a set of ring_size_min..ring_size_max "distinct" customer
accounts that:
  1. share >=1 high-signal attribute across most members: either one
     device_id, one card_id, or a near-duplicate address (same building,
     text-perturbed line1/line2 -- entity resolution must fuzzy-match
     these back together in Stage 2),
  2. were created within a short burst window (1-10 days apart), unlike
     the uniformly-spread creation dates of legit accounts,
  3. have events (return/chargeback/cod_refusal) drawn from an ELEVATED
     rate, concentrated in a short window after order placement (a burst
     of loss-causing events, not spread over months),
  4. ~50% of rings additionally farm a single shared promo_code across
     most of their orders (promo/refund farming concentration).

--------------------------------------------------------------------------
MESSINESS INJECTED (so Stage 2 entity resolution has real work to do)
--------------------------------------------------------------------------
  * addresses: line1 randomly rewritten with abbreviation swaps (Apartment
    <-> Apt <-> Flat), random extra landmark text ("near XYZ mall"),
    building/floor typos (transposed digits, dropped letters), inconsistent
    comma/spacing.
  * phones: some numbers formatted with +91, spaces, hyphens, leading 0,
    or missing the country code -- same underlying 10-digit number.
  * general: ~3% chance any given text field gets a single-character typo
    (swap/drop/duplicate) via a shared `_typo` helper, applied independently
    of the ring/benign logic above so it does not correlate with fraud.

Everything is driven by Python's ``random`` module seeded once from
config.yaml's ``random_seed`` -- no other source of randomness is used, so
runs are fully reproducible.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"

SIM_START = datetime(2024, 1, 1)
SIM_END = datetime(2025, 12, 31)

# ---------------------------------------------------------------------------
# Reference data for realistic Indian names/addresses
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna",
    "Ishaan", "Rohan", "Kabir", "Aryan", "Dhruv", "Karthik", "Nikhil", "Rahul",
    "Saanvi", "Ananya", "Diya", "Aadhya", "Myra", "Pari", "Anika", "Ira",
    "Kavya", "Riya", "Priya", "Sneha", "Neha", "Pooja", "Divya", "Meera",
    "Rajesh", "Suresh", "Ramesh", "Vikram", "Sanjay", "Amit", "Deepak", "Manoj",
    "Sunita", "Anita", "Kavita", "Lakshmi", "Geeta", "Rekha", "Shalini", "Nisha",
]

LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Kumar", "Singh", "Patel", "Reddy", "Rao",
    "Nair", "Iyer", "Menon", "Pillai", "Chatterjee", "Banerjee", "Mukherjee",
    "Das", "Bose", "Joshi", "Desai", "Shah", "Mehta", "Agarwal", "Bhatt",
    "Chauhan", "Yadav", "Mishra", "Pandey", "Tiwari", "Naidu", "Krishnan",
]

CITY_STATE_PINCODE = [
    ("Bengaluru", "Karnataka", "5600"),
    ("Mumbai", "Maharashtra", "4000"),
    ("Pune", "Maharashtra", "4110"),
    ("Delhi", "Delhi", "1100"),
    ("Hyderabad", "Telangana", "5000"),
    ("Chennai", "Tamil Nadu", "6000"),
    ("Kolkata", "West Bengal", "7000"),
    ("Ahmedabad", "Gujarat", "3800"),
    ("Jaipur", "Rajasthan", "3020"),
    ("Lucknow", "Uttar Pradesh", "2260"),
]

STREET_NAMES = [
    "MG Road", "Church Street", "Brigade Road", "Residency Road", "Park Street",
    "Linking Road", "SV Road", "FC Road", "Camp Road", "Anna Salai",
    "Jubilee Hills Road", "Banjara Hills Road", "Sector 15 Main Road",
    "Ring Road", "Station Road", "Gandhi Nagar Main Road", "Model Colony Road",
    "Koramangala 5th Block", "Indiranagar 100 Feet Road", "Andheri West Road",
]

BUILDING_WORDS = ["Apartments", "Residency", "Towers", "Heights", "Enclave", "Nagar", "Vihar", "Society"]

APARTMENT_VARIANTS = ["Apt", "Apartment", "Flat", "Flat No.", "Apt No."]

DEVICE_OS = ["android", "ios", "web"]

PROMO_CODES = ["WELCOME50", "FESTIVE20", "FIRST100", "SAVE30", "MEGA25"]

EVENT_TYPES = ["return", "chargeback", "cod_refusal"]


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------

def _typo(text: str, rng: random.Random, p: float = 0.03) -> str:
    """With probability p, introduce one small character-level typo."""
    if rng.random() >= p or len(text) < 4:
        return text
    i = rng.randrange(1, len(text) - 1)
    kind = rng.choice(["swap", "drop", "dup"])
    if kind == "swap" and i + 1 < len(text):
        chars = list(text)
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
        return "".join(chars)
    if kind == "drop":
        return text[:i] + text[i + 1:]
    return text[:i] + text[i] + text[i:]


def _random_date(rng: random.Random, start: datetime, end: datetime) -> datetime:
    delta = end - start
    seconds = rng.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=seconds)


def _gen_name(rng: random.Random) -> tuple[str, str]:
    return rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)


def _gen_device_id(rng: random.Random) -> str:
    os_ = rng.choice(DEVICE_OS)
    token = "".join(rng.choices(string.hexdigits.lower(), k=16))
    return f"dev-{os_}-{token}"


def _gen_card_id(rng: random.Random) -> str:
    if rng.random() < 0.5:
        last4 = "".join(rng.choices(string.digits, k=4))
        return f"card-**** **** **** {last4}"
    handle = "".join(rng.choices(string.ascii_lowercase + string.digits, k=8))
    bank = rng.choice(["okaxis", "oksbi", "okhdfc", "ybl", "paytm"])
    return f"upi-{handle}@{bank}"


def _gen_phone(rng: random.Random) -> str:
    """Return a 'clean' canonical 10-digit Indian mobile number string."""
    first = rng.choice("6789")
    rest = "".join(rng.choices(string.digits, k=9))
    return first + rest


def _messify_phone(number: str, rng: random.Random) -> str:
    """Reformat a clean 10-digit number with realistic messy variants."""
    style = rng.choice(["plain", "plus91", "spaced", "hyphen", "zero_prefix", "plus91_spaced"])
    if style == "plain":
        return number
    if style == "plus91":
        return f"+91{number}"
    if style == "spaced":
        return f"{number[:5]} {number[5:]}"
    if style == "hyphen":
        return f"{number[:5]}-{number[5:]}"
    if style == "zero_prefix":
        return f"0{number}"
    return f"+91 {number[:5]} {number[5:]}"


def _gen_ip(rng: random.Random, subnet: str | None = None) -> tuple[str, str]:
    if subnet is None:
        subnet = f"{rng.randint(10, 223)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}"
    last = rng.randint(1, 254)
    return f"{subnet}.{last}", subnet


def _gen_building(rng: random.Random) -> str:
    return f"{rng.choice(LAST_NAMES)} {rng.choice(BUILDING_WORDS)}"


def _gen_address_lines(rng: random.Random, building: str | None = None,
                        street: str | None = None) -> tuple[str, str]:
    """Return (line1, line2) with realistic messiness applied to line1."""
    unit = rng.randint(1, 12)
    floor = rng.randint(1, 9)
    apt_word = rng.choice(APARTMENT_VARIANTS)
    building = building or _gen_building(rng)
    street = street or rng.choice(STREET_NAMES)
    line1 = f"{apt_word} {floor}{rng.choice(['A', 'B', 'C', ''])}-{unit}, {building}"
    if rng.random() < 0.35:
        landmark = rng.choice(["near Metro Station", "opp City Mall", "behind Big Bazaar",
                                "next to HDFC Bank", "near Community Park"])
        line1 = f"{line1}, {landmark}"
    line1 = _typo(line1, rng, p=0.05)
    line2 = street
    return line1, line2


def _near_duplicate_address(line1: str, line2: str, rng: random.Random) -> tuple[str, str]:
    """Perturb an existing address's text while keeping it the 'same place' --
    the realistic messiness a fraud ring's near-duplicate addresses need,
    for Stage 2 entity resolution to fuzzy-match back together."""
    swaps = {
        "Apartment": "Apt", "Apt": "Apartment", "Flat": "Flat No.",
        "Flat No.": "Flat",
    }
    for old, new in swaps.items():
        if old in line1:
            line1 = line1.replace(old, new, 1)
            break
    line1 = _typo(line1, rng, p=0.15)
    if rng.random() < 0.3:
        line1 = line1.replace(", ", " ", 1)
    return line1, line2


# ---------------------------------------------------------------------------
# Data classes for the rows we accumulate
# ---------------------------------------------------------------------------

@dataclass
class Registry:
    customers: list[dict] = field(default_factory=list)
    devices: list[dict] = field(default_factory=list)
    addresses: list[dict] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    phones: list[dict] = field(default_factory=list)
    ips: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    ground_truth: list[dict] = field(default_factory=list)

    _customer_seq: int = 0
    _order_seq: int = 0
    _event_seq: int = 0
    _device_row_seq: int = 0
    _addr_seq: int = 0
    _card_row_seq: int = 0
    _phone_seq: int = 0
    _ip_seq: int = 0

    def next_customer_id(self) -> str:
        self._customer_seq += 1
        return f"CUST{self._customer_seq:06d}"

    def next_order_id(self) -> str:
        self._order_seq += 1
        return f"ORD{self._order_seq:07d}"

    def next_event_id(self) -> str:
        self._event_seq += 1
        return f"EVT{self._event_seq:07d}"


def add_customer(reg: Registry, rng: random.Random, created_at: datetime,
                  ring_id: str | None, cluster_tag: str) -> str:
    cust_id = reg.next_customer_id()
    first, last = _gen_name(rng)
    reg.customers.append({
        "customer_id": cust_id,
        "name": f"{first} {last}",
        "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "ring_id": ring_id or "",
        "cluster_tag": cluster_tag,
    })
    reg.ground_truth.append({
        "account_id": cust_id,
        "label": ring_id if ring_id else "legit",
    })
    return cust_id


def add_device(reg: Registry, customer_id: str, device_id: str) -> None:
    reg._device_row_seq += 1
    reg.devices.append({
        "device_row_id": f"DEV{reg._device_row_seq:07d}",
        "device_id": device_id,
        "customer_id": customer_id,
    })


def add_address(reg: Registry, customer_id: str, line1: str, line2: str,
                 city: str, state: str, pincode: str) -> str:
    reg._addr_seq += 1
    addr_id = f"ADDR{reg._addr_seq:07d}"
    reg.addresses.append({
        "address_id": addr_id,
        "customer_id": customer_id,
        "line1": line1,
        "line2": line2,
        "city": city,
        "state": state,
        "pincode": pincode,
    })
    return addr_id


def add_card(reg: Registry, customer_id: str, card_id: str) -> None:
    reg._card_row_seq += 1
    instrument_type = "upi" if card_id.startswith("upi-") else "card"
    reg.cards.append({
        "card_row_id": f"CARD{reg._card_row_seq:07d}",
        "card_id": card_id,
        "customer_id": customer_id,
        "instrument_type": instrument_type,
    })


def add_phone(reg: Registry, customer_id: str, raw_number: str) -> None:
    reg._phone_seq += 1
    reg.phones.append({
        "phone_id": f"PH{reg._phone_seq:07d}",
        "customer_id": customer_id,
        "raw_number": raw_number,
    })


def add_ip(reg: Registry, customer_id: str, ip_address: str, subnet_24: str) -> None:
    reg._ip_seq += 1
    reg.ips.append({
        "ip_id": f"IP{reg._ip_seq:07d}",
        "customer_id": customer_id,
        "ip_address": ip_address,
        "subnet_24": subnet_24,
    })


def add_orders_and_events(reg: Registry, rng: random.Random, customer_id: str,
                           created_at: datetime, n_orders: int,
                           event_rate: float, promo_code: str | None,
                           burst_window_days: int | None) -> None:
    """Create n_orders orders for a customer plus events at event_rate.

    burst_window_days: if set, order dates AND any resulting events are
    concentrated within this many days after account creation (ring
    behavior). If None, orders spread naturally over the following months
    (legit behavior) and any event lags its order by a realistic 1-30 days.
    """
    for _ in range(n_orders):
        if burst_window_days:
            order_date = created_at + timedelta(
                days=rng.uniform(0, burst_window_days),
                hours=rng.uniform(0, 24),
            )
        else:
            order_date = created_at + timedelta(
                days=rng.uniform(1, 500),
                hours=rng.uniform(0, 24),
            )
        order_date = min(order_date, SIM_END)
        order_id = reg.next_order_id()
        amount = round(rng.uniform(299, 8999), 2)
        use_promo = promo_code if (promo_code and rng.random() < 0.85) else (
            rng.choice(PROMO_CODES) if rng.random() < 0.08 else ""
        )
        payment_status = "completed"
        reg.orders.append({
            "order_id": order_id,
            "customer_id": customer_id,
            "order_date": order_date.strftime("%Y-%m-%d %H:%M:%S"),
            "amount": amount,
            "promo_code": use_promo,
            "payment_status": payment_status,
        })

        if rng.random() < event_rate:
            event_type = rng.choice(EVENT_TYPES)
            if burst_window_days:
                lag_days = rng.uniform(0.2, 5)
            else:
                lag_days = rng.uniform(1, 30)
            event_date = min(order_date + timedelta(days=lag_days), SIM_END)
            reg.events.append({
                "event_id": reg.next_event_id(),
                "order_id": order_id,
                "customer_id": customer_id,
                "event_type": event_type,
                "event_date": event_date.strftime("%Y-%m-%d %H:%M:%S"),
            })


# ---------------------------------------------------------------------------
# Population builders
# ---------------------------------------------------------------------------

LEGIT_EVENT_RATE = 0.04          # ~4% of orders trigger a return/CB/COD-refusal
RING_EVENT_RATE_LOW = 0.35       # elevated rings
RING_EVENT_RATE_HIGH = 0.65      # aggressively elevated rings


def build_independent_legit(reg: Registry, rng: random.Random, n: int) -> None:
    """Ordinary legit customers with no interesting shared attributes."""
    for _ in range(n):
        created_at = _random_date(rng, SIM_START, SIM_END - timedelta(days=30))
        cust_id = add_customer(reg, rng, created_at, ring_id=None, cluster_tag="independent")

        city, state, pin_prefix = rng.choice(CITY_STATE_PINCODE)
        pincode = pin_prefix + "".join(rng.choices(string.digits, k=2))
        line1, line2 = _gen_address_lines(rng)
        add_address(reg, cust_id, line1, line2, city, state, pincode)

        add_device(reg, cust_id, _gen_device_id(rng))
        add_card(reg, cust_id, _gen_card_id(rng))

        raw_phone = _messify_phone(_gen_phone(rng), rng)
        add_phone(reg, cust_id, raw_phone)

        ip_addr, subnet = _gen_ip(rng)
        add_ip(reg, cust_id, ip_addr, subnet)

        n_orders = rng.randint(1, 20)
        add_orders_and_events(reg, rng, cust_id, created_at, n_orders,
                               event_rate=LEGIT_EVENT_RATE, promo_code=None,
                               burst_window_days=None)


def build_families(reg: Registry, rng: random.Random, n_families: int) -> int:
    """3-6 customers sharing one address, sometimes one device. Legit."""
    total = 0
    for fam_idx in range(n_families):
        size = rng.randint(3, 6)
        total += size
        surname = rng.choice(LAST_NAMES)
        city, state, pin_prefix = rng.choice(CITY_STATE_PINCODE)
        pincode = pin_prefix + "".join(rng.choices(string.digits, k=2))
        building = _gen_building(rng)
        street = rng.choice(STREET_NAMES)
        base_line1, base_line2 = _gen_address_lines(rng, building=building, street=street)

        shared_device = _gen_device_id(rng) if rng.random() < 0.4 else None

        for _ in range(size):
            created_at = _random_date(rng, SIM_START, SIM_END - timedelta(days=30))
            cust_id = add_customer(reg, rng, created_at, ring_id=None,
                                    cluster_tag=f"family_{fam_idx}")

            add_address(reg, cust_id, base_line1, base_line2, city, state, pincode)

            if shared_device and rng.random() < 0.7:
                add_device(reg, cust_id, shared_device)
            else:
                add_device(reg, cust_id, _gen_device_id(rng))

            add_card(reg, cust_id, _gen_card_id(rng))

            raw_phone = _messify_phone(_gen_phone(rng), rng)
            add_phone(reg, cust_id, raw_phone)

            ip_addr, subnet = _gen_ip(rng)
            add_ip(reg, cust_id, ip_addr, subnet)

            n_orders = rng.randint(1, 20)
            add_orders_and_events(reg, rng, cust_id, created_at, n_orders,
                                   event_rate=LEGIT_EVENT_RATE, promo_code=None,
                                   burst_window_days=None)
    return total


def build_office_cluster(reg: Registry, rng: random.Random, n_members: int) -> None:
    """~30 customers sharing an IP /24 subnet (office/co-working). Legit."""
    subnet = f"{rng.randint(10, 223)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}"
    for _ in range(n_members):
        created_at = _random_date(rng, SIM_START, SIM_END - timedelta(days=30))
        cust_id = add_customer(reg, rng, created_at, ring_id=None, cluster_tag="office_cluster")

        city, state, pin_prefix = rng.choice(CITY_STATE_PINCODE)
        pincode = pin_prefix + "".join(rng.choices(string.digits, k=2))
        line1, line2 = _gen_address_lines(rng)
        add_address(reg, cust_id, line1, line2, city, state, pincode)

        add_device(reg, cust_id, _gen_device_id(rng))
        add_card(reg, cust_id, _gen_card_id(rng))

        raw_phone = _messify_phone(_gen_phone(rng), rng)
        add_phone(reg, cust_id, raw_phone)

        ip_addr, _ = _gen_ip(rng, subnet=subnet)
        add_ip(reg, cust_id, ip_addr, subnet)

        n_orders = rng.randint(1, 20)
        add_orders_and_events(reg, rng, cust_id, created_at, n_orders,
                               event_rate=LEGIT_EVENT_RATE, promo_code=None,
                               burst_window_days=None)


def build_hostel_cluster(reg: Registry, rng: random.Random, n_members: int) -> None:
    """Many customers sharing one pincode (hostel/PG), distinct addresses/devices. Legit."""
    city, state, pin_prefix = rng.choice(CITY_STATE_PINCODE)
    pincode = pin_prefix + "".join(rng.choices(string.digits, k=2))
    hostel_building = _gen_building(rng)
    for room in range(n_members):
        created_at = _random_date(rng, SIM_START, SIM_END - timedelta(days=30))
        cust_id = add_customer(reg, rng, created_at, ring_id=None, cluster_tag="hostel_pg")

        room_no = 100 + room
        line1 = f"Room {room_no}, {hostel_building}"
        line1 = _typo(line1, rng, p=0.05)
        line2 = rng.choice(STREET_NAMES)
        add_address(reg, cust_id, line1, line2, city, state, pincode)

        add_device(reg, cust_id, _gen_device_id(rng))
        add_card(reg, cust_id, _gen_card_id(rng))

        raw_phone = _messify_phone(_gen_phone(rng), rng)
        add_phone(reg, cust_id, raw_phone)

        ip_addr, subnet = _gen_ip(rng)
        add_ip(reg, cust_id, ip_addr, subnet)

        n_orders = rng.randint(1, 20)
        add_orders_and_events(reg, rng, cust_id, created_at, n_orders,
                               event_rate=LEGIT_EVENT_RATE, promo_code=None,
                               burst_window_days=None)


def build_couple_shared_card(reg: Registry, rng: random.Random, n_couples: int) -> None:
    """2 customers sharing one address + one card (joint account). Legit, low volume."""
    for _ in range(n_couples):
        city, state, pin_prefix = rng.choice(CITY_STATE_PINCODE)
        pincode = pin_prefix + "".join(rng.choices(string.digits, k=2))
        line1, line2 = _gen_address_lines(rng)
        shared_card = _gen_card_id(rng)

        for _ in range(2):
            created_at = _random_date(rng, SIM_START, SIM_END - timedelta(days=30))
            cust_id = add_customer(reg, rng, created_at, ring_id=None, cluster_tag="couple")

            add_address(reg, cust_id, line1, line2, city, state, pincode)
            add_device(reg, cust_id, _gen_device_id(rng))
            add_card(reg, cust_id, shared_card)

            raw_phone = _messify_phone(_gen_phone(rng), rng)
            add_phone(reg, cust_id, raw_phone)

            ip_addr, subnet = _gen_ip(rng)
            add_ip(reg, cust_id, ip_addr, subnet)

            n_orders = rng.randint(1, 15)
            add_orders_and_events(reg, rng, cust_id, created_at, n_orders,
                                   event_rate=LEGIT_EVENT_RATE, promo_code=None,
                                   burst_window_days=None)


def build_fraud_ring(reg: Registry, rng: random.Random, ring_id: str, size: int) -> None:
    """A coordinated fraud ring: shared high-signal attribute(s), burst-created,
    elevated loss-causing event rate, optional shared promo farming."""
    burst_start = _random_date(rng, SIM_START, SIM_END - timedelta(days=60))
    burst_span_days = rng.randint(1, 10)

    # Pick 1-2 sharing strategies for this ring so rings aren't all identical.
    strategies = rng.sample(["device", "card", "address"], k=rng.randint(1, 2))

    shared_device = _gen_device_id(rng) if "device" in strategies else None
    shared_card = _gen_card_id(rng) if "card" in strategies else None

    base_addr = None
    if "address" in strategies:
        city, state, pin_prefix = rng.choice(CITY_STATE_PINCODE)
        pincode = pin_prefix + "".join(rng.choices(string.digits, k=2))
        building = _gen_building(rng)
        street = rng.choice(STREET_NAMES)
        base_line1, base_line2 = _gen_address_lines(rng, building=building, street=street)
        base_addr = (base_line1, base_line2, city, state, pincode)

    event_rate = rng.uniform(RING_EVENT_RATE_LOW, RING_EVENT_RATE_HIGH)
    promo_farm = rng.choice(PROMO_CODES) if rng.random() < 0.5 else None

    for _ in range(size):
        created_at = burst_start + timedelta(
            days=rng.uniform(0, burst_span_days), hours=rng.uniform(0, 24)
        )
        cust_id = add_customer(reg, rng, created_at, ring_id=ring_id, cluster_tag=f"ring_{ring_id}")

        # Attribute sharing per chosen strategy; members not on the shared
        # attribute still get their own random one (rings look "distinct").
        if shared_device and rng.random() < 0.8:
            add_device(reg, cust_id, shared_device)
        else:
            add_device(reg, cust_id, _gen_device_id(rng))

        if shared_card and rng.random() < 0.75:
            add_card(reg, cust_id, shared_card)
        else:
            add_card(reg, cust_id, _gen_card_id(rng))

        if base_addr and rng.random() < 0.85:
            line1, line2 = _near_duplicate_address(base_addr[0], base_addr[1], rng)
            add_address(reg, cust_id, line1, line2, base_addr[2], base_addr[3], base_addr[4])
        else:
            city, state, pin_prefix = rng.choice(CITY_STATE_PINCODE)
            pincode = pin_prefix + "".join(rng.choices(string.digits, k=2))
            line1, line2 = _gen_address_lines(rng)
            add_address(reg, cust_id, line1, line2, city, state, pincode)

        raw_phone = _messify_phone(_gen_phone(rng), rng)
        add_phone(reg, cust_id, raw_phone)

        ip_addr, subnet = _gen_ip(rng)
        add_ip(reg, cust_id, ip_addr, subnet)

        n_orders = rng.randint(2, 8)
        add_orders_and_events(reg, rng, cust_id, created_at, n_orders,
                               event_rate=event_rate, promo_code=promo_farm,
                               burst_window_days=burst_span_days + 14)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def generate(config: dict) -> tuple[Registry, dict]:
    seed = config.get("random_seed", 42)
    rng = random.Random(seed)

    gen_cfg = config.get("data_gen", {}) or {}
    n_legit_total = gen_cfg.get("n_legit_customers", 1500)
    n_rings = gen_cfg.get("n_rings", 15)
    ring_size_min = gen_cfg.get("ring_size_min", 4)
    ring_size_max = gen_cfg.get("ring_size_max", 9)

    reg = Registry()

    # --- Benign look-alike clusters (carved out of the legit population) ---
    n_office = 30
    n_hostel = 40
    n_couples = 6           # 12 customers
    n_families = 12         # ~3-6 each, ~54 customers

    family_count = build_families(reg, rng, n_families)
    build_office_cluster(reg, rng, n_office)
    build_hostel_cluster(reg, rng, n_hostel)
    build_couple_shared_card(reg, rng, n_couples)

    clustered_so_far = family_count + n_office + n_hostel + (n_couples * 2)
    n_independent = max(0, n_legit_total - clustered_so_far)
    build_independent_legit(reg, rng, n_independent)

    # --- Fraud rings ---
    ring_sizes = []
    for i in range(1, n_rings + 1):
        ring_id = f"RING{i:03d}"
        size = rng.randint(ring_size_min, ring_size_max)
        ring_sizes.append((ring_id, size))
        build_fraud_ring(reg, rng, ring_id, size)

    stats = {
        "n_legit_total": clustered_so_far + n_independent,
        "n_independent": n_independent,
        "n_family_clusters": n_families,
        "n_family_members": family_count,
        "n_office_members": n_office,
        "n_hostel_members": n_hostel,
        "n_couples": n_couples,
        "n_rings": n_rings,
        "ring_sizes": ring_sizes,
    }
    return reg, stats


def write_csvs(reg: Registry) -> None:
    pd.DataFrame(reg.customers).to_csv(DATA_DIR / "customers.csv", index=False)
    pd.DataFrame(reg.devices).to_csv(DATA_DIR / "devices.csv", index=False)
    pd.DataFrame(reg.addresses).to_csv(DATA_DIR / "addresses.csv", index=False)
    pd.DataFrame(reg.cards).to_csv(DATA_DIR / "cards.csv", index=False)
    pd.DataFrame(reg.phones).to_csv(DATA_DIR / "phones.csv", index=False)
    pd.DataFrame(reg.ips).to_csv(DATA_DIR / "ips.csv", index=False)
    pd.DataFrame(reg.orders).to_csv(DATA_DIR / "orders.csv", index=False)
    pd.DataFrame(reg.events).to_csv(DATA_DIR / "events.csv", index=False)
    pd.DataFrame(reg.ground_truth).to_csv(DATA_DIR / "ground_truth.csv", index=False)


def print_summary(reg: Registry, stats: dict) -> None:
    ring_sizes = stats["ring_sizes"]
    total_ring_members = sum(sz for _, sz in ring_sizes)

    print("=" * 72)
    print("Stage 1 — synthetic data generation summary")
    print("=" * 72)
    print(f"Legit customers (total):        {stats['n_legit_total']}")
    print(f"  - independent legit:           {stats['n_independent']}")
    print(f"  - family cluster members:      {stats['n_family_members']} "
          f"({stats['n_family_clusters']} families, 3-6 each)")
    print(f"  - office/co-working cluster:   {stats['n_office_members']} (shared /24 subnet)")
    print(f"  - hostel/PG cluster:           {stats['n_hostel_members']} (shared pincode)")
    print(f"  - couples sharing a card:      {stats['n_couples']} couples "
          f"({stats['n_couples'] * 2} customers)")
    print()
    print(f"Fraud rings injected:           {stats['n_rings']}")
    print(f"  total ring member accounts:    {total_ring_members}")
    print("  ring sizes:")
    for ring_id, size in ring_sizes:
        print(f"    {ring_id}: {size} accounts")
    print()
    print(f"Grand total accounts:            {stats['n_legit_total'] + total_ring_members}")
    print()

    # Confirm benign look-alike clusters exist as connected-but-legit.
    df_customers = pd.DataFrame(reg.customers)
    tag_counts = df_customers["cluster_tag"].value_counts()
    benign_cluster_tags = [t for t in tag_counts.index
                            if t.startswith("family_") or t in ("office_cluster", "hostel_pg", "couple")]
    print("Benign look-alike clusters confirmed present (label=legit in ground_truth):")
    print(f"  families:        {sum(1 for t in tag_counts.index if t.startswith('family_'))} clusters")
    print(f"  office_cluster:  {tag_counts.get('office_cluster', 0)} accounts, 1 shared IP subnet")
    print(f"  hostel_pg:       {tag_counts.get('hostel_pg', 0)} accounts, 1 shared pincode")
    print(f"  couple:          {tag_counts.get('couple', 0)} accounts sharing a card")
    print("=" * 72)


def main() -> None:
    config = load_config()
    reg, stats = generate(config)
    write_csvs(reg)
    print_summary(reg, stats)
    print(f"\nCSV files written to: {DATA_DIR}")


if __name__ == "__main__":
    main()
