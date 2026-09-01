"""Example: add products the way an MCP client does — server.add_product(...).

The four products here (Honda Fit, Toyota Prius, Tesla Model Y, Guardian 24" bike)
live only in the database; this file documents their rule sets. Run with
PF_DB=<db> uv run python docs/add_products_example.py
"""

from product_finder_mcp import server

LOCAL = ["craigslist", "facebook-marketplace"]
YEAR = {"pattern": r"\b(20[0-2]\d)\b", "type": "int"}
MILES = {"pattern": r"\b(\d{4,6})\s*(?:mi\b|miles)", "type": "int"}
MILES_K = {"pattern": r"\b(\d{1,3})k\s*(?:mi\b|miles)?", "type": "int"}
SALVAGE = {
    "pattern": r"salvage|rebuilt|branded title|flood|lemon|totaled|mechanic special|blown",
    "type": "bool",
}
VEHICLE = {
    "pattern": r"\b(?:miles|mi|mileage|title|owner|vin|hybrid|hatchback|sedan|awd|long range|performance|automatic|cvt|ex-l|lx|ex|sport|touring|prime|eco|\d{2,3}k)\b|\b20[0-2]\d\b",
    "type": "bool",
}
CAR_PARTS = {
    "pattern": (
        r"for parts|parts only|\bparts?\b|bumper|head ?light|tail ?light|fog light|mirror|\bwheels?\b|\brims?\b"
        r"|\btires?\b|floor ?mats?|\bmats?\b|key ?fob|\bkey\b|seat covers?|hubcaps?|battery (?:module|pack|cell)"
        r"|charger|charging|mud ?flaps?|roof rack|cargo|wipers?|\boem\b|\bfits\b|tow hitch|hitch|spoiler|grille?"
        r"|door handle|fender|\bhood\b|trunk|console|radio|stereo|sensor|module|accessor|brochure|\btoy\b|diecast"
        r"|scale|model kit|sticker|decal|emblem|badge|\bcover\b|filter|engine\b|transmission|catalytic|exhaust"
        r"|alternator|starter|axle|strut|shock|brake|rotor|caliper|radiator|pump|belt|hose|gasket|bracket"
        r"|lens|bulb|\bled\b|liner|\bnet\b|wall connector|mobile connector|adapter|j1772|nema|screen protector"
        r"|\bwrap\b|tint|tesla cam|dashcam|sunshade|sun shade|wheel cover|aero cover|lug nut|jack\b|\bkit\b"
    ),
    "type": "bool",
}
CAR_CHECKS = [
    "Run the VIN through Carfax/AutoCheck before viewing (accidents, odometer, title brands)",
    "Seller has the physical title in hand, in their name — no open liens",
    "Independent pre-purchase inspection (~$100-150) before paying",
    "Test drive cold start: warning lights, brakes, pulling, noises",
    "Check recalls at nhtsa.gov/recalls by VIN",
]


def car(
    slug,
    name,
    desc,
    queries,
    year_min,
    price_floor,
    miles_w,
    extra_checks,
    extra_crit=(),
    model=None,
):
    return dict(
        slug=slug,
        name=name,
        description=desc,
        queries=queries,
        sites=LOCAL,
        extractors={
            "year": YEAR,
            "mileage": MILES,
            "mileage_k": MILES_K,
            "salvage": SALVAGE,
            "is_parts": CAR_PARTS,
            "is_vehicle": VEHICLE,
            "is_model": {"pattern": model, "type": "bool"},
        },
        criteria=[
            {
                "field": "is_model",
                "op": "eq",
                "value": True,
                "weight": 0,
                "required": True,
                "reject": True,
                "note": f"not a {name}",
            },
            {
                "field": "is_parts",
                "op": "eq",
                "value": False,
                "weight": 0,
                "required": True,
                "reject": True,
                "note": "parts / accessory listing, not a car",
            },
            {
                "field": "is_vehicle",
                "op": "eq",
                "value": True,
                "weight": 0,
                "required": True,
                "reject": True,
                "note": "no vehicle signals in title (year, miles, title, trim)",
            },
            {
                "field": "price",
                "op": "gte",
                "value": price_floor,
                "weight": 0,
                "required": True,
                "reject": True,
                "note": f"under ${price_floor:,} is not a whole car",
            },
            {
                "field": "salvage",
                "op": "eq",
                "value": False,
                "weight": 2,
                "required": True,
                "reject": True,
                "note": "salvage / rebuilt / branded title",
            },
            {
                "field": "year",
                "op": "gte",
                "value": year_min,
                "weight": 3,
                "required": True,
                "note": f"{year_min}+ model year",
            },
            {
                "field": "mileage",
                "op": "lte",
                "value": 100000,
                "weight": miles_w,
                "note": "under 100k miles",
            },
            {
                "field": "mileage_k",
                "op": "lte",
                "value": 100,
                "weight": 1,
                "note": "under 100k miles",
            },
            *extra_crit,
        ],
        manual_checks=CAR_CHECKS + extra_checks,
    )


