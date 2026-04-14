import httpx

from themeparks import ThemeParks


def sequential_handler(responses):
    idx = {"i": 0}

    def h(req):
        r = responses[idx["i"]]
        idx["i"] += 1
        return httpx.Response(200, json=r)

    return h


def test_get_calls_entity_path():
    body = {
        "id": "abc",
        "name": "X",
        "entityType": "PARK",
        "timezone": "UTC",
    }
    tp = ThemeParks(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
        cache=False,
    )
    e = tp.entity("abc").get()
    assert e.id == "abc"


def test_walk_bfs_and_dedupes():
    responses = [
        {
            "id": "root",
            "children": [
                {"id": "a", "name": "A", "entityType": "PARK"},
                {"id": "b", "name": "B", "entityType": "PARK"},
            ],
        },
        {
            "id": "a",
            "children": [{"id": "c", "name": "C", "entityType": "ATTRACTION"}],
        },
        {
            "id": "b",
            "children": [{"id": "c", "name": "C", "entityType": "ATTRACTION"}],
        },
        {"id": "c", "children": []},
    ]
    tp = ThemeParks(
        transport=httpx.MockTransport(sequential_handler(responses)), cache=False
    )
    ids = [c.id for c in tp.entity("root").walk()]
    assert ids == ["a", "b", "c"]
