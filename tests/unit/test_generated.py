"""Invariants the regenerate.py post-gen patches must keep true.

If datamodel-codegen output ever drifts and silently breaks the post-gen
patches, these assertions fail loudly in CI.
"""

from themeparks._generated.models import LiveQueue, PriceData


def test_live_queue_uses_natural_attr_names():
    fields = set(LiveQueue.model_fields.keys())
    assert "STANDBY" in fields
    assert "SINGLE_RIDER" in fields
    assert "RETURN_TIME" in fields
    assert "PAID_RETURN_TIME" in fields
    assert "BOARDING_GROUP" in fields
    assert "PAID_STANDBY" in fields


def test_live_queue_has_no_aliased_names():
    fields = set(LiveQueue.model_fields.keys())
    assert "STANDBY_1" not in fields


def test_price_amount_accepts_null():
    """null = the item costs money but no amount is published; 0 = genuinely free.

    Both used to arrive as 0, so an unknown price was indistinguishable from a
    free one. The spec marks amount required and nullable, which
    datamodel-codegen does not honour for required fields — hence the
    NULLABLE_PATCHES entry. Without it a single null amount on one attraction
    makes pydantic reject the entire live response.
    """
    assert (
        PriceData.model_validate(
            {"amount": None, "currency": "JPY", "formatted": "Unknown"}
        ).amount
        is None
    )
    assert (
        PriceData.model_validate({"amount": 0, "currency": "USD"}).amount == 0.0
    )
    assert (
        PriceData.model_validate({"amount": 2100, "currency": "JPY"}).amount == 2100.0
    )