PRODUCTS = [
    car(
        model=r"honda\s*fit\b|\bfit\s+(?:ex|lx|sport|hatch)",
        slug="honda-fit",
        name="Honda Fit",
        desc="Used Honda Fit (3rd gen, 2015+), clean title, under 100k miles.",
        queries=["Honda Fit", "Honda Fit EX", "Honda Fit LX"],
        year_min=2015,
        price_floor=4000,
        miles_w=2,
        extra_checks=[
            "Fit-specific: check the CVT for shudder on test drive; ask for CVT fluid change history",
            "Fit-specific: rear Magic Seat latches and hatch struts work",
        ],
        extra_crit=[
            {
                "field": "trim",
                "op": "one_of",
                "value": ["ex", "ex-l"],
                "weight": 1,
                "note": "EX/EX-L trim",
            }
        ],
    ),
    car(
        model=r"\bprius\b",
        slug="toyota-prius",
        name="Toyota Prius",
        desc="Used Toyota Prius (4th gen, 2016+) or Prius Prime, clean title.",
        queries=["Toyota Prius", "Prius Prime", "Prius hybrid"],
        year_min=2016,
        price_floor=5000,
        miles_w=2,
        extra_checks=[
            "Prius-specific: ask hybrid battery health (Dr. Prius app) and whether the pack was ever replaced",
            "Prius-specific: verify the catalytic converter is original / has a shield (theft target)",
            "Prius-specific: check the 12V auxiliary battery age",
        ],
    ),
    car(
        model=r"model\s*y\b",
        slug="tesla-model-y",
        name="Tesla Model Y",
        desc="Used Tesla Model Y (2020+), clean title, battery health known.",
        queries=["Tesla Model Y", "Model Y Long Range", "Model Y Performance"],
        year_min=2020,
        price_floor=15000,
        miles_w=3,
        extra_checks=[
            "Tesla-specific: ask for a battery health screenshot (Service > Battery health) or rated range at 100%",
            "Tesla-specific: confirm remaining battery/drivetrain warranty (8 yr / 120k mi from in-service date)",
            "Tesla-specific: FSD/Autopilot package does NOT transfer on private sale — don't pay for it",
            "Tesla-specific: check for Supercharger free/idle-fee flags and that the car is removed from the seller's account at handoff",
            "Tesla-specific: heat pump models (2021+) and 4680/structural packs affect service — note the build date",
        ],
    ),
]
# Trim extractor only for the Fit
PRODUCTS[0]["extractors"]["trim"] = {"pattern": r"\b(ex-l|ex|lx|sport)\b", "type": "str"}

BIKE_PARTS = {
    "pattern": (
        r"\btires?\b|\btubes?\b|saddle|\bseat\b|pedals?|helmet|brake|\bchain\b|handlebar|\bgrips?\b"
        r"|training wheels|kickstand|\bbell\b|basket|\brack\b|\bfork\b|crank|\bstem\b|wheel\s*(?:set|only)"
        r"|frame only|for parts|parts only|\block\b|\blight\b|\bpump\b|\bbag\b|decal|sticker|reflector"
        r"|fender|water bottle|\bkit\b|\bparts?\b|accessor|trailer|bike rack|car rack|wiper|blade"
    ),
    "type": "bool",
}
PRODUCTS.append(
    dict(
        slug="guardian-bike-24",
        name='Guardian 24" kids bike',
        description="Guardian Bikes (SureStop single-lever braking) with 24-inch wheels — Original or Ethos.",
        queries=[
            "Guardian bike 24",
            "Guardian Bikes 24 inch",
            "Guardian Ethos 24",
            "Guardian Original 24",
        ],
        sites=LOCAL,
        extractors={
            "is_guardian": {"pattern": r"\bguardian\b", "type": "bool"},
            "is_bike": {"pattern": r"\b(?:bike|bicycle|ethos|original)\b", "type": "bool"},
            "wheel_in": {
                "pattern": r"\b(1[2468]|20|24|26)\s*(?:\"|”|''|inch|in\b|-inch)",
                "type": "int",
            },
            "model": {"pattern": r"\b(ethos|original)\b", "type": "str"},
            "surestop": {"pattern": r"surestop|sure stop", "type": "bool"},
            "is_balance": {"pattern": r"balance bike|\bbalance\b", "type": "bool"},
            "is_parts": BIKE_PARTS,
        },
        criteria=[
            {
                "field": "is_guardian",
                "op": "eq",
                "value": True,
                "weight": 0,
                "required": True,
                "reject": True,
                "note": "not a Guardian bike",
            },
            {
                "field": "is_parts",
                "op": "eq",
                "value": False,
                "weight": 0,
                "required": True,
                "reject": True,
                "note": "bike part / accessory, not a bike",
            },
            {
                "field": "is_bike",
                "op": "eq",
                "value": True,
                "weight": 0,
                "required": True,
                "reject": True,
                "note": "not a bike listing",
            },
            {
                "field": "is_balance",
                "op": "eq",
                "value": False,
                "weight": 0,
                "required": True,
                "reject": True,
                "note": "balance bike (toddler), not a 24-inch",
            },
            {
                "field": "wheel_in",
                "op": "eq",
                "value": 24,
                "weight": 3,
                "required": True,
                "reject": True,
                "note": "24-inch wheels",
            },
            {
                "field": "model",
                "op": "one_of",
                "value": ["ethos", "original"],
                "weight": 1,
                "note": "Ethos or Original",
            },
            {
                "field": "surestop",
                "op": "eq",
                "value": True,
                "weight": 1,
                "note": "SureStop braking mentioned",
            },
            {
                "field": "price",
                "op": "lte",
                "value": 250,
                "weight": 2,
                "note": "under $250 (new is $350-500)",
            },
        ],
        manual_checks=[
            "Photos: SureStop lever pulls both brakes; pads not worn to metal",
            "Frame size/rider height: 24\" Guardian fits roughly 4'5\"-5'0\" — confirm the kid's inseam",
            "Check rims for wobble, tires for dry rot, chain rust",
            "Ask why they're selling and whether it was stored indoors",
        ],
    )
)

for p in PRODUCTS:
    r = server.add_product(**p)
    print("added", r["slug"], "sites=", r["sites"], "queries=", len(r["queries"]))
