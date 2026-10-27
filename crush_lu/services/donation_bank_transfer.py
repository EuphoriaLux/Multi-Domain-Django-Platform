"""Bank-transfer donations for the Community Supporter card.

SumUp refused the platform in 2026-10, so the support card has no card
checkout. When ``DONATION_BANK_IBAN`` and ``DONATION_BANK_BENEFICIARY`` are
set, the card shows the account details instead, with an EPC QR code
("scan to pay", EPC069-12) that a banking app turns into a pre-filled SEPA
transfer. Either setting missing, or an IBAN that fails its checksum, and the
card falls back to the "paused" notice: a typo must never reach a member's
transfer form.

The QR carries no amount, so the member picks one in their banking app. The
remittance text carries the profile's primary key, which is how a coach
matches the transfer to a member: pasted into the CrushProfile admin search,
the reference finds the profile, and the "Grant Community Supporter badge"
action grants the badge.
"""

import logging
import re
from dataclasses import dataclass
from functools import cached_property, lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)

# EPC069-12 field limits.
EPC_MAX_NAME = 70
EPC_MAX_REMITTANCE = 140
EPC_MAX_PAYLOAD_BYTES = 331

_IBAN_SHAPE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
_BIC_SHAPE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")


def normalize_iban(raw):
    """``"lu28 0019 4006..."`` -> ``"LU280019400644750000"`` (BICs too)."""
    return re.sub(r"\s+", "", raw or "").upper()


def iban_is_valid(iban):
    """ISO 13616 mod-97 check on a normalized IBAN."""
    if not _IBAN_SHAPE.match(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def format_iban(iban):
    """Groups of four, the way banks print it."""
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def epc_payload(*, iban, beneficiary, bic="", remittance=""):
    """The EPC QR text for a SEPA credit transfer with no preset amount.

    Version 002 makes the BIC optional inside the EEA. The amount, purpose
    and structured reference stay empty, and the spec forbids a separator
    after the last populated element, hence the rstrip.
    """
    lines = [
        "BCD",
        "002",
        "1",  # UTF-8
        "SCT",
        bic,
        beneficiary[:EPC_MAX_NAME],
        iban,
        "",  # amount: the member chooses it in their banking app
        "",  # purpose code
        "",  # structured (RF) reference
        remittance[:EPC_MAX_REMITTANCE],
    ]
    return "\n".join(lines).rstrip("\n")


@dataclass(frozen=True)
class QrSvg:
    size: int
    path: str


@lru_cache(maxsize=128)
def qr_svg(payload):
    """Module count and SVG path data for ``payload`` as an EPC-compliant QR.

    Error correction M and a single byte-mode segment are what EPC069-12
    specifies; ``optimize=0`` stops qrcode splitting the text into numeric
    and alphanumeric segments. The template draws the ``<svg>`` itself, so no
    markup is marked safe here. Each dark run on a row becomes one rectangle,
    which keeps the path a few KB. Cached per payload: the card renders on
    every dashboard view and a member's payload does not change.
    """
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=4)
    qr.add_data(payload.encode("utf-8"), optimize=0)
    qr.make(fit=True)
    matrix = qr.get_matrix()

    parts = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if not row[x]:
                x += 1
                continue
            start = x
            while x < len(row) and row[x]:
                x += 1
            parts.append(f"M{start} {y}h{x - start}v1h-{x - start}z")
    return QrSvg(size=len(matrix), path="".join(parts))


@dataclass(frozen=True)
class BankTransferDetails:
    beneficiary: str
    iban: str
    bic: str
    reference: str

    @property
    def iban_display(self):
        return format_iban(self.iban)

    @property
    def payload(self):
        return epc_payload(
            iban=self.iban,
            beneficiary=self.beneficiary,
            bic=self.bic,
            remittance=self.reference,
        )

    @cached_property
    def qr(self):
        # Built on first access only: the card asks for the details before it
        # knows whether it will show them (native shell, missing profile).
        return qr_svg(self.payload)


def donation_reference(profile):
    # Plain SEPA characters only: some banks drop anything outside them.
    return f"Crush.lu support {profile.pk}"


# Reads a reference back off a bank statement, which may upper-case it, drop
# the dot or wrap it in other text. The CrushProfile admin search uses it.
DONATION_REFERENCE_RE = re.compile(r"crush\.?lu\s*support\s*(\d+)", re.IGNORECASE)


def get_bank_transfer_details(profile=None):
    """Details for the support card, or ``None`` when they are not usable."""
    iban = normalize_iban(getattr(settings, "DONATION_BANK_IBAN", ""))
    beneficiary = (getattr(settings, "DONATION_BANK_BENEFICIARY", "") or "").strip()
    bic = normalize_iban(getattr(settings, "DONATION_BANK_BIC", ""))
    if not iban or not beneficiary:
        return None
    if not iban_is_valid(iban):
        logger.warning("DONATION_BANK_IBAN fails its checksum; bank details hidden")
        return None
    if bic and not _BIC_SHAPE.match(bic):
        logger.warning("DONATION_BANK_BIC is malformed; shown without a BIC")
        bic = ""
    return BankTransferDetails(
        beneficiary=beneficiary[:EPC_MAX_NAME],
        iban=iban,
        bic=bic,
        reference=donation_reference(profile) if profile is not None else "",
    )
