import pytest

from scripts.sync_us_nasdaq100_reference import validated_snapshots


def payload(count: int) -> bytes:
    members = ",".join(f"T{i:03d}" for i in range(count))
    return f'date,tickers\n2026-01-01,"{members}"\n'.encode()


def test_nasdaq100_reference_accepts_multi_class_constituent_count():
    snapshots = validated_snapshots(payload(101))
    assert len(snapshots[0][1]) == 101


@pytest.mark.parametrize("count", [89, 121])
def test_nasdaq100_reference_rejects_implausible_constituent_count(count):
    with pytest.raises(ValueError):
        validated_snapshots(payload(count))
