"""Starter categories and rules for a new database.

They cover common UK banks' payees and big chains. Everything is editable in the app,
and categorising a transaction by hand offers to turn it into a rule, so the set
grows to fit you. Seeding only happens on an empty database; investment platforms
are added during setup.
"""

from __future__ import annotations

import sqlite3

from .rules import categorise

# name -> group
CATEGORIES: dict[str, str] = {
    # Regulars: income and fixed bills
    "Wages": "regular",
    "Benefits & tax refunds": "regular",
    "Rent or mortgage": "regular",
    "Council tax": "regular",
    "Utilities": "regular",
    "Internet": "regular",
    "Phone": "regular",
    "Insurance": "regular",
    "Gym": "regular",
    "Loans & credit cards": "regular",
    # Cash
    "Cash machine": "cash",
    "Transfers between my accounts": "cash",
    # Ad hoc spending
    "Groceries": "adhoc",
    "Eating & drinking": "adhoc",
    "Transport": "adhoc",
    "Shopping": "adhoc",
    "Going out & trips": "adhoc",
    "Friends & family": "adhoc",
    "Apps & subscriptions": "adhoc",
    "Selling stuff": "adhoc",
    "Other": "adhoc",
}

BILL = 10  # named billers
SPECIFIC = 6  # named shops and services
KEYWORD = 2  # generic words like PUB or CAFE
FALLBACK = -10  # bank transaction types

