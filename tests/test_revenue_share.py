import pytest
from inkora.revenue_share import split_gross


def test_ten_rupee_payment_allocates_nine_to_owner_one_to_inkora():
    result = split_gross(1000)
    assert result.owner_paise == 900 and result.inkora_paise == 100
    assert result.inkora_basis_points == 1000


@pytest.mark.parametrize('gross', [1, 5, 25, 250, 999, 1000, 25000000])
def test_integer_allocation_conserves_every_paisa(gross):
    result = split_gross(gross)
    assert result.owner_paise + result.inkora_paise == gross
    assert result.owner_paise >= 0 and result.inkora_paise >= 0


def test_half_paisa_rounding_is_explicit_and_deterministic():
    result = split_gross(25)
    assert result.inkora_paise == 3 and result.owner_paise == 22


@pytest.mark.parametrize('gross', [True, False, 0, -1, 10.0, '1000', None])
def test_float_and_invalid_amounts_are_rejected(gross):
    with pytest.raises(ValueError):
        split_gross(gross)


@pytest.mark.parametrize('rate', [True, -1, 10001, 10.0, '1000', None])
def test_invalid_rate_rejected(rate):
    with pytest.raises(ValueError):
        split_gross(1000, inkora_basis_points=rate)


def test_snapshotted_rate_can_be_reused_without_mutating_prior_allocation():
    original = split_gross(1000)
    other = split_gross(1000, inkora_basis_points=2000)
    assert original.inkora_paise == 100 and other.inkora_paise == 200
    assert split_gross(1000, inkora_basis_points=original.inkora_basis_points) == original
