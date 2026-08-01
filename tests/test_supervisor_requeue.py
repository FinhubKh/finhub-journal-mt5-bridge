from workers.supervisor import mark_requeue, should_requeue


def test_should_requeue_and_mark_requeue():
    assert should_requeue({}) is True
    assert should_requeue({"attempt": 0}) is True
    assert should_requeue({"attempt": 1}) is False

    job = {"job_id": "j1", "login": "1"}
    requeued = mark_requeue(job)
    assert requeued["attempt"] == 1
    assert requeued["job_id"] == "j1"
    assert "attempt" not in job


def test_run_worker_import_does_not_load_metatrader5():
    import sys

    sys.modules.pop("MetaTrader5", None)
    import workers.run_worker  # noqa: F401

    assert "MetaTrader5" not in sys.modules