# (pattern, category, match_type, direction, tx_type, priority)
RULES: list[tuple[str, str, str, str, str | None, int]] = [
    # Income
    (r"SALARY|WAGES|PAYROLL", "Wages", "regex", "in", None, BILL),
    (
        r"\bDWP\b|HMRC|UNIVERSAL CREDIT|CHILD BENEFIT",
        "Benefits & tax refunds",
        "regex",
        "in",
        None,
        BILL,
    ),
    # Bills
    (r"\bRENT\b|MORTGAGE|LETTINGS|PROPERTY MANAG", "Rent or mortgage", "regex", "out", None, BILL),
    (r"COUNCIL|BOROUGH|CITY COU", "Council tax", "regex", "out", "DD", BILL),
    (r"COUNCIL TAX", "Council tax", "regex", "out", None, BILL),
    (
        r"OCTOPUS ENERGY|BRITISH GAS|\bEDF\b|E\.?ON|OVO|SCOTTISH ?POWER|SO ENERGY|UTILITA"
        r"|SHELL ENERGY|SWITCH2|CLEVER ENERGY|UNITED UTILITIES|THAMES WATER|SEVERN TRENT"
        r"|YORKSHIRE WATER|ANGLIAN WATER|WELSH WATER|SOUTHERN WATER|NORTHUMBRIAN WATER",
        "Utilities",
        "regex",
        "any",
        None,
        BILL,
    ),
    (
        r"VIRGIN MEDIA|BT GROUP|\bBT\b|SKY DIGITAL|SKY UK|TALKTALK|PLUSNET|HYPEROPTIC"
        r"|COMMUNITY FIBRE|VODAFONE BROADBAND|NOW BROADBAND|SHELL BROADBAND",
        "Internet",
        "regex",
        "any",
        None,
        BILL,
    ),
    (
        r"\bO2\b|TELEFONICA|\bEE\b|EE LIMITED|VODAFONE|\bTHREE\b|HUTCHISON|GIFFGAFF|ECOTALK"
        r"|LEBARA|SMARTY|VOXI|TESCO MOBILE|ID MOBILE|LYCAMOBILE|SKY MOBILE",
        "Phone",
        "regex",
        "any",
        None,
        BILL + 1,
    ),
    (
        r"INSURANCE|ADMIRAL|AVIVA|DIRECT LINE|\bAXA\b|\bLV\b|CHURCHILL|HASTINGS|ESURE"
        r"|SIMPLY BUSINESS|LEMONADE",
        "Insurance",
        "regex",
        "any",
        None,
        BILL,
    ),
    (
        r"PUREGYM|THE GYM|DAVID LLOYD|NUFFIELD|ANYTIME FITNESS|FITNESS FIRST|BANNATYNE"
        r"|\bGYM\b",
        "Gym",
        "regex",
        "any",
        None,
        BILL,
    ),
    (
        r"\bSLC\b|STUDENT LOAN|AMERICAN EXPRESS|\bAMEX\b|BARCLAYCARD|CAPITAL ONE|\bMBNA\b"
        r"|VANQUIS|AQUA CARD|KLARNA|CLEARPAY|PAYPAL CREDIT",
        "Loans & credit cards",
        "regex",
        "out",
        None,
        BILL,
    ),
    # Groceries
    (
        r"TESCO|SAINSBURY|\bASDA\b|\bALDI\b|\bLIDL\b|MORRISONS|CO-?OP|\bCO OP\b|ICELAND"
        r"|WAITROSE|M&S SIMPLY|M&S FOOD|MARKS&SPENCER|MARKS & SPENCER|OCADO|FARMFOODS"
        r"|\bNISA\b|ONE STOP|PREMIER|SPAR\b|COSTCUTTER|BUDGENS|CONVENIEN",
        "Groceries",
        "regex",
        "any",
        None,
        SPECIFIC,
    ),
    ("SAINSBURYS PETROL", "Transport", "contains", "any", None, SPECIFIC + 1),
    ("TESCO MOBILE", "Phone", "contains", "any", None, BILL + 1),
    # Eating & drinking
    (
        r"MCDONALDS|\bKFC\b|BURGER KING|FIVE GUYS|NANDOS|WAGAMAMA|PIZZA|DOMINO|PAPA JOHN"
        r"|SUBWAY|GREGGS|STARBUCKS|COSTA|CAFFE NERO|PRET|TIM HORTONS|LEON\b|ITSU|WASABI"
        r"|YO SUSHI|DELIVEROO|JUST EAT|UBER \*EATS|WETHERSPOON|BREWDOG|TOBY CARVERY"
        r"|HARVESTER|FRANKIE|ZIZZI|PREZZO|BELLA ITALIA|WH SMITH|WHSMITH",
        "Eating & drinking",
        "regex",
        "any",
        None,
        SPECIFIC,
    ),
    (
        r"\bPUB\b|\bBAR\b|BARS\b|TAVERN|\bINN\b|ARMS\b|KITCHEN|RESTAURANT|GRILL|CAFE|CAFFE"
        r"|COFFEE|BAKERY|TAKEAWAY|NOODLE|SUSHI|CURRY|KEBAB|CHIPPY|BREWERY|BREWING|\bBEER"
        r"|VENDING|NEWSAGENT|\bNEWS\b",
        "Eating & drinking",
        "regex",
        "any",
        None,
        KEYWORD,
    ),
    # Transport
    (
        r"TRAINLINE|NATIONAL RAIL|AVANTI|LNER|NORTHERN TRAINS|GWR|SOUTHERN RAIL|THAMESLINK"
        r"|MERSEYRAIL|SCOTRAIL|TFL|TRANSPORT FOR|ARRIVA|STAGECOACH|FIRST BUS|NATIONAL EXPRESS"
        r"|MEGABUS|UBER \*TRIP|UBER TRIP|\bBOLT\b|ADDISON LEE|JUSTPARK|RINGGO|PAYBYPHONE|\bNCP\b"
        r"|PETROL|\bESSO\b|\bBP\b|SHELL\b|TEXACO|JET\b|RONTEC|MFG|EV ?CHARG|INSTAVOLT"
        r"|GRIDSERVE|POD POINT|BP PULSE|IONITY|TESLA|MERSEY GATEWAY|DART CHARGE|\bDVLA\b",
        "Transport",
        "regex",
        "any",
        None,
        SPECIFIC,
    ),
    # Shopping
    (
        r"AMAZON|EBAY O\*|\bEBAY\b|ARGOS|CURRYS|ASOS|PRIMARK|TK ?MAXX|NEXT\b|H&M|ZARA|UNIQLO"
        r"|JOHN LEWIS|DEBENHAMS|SPORTS DIRECT|JD SPORTS|SCHUH|BOOTS|SUPERDRUG|HOLLAND"
        r"|\bLUSH\b|IKEA|B&Q|WICKES|HOMEBASE|DUNELM|THE RANGE|B&M|HOME BARGAINS|POUNDLAND"
        r"|WILKO|SMYTHS|WATERSTONES|APPLE STORE|SAVERS|PHARMACY",
        "Shopping",
        "regex",
        "any",
        None,
        SPECIFIC,
    ),
    # Going out & trips
    (
        r"TICKETMASTER|SEE TICKETS|EVENTBRITE|FATSOMA|DICE\.FM|SKIDDLE|ODEON|CINEWORLD"
        r"|\bVUE\b|EVERYMAN|MUSEUM|NATIONAL TRUST|BOWLING|TRAVELODGE|PREMIER INN|HOLIDAY INN"
        r"|HILTON|MARRIOTT|AIRBNB|BOOKING\.COM|HOTELS\.COM|EXPEDIA|EASYJET|RYANAIR|JET2"
        r"|BRITISH AIRWAYS|TUI\b|LOVEHOLIDAYS|HOSTEL",
        "Going out & trips",
        "regex",
        "any",
        None,
        SPECIFIC + 1,
    ),
    # Apps & subscriptions
    (
        r"NETFLIX|SPOTIFY|APPLE\.COM|ITUNES|GOOGLE PLAY|GOOGLE \*|YOUTUBE|DISNEY|AMAZON PRIME"
        r"|PRIME VIDEO|AUDIBLE|NOW TV|PARAMOUNT|CRUNCHYROLL|PLAYSTATION|XBOX|NINTENDO|STEAM"
        r"|PATREON|MICROSOFT|ADOBE|DROPBOX|NORDVPN|EXPRESSVPN|CHATGPT|OPENAI|ANTHROPIC"
        r"|\bTV LICENCE",
        "Apps & subscriptions",
        "regex",
        "out",
        None,
        SPECIFIC + 1,
    ),
    # Selling stuff
    (
        r"EBAY COMMERCE|VINTED|MANGOPAY|DEPOP|ETSY PAYMENTS",
        "Selling stuff",
        "regex",
        "in",
        None,
        BILL,
    ),
    # Other
    (
        r"CHARITY|BARNARDO|OXFAM|BRITISH HEART|CANCER RESEARCH|RSPCA|SHELTER|SAMARITANS"
        r"|JUSTGIVING|GOFUNDME|POST OFFICE|ROYAL MAIL|DEPOSIT PROTECTION|\bTDS\b|\bDPS\b",
        "Other",
        "regex",
        "any",
        None,
        SPECIFIC,
    ),
    # By bank transaction type (Lloyds-style CSVs), when nothing else matches
    ("", "Cash machine", "contains", "out", "CPT", FALLBACK),
    ("", "Friends & family", "contains", "out", "FPO", FALLBACK),
    ("", "Friends & family", "contains", "in", "FPI", FALLBACK),
    (r"CASH|ATM|NOTEMACHINE|LINK\b", "Cash machine", "regex", "out", None, KEYWORD),
]


def seed(conn: sqlite3.Connection, *, apply: bool = True) -> bool:
    """Insert starter categories and rules into an empty database.

    Returns True if anything was seeded.
    """
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]:
        return False
    with conn:
        ids: dict[str, int] = {}
        for name, bucket in CATEGORIES.items():
            cur = conn.execute("INSERT INTO categories(name, bucket) VALUES (?,?)", (name, bucket))
            ids[name] = cur.lastrowid
        conn.executemany(
            "INSERT INTO rules(pattern, category_id, match_type, direction, tx_type, priority) "
            "VALUES (?,?,?,?,?,?)",
            [
                (pattern, ids[category], match_type, direction, tx_type, priority)
                for pattern, category, match_type, direction, tx_type, priority in RULES
            ],
        )
        if apply:
            categorise(conn)
    return True
