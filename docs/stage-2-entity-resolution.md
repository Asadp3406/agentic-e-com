# Stage 2 — Entity Resolution (in plain language)

This document explains what Stage 2 does, why it exists, and what we found when we
tested it. No jargon assumed — if a term needs explaining, it's explained here.

## 1. What problem is this solving?

Stage 1 generated fake customers, and on purpose it made their data **messy**, the
same way real-world data is messy:

- The same address gets typed differently by different people: `"Apt 4B, MG Road"`
  vs `"Apartment 4-B, M.G. Rd"` vs `"Flat 4B MG Road near Metro Station"`.
- The same phone number gets formatted differently: `9876543210` vs `+91 98765 43210`
  vs `09876543210`.
- Fraud rings deliberately create accounts that *look* different (slightly different
  spelling, extra words) but are actually the **same** address/device/card, hoping
  that "different-looking" data will stop us from noticing they're connected.

If we compare these fields with plain text equality (`==`), we'd miss all of these
matches. Two accounts secretly sharing an address would look like two unrelated
accounts, and the fraud ring hiding behind that trick would go undetected.

**Stage 2's job:** clean up and match these messy fields so that things which are
really "the same" get tagged with the same ID — called a **canonical entity ID** —
regardless of how differently they were typed.

This has to be done carefully, though. If we're too aggressive about matching, we'll
wrongly say two different addresses (e.g. two different rooms in the same hostel) are
"the same," and later stages will falsely accuse innocent people of being a fraud
ring. So the real challenge of this stage is: **match the true duplicates, without
merging things that just happen to look similar.**

## 2. What was built

A script: [`resolve/entity_resolution.py`](../resolve/entity_resolution.py)

Run it with:
```
make resolve
```

It reads the raw data Stage 1 created (`data/addresses.csv`, `phones.csv`,
`devices.csv`, `cards.csv`, `ips.csv`) and writes cleaned-up versions into
`data/resolved/`, each with a new canonical ID column added.

Nothing here uses AI/an LLM — every rule is a plain deterministic piece of logic
(string cleanup, a similarity score, a phone-number library, simple arithmetic on an
IP address). That's intentional: this step needs to be 100% reproducible and
explainable, since everything downstream depends on it being trustworthy.

## 3. How each type of data is handled

### Addresses — the hard one

This is the only field where "same vs different" is genuinely fuzzy (typing style
varies), so it gets the most careful treatment, in three steps:

**Step 1 — Clean up the text.**
Lowercase everything, remove commas/periods, and treat spelling variants as
identical: `"Apartment"`, `"Apt"`, `"Flat"`, `"Flat No."`, and even the typo `"Flt"`
all get turned into one common word. Extra "near/opposite/behind [landmark]" phrases
get stripped out too, since a landmark description doesn't change *which* building
the address is.

**Step 2 — Only compare addresses that could plausibly be the same place.**
We only ever compare two addresses if they're in the **same city and same postal
code (pincode)**. This is called "blocking" — it's both a speed optimization (we're
not comparing every address to every other address in the whole country) and, more
importantly, a safety rule: two addresses in different cities are never going to be
merged, no matter how similar the street name text looks.

**Step 3 — Score similarity, but require the unit/room number to also match.**
Within the same city+pincode, we use a fuzzy text-matching library (`rapidfuzz`) to
score how similar two cleaned-up addresses are (0–100). If the score is high enough
(88+), they're considered a match — **but only if** the flat/room/unit number
extracted from each address (e.g. the `"4B-2"` in `"Apt 4B-2, Kumar Residency"`) is
either missing or the same on both sides. If both addresses have a unit number and
those numbers are *different*, we refuse to merge them, even if the score is high.

**Why that last rule matters (this is the precision fix):** a hostel where 40
different people each rent their own room will have addresses like `"Room 101, Gupta
Nagar"` and `"Room 106, Gupta Nagar"` — nearly identical text, different rooms,
completely different people who don't know each other. Without the unit-number
check, the text similarity score alone was high enough (89–93%) to wrongly merge
these into "the same address" — which would have made an innocent hostel look like a
suspicious cluster later on. Requiring the room number to match fixed this.

Addresses that really are duplicates — typos, abbreviation differences, added
landmark text — still merge correctly, because in genuine duplicates the room/unit
number is the *same*, just the words around it differ.

### Phones

Handled by a well-tested library (`phonenumbers`) rather than custom rules, since
phone number formatting has many real-world edge cases already solved by that
library. Every raw number (`+91 98765 43210`, `09876543210`, `98765-43210`, etc.) is
parsed as an Indian number and converted to one standard format (E.164, e.g.
`+919876543210`). Two phone numbers that boil down to the same standard format get
the same canonical ID.

