"""Invariants the regenerate.py post-gen patches must keep true.

If datamodel-codegen output ever drifts and silently breaks the post-gen
patches, these assertions fail loudly in CI.
"""
from themeparks._generated.models import LiveQueue


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
