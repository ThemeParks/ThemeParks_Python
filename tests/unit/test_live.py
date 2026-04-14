from themeparks._ergonomic.live import current_wait_time, iter_queues
from themeparks._generated.models import EntityLiveData


def make_entry(queue: dict):
    return EntityLiveData.model_validate(
        {
            "id": "r1",
            "name": "Ride 1",
            "entityType": "ATTRACTION",
            "status": "OPERATING",
            "lastUpdated": "2026-04-14T12:00:00Z",
            "queue": queue,
        }
    )


def test_current_wait_time_standby():
    e = make_entry({"STANDBY": {"waitTime": 30}})
    assert current_wait_time(e) == 30


def test_current_wait_time_null():
    e = make_entry({"STANDBY": {"waitTime": None}})
    assert current_wait_time(e) is None


def test_current_wait_time_missing_standby():
    e = make_entry({"BOARDING_GROUP": {"currentGroupStart": 50}})
    assert current_wait_time(e) is None


def test_iter_queues_yields_typed_entries():
    e = make_entry({"STANDBY": {"waitTime": 30}})
    out = list(iter_queues(e))
    assert len(out) == 1
    assert out[0]["type"] == "STANDBY"
    assert out[0]["waitTime"] == 30