### Devices and cards

These are exact matches after simple cleanup (trim whitespace, lowercase). Unlike
addresses, there's no "typing style" issue here — a device fingerprint or a card
number is a fixed token, not free text a human types differently each time. Fuzzy
matching these would risk randomly linking two *different* devices/cards just
because their strings happen to look alike, which would be worse than not matching
at all. So these are kept strict on purpose.

### IP addresses

Each IP address is bucketed by its first three number groups (its "/24 subnet" —
e.g. `203.102.159.5` and `203.102.159.88` both become `203.102.159`). This is a
deliberately weak, low-confidence signal: lots of unrelated people can share the same
office or ISP block, so a shared subnet alone should never be treated as strong
evidence of a connection. That weighting happens in a later stage — this stage just
produces the grouping.

## 4. What we tested, and what we found (the precision check)

The whole point of this stage is *"don't merge things that shouldn't be merged."*
So the script prints a self-check report every time it runs, showing real examples
from the generated data:

**Examples that correctly got merged (real duplicates):**

| Score | Address A | Address B |
|-------|-----------|-----------|
| 97.0 | Flat No. 3B-2 Joshi Apts | Flat No. 3B-2, oJshi Apts *(typo)* |
| 97.0 | Apartment No. 5C-7, Bhatt Society | Apartment No. 5C-7 Bhatt Society *(missing comma)* |
| 98.7 | Apt 7B-11,J oshi Apts | Apt 7B-11, Joshi Apts *(typo)* |

These are the same address typed slightly differently — correctly recognized as one
place.

**Examples that correctly stayed separate (different addresses that only look alike):**

| Score | Address A | Address B |
|-------|-----------|-----------|
| 97.7 | Room 103, Gupta Nagar | Room 134, Gupta Nagar |
| 97.7 | Room 121, Gupta Nagar | Room 124, Gupta Nagar |
| 97.6 | Room 104, Gupta Nagar | Room 105, Gupta Nagar |

Notice these score *even higher* (97+) than some of the real duplicates above — pure
text similarity would have merged them. They were correctly kept apart because their
room numbers differ.

**Sanity check against the known "honest" test clusters from Stage 1:**

Stage 1 deliberately built a few groups of people who innocently share something, so
we can check we don't falsely flag them:

- **Hostel (40 people, one building, all different rooms):** 40 raw addresses → 40
  separate entities. Zero incorrect merges. ✅
- **Office (30 people sharing one internet connection/subnet):** 30 raw addresses →
  30 separate entities, since their home addresses are all genuinely different. ✅
- **Families (people who really do live at the same address):** each family
  correctly collapses down to a single shared address entity, as expected — they
  really do share one home. ✅

**Overall numbers from one run on the generated dataset:**

| Attribute | Raw rows | Canonical entities after matching |
|-----------|----------|-----------------------------------|
| Addresses | 1,601 | 1,530 (71 duplicates merged) |
| Phones | 1,601 | 1,601 (no accidental merges) |
| Devices | 1,601 | 1,561 |
| Cards | 1,601 | 1,542 |
| IPs | 1,601 | 1,572 subnets |

## 5. Bugs found and fixed while checking this

Worth recording, since they explain *why* the current rules look the way they do:

1. **Hostel rooms were wrongly merging.** Early version only checked text
   similarity, so different rooms in the same hostel scored high enough to merge.
   Fixed by requiring room/unit numbers to also match.
2. **A "missing value" bug hid real duplicates.** When an address had no
   extractable unit number, a technical quirk in how the data table stored "nothing
   is here" caused the matching rule to wrongly reject some valid duplicate pairs.
   Fixed by correctly checking for "missing" in a way that works with how the data
   library represents it.
3. **Phone numbers can get silently corrupted if read carelessly.** If someone
   later opens the output file the naive way, the phone number's leading `+` can get
   silently dropped, turning `+919876543210` into a plain number missing its country
   code marker. Documented clearly in the code so nobody gets caught by this later.

## 6. What Stage 3 gets from this

Stage 3 (building the graph of who's connected to whom) doesn't need to look at any
raw addresses/phones/devices/cards/IPs at all. It just reads the canonical IDs this
stage produced and draws a connection between two customers whenever they share one.
That's the entire point: messy real-world data in, clean trustworthy connections out.

Output files, all under `data/resolved/`:

| File | What it maps |
|------|--------------|
| `resolved_addresses.csv` | address → canonical address ID |
| `resolved_phones.csv` | phone number → canonical phone ID |
| `resolved_devices.csv` | device → canonical device ID |
| `resolved_cards.csv` | card/UPI → canonical card ID |
| `resolved_ips.csv` | IP address → canonical /24 subnet ID |
