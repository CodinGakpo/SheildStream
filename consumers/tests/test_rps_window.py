from alerts.rps_window import RollingRpsCounter


def test_completed_second_is_counted_and_drained():
    clock = [100.0]
    c = RollingRpsCounter(now_fn=lambda: clock[0])
    for _ in range(5):
        c.record("/a")
    # Still inside second 100 — nothing has completed yet.
    assert c.tick() == []
    clock[0] = 101.0  # second 100 is now fully elapsed
    assert c.tick() == [("/a", 5.0)]


def test_drained_bucket_is_not_yielded_again():
    clock = [100.0]
    c = RollingRpsCounter(now_fn=lambda: clock[0])
    c.record("/a")
    clock[0] = 101.0
    assert c.tick() == [("/a", 1.0)]
    assert c.tick() == []  # already drained


def test_multiple_endpoints_in_one_second():
    clock = [100.0]
    c = RollingRpsCounter(now_fn=lambda: clock[0])
    c.record("/a")
    c.record("/a")
    c.record("/b")
    clock[0] = 101.0
    assert sorted(c.tick()) == [("/a", 2.0), ("/b", 1.0)]


def test_stalled_loop_drains_each_completed_second_separately():
    clock = [100.0]
    c = RollingRpsCounter(now_fn=lambda: clock[0])
    c.record("/a")
    clock[0] = 101.0
    c.record("/a")
    c.record("/a")
    clock[0] = 103.0  # loop stalled; seconds 100 and 101 both completed
    assert c.tick() == [("/a", 1.0), ("/a", 2.0)]  # oldest second first
