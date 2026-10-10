"""Gross INR allocation contract. Calculation does not transfer or settle money."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RevenueSplit:
    gross_paise: int
    owner_paise: int
    inkora_paise: int
    inkora_basis_points: int


def split_gross(gross_paise, *, inkora_basis_points=1000):
    """Allocate 90/10 by default, in integer paise with half-up share rounding.

    Fee/GST/refund deductions are separate ledger entries, not hidden inside this
    gross calculation. Snapshot the agreed rate when authorizing each payment;
    later pricing or policy changes must not rewrite historical allocations.
    """
    if isinstance(gross_paise, bool) or not isinstance(gross_paise, int) or gross_paise <= 0:
        raise ValueError('Gross amount must be a positive integer number of paise.')
    if (isinstance(inkora_basis_points, bool) or not isinstance(inkora_basis_points, int)
            or not 0 <= inkora_basis_points <= 10000):
        raise ValueError('Share must be integer basis points between 0 and 10000.')
    inkora = (gross_paise * inkora_basis_points + 5000) // 10000
    return RevenueSplit(gross_paise, gross_paise - inkora, inkora, inkora_basis_points)
