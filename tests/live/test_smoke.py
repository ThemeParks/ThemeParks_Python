import pytest

from themeparks import ThemeParks
from themeparks._generated.models import EntityType

WDW_ID = "e957da41-3552-4cf6-b636-5babc5cbc4e5"
MK_ID = "75ea578a-adc8-4116-a54d-dccb60765ef9"
DLP_ID = "e8d0207f-da8a-4048-bec8-117aa946b2c2"


@pytest.fixture
def tp():
    with ThemeParks(cache=False) as c:
        yield c


def test_destinations(tp):
    assert len(tp.destinations.list().destinations) > 0


def test_wdw_entity(tp):
    e = tp.entity(WDW_ID).get()
    assert e.entityType == EntityType.DESTINATION


def test_mk_live_parses(tp):
    live = tp.entity(MK_ID).live()
    assert live.liveData is not None


def test_dlp_schedule(tp):
    s = tp.entity(DLP_ID).schedule.upcoming()
    assert s.schedule is not None
