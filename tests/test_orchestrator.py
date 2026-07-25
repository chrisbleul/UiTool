import json

import pytest

from uiflow.orchestrator import db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "orchestrator.db")
    db.init_db()


def test_create_and_claim_job():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})
    job = db.get_job(job_id)
    assert job["status"] == "queued"

    claimed = db.claim_next_job("worker-1")
    assert claimed["id"] == job_id
    assert claimed["status"] == "running"

    # a second worker racing for the same job must not also claim it
    assert db.claim_next_job("worker-2") is None


def test_create_job_stores_and_returns_sub_workflows_snapshot():
    sub = {"name": "teilprozess", "backend": "web", "steps": [{"action": "navigate", "url": "sub"}]}
    job_id = db.create_job(
        "demo", {"name": "demo", "backend": "web", "steps": []}, sub_workflows={"teilprozess": sub}
    )

    job = db.get_job(job_id)

    assert json.loads(job["sub_workflows_json"]) == {"teilprozess": sub}


def test_create_job_defaults_sub_workflows_to_empty_dict():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})

    job = db.get_job(job_id)

    assert json.loads(job["sub_workflows_json"]) == {}


def test_finish_job_sets_status_and_timestamp():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})
    db.claim_next_job("worker-1")

    db.finish_job(job_id, "success")

    job = db.get_job(job_id)
    assert job["status"] == "success"
    assert job["finished_at"] is not None


def test_claim_next_job_sets_an_initial_heartbeat():
    db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})

    claimed = db.claim_next_job("worker-1")

    assert claimed["last_heartbeat_at"] is not None
    assert claimed["last_heartbeat_at"] == claimed["started_at"]


def test_heartbeat_job_updates_the_timestamp():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})
    claimed = db.claim_next_job("worker-1")
    original = claimed["last_heartbeat_at"]

    with db.connect() as conn:
        conn.execute("UPDATE jobs SET last_heartbeat_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", job_id))
    db.heartbeat_job(job_id)

    assert db.get_job(job_id)["last_heartbeat_at"] != "2000-01-01T00:00:00+00:00"
    assert db.get_job(job_id)["last_heartbeat_at"] != original  # moved forward, not just restored


def test_heartbeat_job_is_a_no_op_for_a_job_that_is_not_running():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})  # still 'queued'

    db.heartbeat_job(job_id)

    assert db.get_job(job_id)["last_heartbeat_at"] is None


def _backdate_heartbeat(job_id: str, seconds_ago: float) -> None:
    from datetime import datetime, timedelta, timezone

    stale_at = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET last_heartbeat_at=? WHERE id=?", (stale_at, job_id))


def test_sweep_stale_jobs_marks_a_silent_one_shot_job_as_error():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})
    db.claim_next_job("worker-1")
    _backdate_heartbeat(job_id, seconds_ago=200)

    swept = db.sweep_stale_jobs(timeout_seconds=90)

    assert swept == [job_id]
    job = db.get_job(job_id)
    assert job["status"] == "error"
    assert "Heartbeat" in job["error_message"]
    assert job["finished_at"] is not None


def test_sweep_stale_jobs_leaves_a_recently_heartbeating_job_alone():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})
    db.claim_next_job("worker-1")  # heartbeat is "now" - well within any timeout

    swept = db.sweep_stale_jobs(timeout_seconds=90)

    assert swept == []
    assert db.get_job(job_id)["status"] == "running"


def test_sweep_stale_jobs_ignores_jobs_that_are_not_running():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})  # still 'queued', no heartbeat yet

    swept = db.sweep_stale_jobs(timeout_seconds=0)

    assert swept == []
    assert db.get_job(job_id)["status"] == "queued"


def test_sweep_stale_jobs_releases_its_in_progress_queue_item_without_consuming_a_retry():
    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"n": 1}, "max_retries": 3}])
    job_id = db.create_job(
        "process", {"name": "process", "backend": "web", "steps": []}, queue_name="invoices"
    )
    db.claim_next_job("worker-1")
    item = db.claim_next_queue_item(queue_id, job_id)
    assert item["status"] == "in_progress"
    _backdate_heartbeat(job_id, seconds_ago=200)

    db.sweep_stale_jobs(timeout_seconds=90)

    [released] = db.list_queue_items(queue_id)
    assert released["status"] == "new"  # handed back, not failed
    assert released["retry_count"] == 0  # no retry consumed - it was never actually attempted-and-failed
    assert released["locked_by"] is None
    # and it's claimable again, by a fresh worker
    assert db.claim_next_queue_item(queue_id, "worker-2") is not None


def test_logs_are_persisted_and_fetchable_incrementally():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})
    db.add_log(job_id, "INFO", "first")
    db.add_log(job_id, "INFO", "second")

    all_logs = db.get_logs(job_id)
    assert [line["message"] for line in all_logs] == ["first", "second"]

    since_first = db.get_logs(job_id, since_id=all_logs[0]["id"])
    assert [line["message"] for line in since_first] == ["second"]


def test_stop_and_resume_controls():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})

    assert db.is_stop_requested(job_id) is False
    db.request_stop(job_id)
    assert db.is_stop_requested(job_id) is True

    assert db.wait_and_clear_resume(job_id) is False
    db.request_resume(job_id)
    assert db.wait_and_clear_resume(job_id) is True
    assert db.wait_and_clear_resume(job_id) is False  # cleared after first read


def test_set_paused_reflected_in_controls():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})
    db.set_paused(job_id, 2, "click", path="1.then.0")

    controls = db.get_controls(job_id)
    assert controls["paused_step_index"] == 2
    assert controls["paused_step_action"] == "click"
    assert controls["paused_step_path"] == "1.then.0"


def test_queue_create_is_idempotent_by_name():
    first = db.create_queue("invoices")
    second = db.create_queue("invoices")
    assert first == second


def test_queue_item_claim_and_complete_success():
    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"n": 1}}])

    item = db.claim_next_queue_item(queue_id, "job-1")
    assert item["status"] == "in_progress"

    db.complete_queue_item(item["id"], True, output={"ok": True})

    [stored] = db.list_queue_items(queue_id)
    assert stored["status"] == "success"


@pytest.fixture
def no_retry_backoff(monkeypatch):
    """Drops the retry backoff to zero so retry *semantics* can be tested
    without the test having to wait out a real delay."""
    monkeypatch.setattr(db, "retry_delay_seconds", lambda retry_count: 0.0)


def test_queue_item_retries_before_failing(no_retry_backoff):
    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"n": 1}, "max_retries": 1}])

    item = db.claim_next_queue_item(queue_id, "job-1")
    assert db.complete_queue_item(item["id"], False, error_message="boom") == "new"
    [after_first_failure] = db.list_queue_items(queue_id)
    assert after_first_failure["status"] == "new"  # retry_count(1) <= max_retries(1)

    item_again = db.claim_next_queue_item(queue_id, "job-1")
    assert db.complete_queue_item(item_again["id"], False, error_message="boom again") == "failed"
    [after_second_failure] = db.list_queue_items(queue_id)
    assert after_second_failure["status"] == "failed"  # retry_count(2) > max_retries(1)


def test_complete_queue_item_permanent_fails_immediately_without_a_retry(no_retry_backoff):
    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"n": 1}, "max_retries": 3}])
    item = db.claim_next_queue_item(queue_id, "job-1")

    status = db.complete_queue_item(item["id"], False, error_message="ungültig", permanent=True)

    assert status == "failed"
    [stored] = db.list_queue_items(queue_id)
    assert stored["status"] == "failed"
    assert stored["retry_count"] == 0  # no retry was consumed
    assert stored["error_message"] == "ungültig"
    assert db.claim_next_queue_item(queue_id, "job-2") is None  # not reclaimable


def test_failed_item_is_not_immediately_reclaimable():
    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"n": 1}, "max_retries": 3}])

    item = db.claim_next_queue_item(queue_id, "job-1")
    db.complete_queue_item(item["id"], False, error_message="boom")

    # Still 'new' (a retry is due), but held back by the backoff rather than
    # being handed straight back to the worker that just failed it.
    [stored] = db.list_queue_items(queue_id)
    assert stored["status"] == "new"
    assert db.claim_next_queue_item(queue_id, "job-1") is None
    assert 0 < db.seconds_until_next_retry(queue_id) <= db.RETRY_BACKOFF_BASE_SECONDS


def test_retry_backoff_grows_exponentially_and_is_capped():
    delays = [db.retry_delay_seconds(n) for n in (1, 2, 3)]
    assert delays == [
        db.RETRY_BACKOFF_BASE_SECONDS,
        db.RETRY_BACKOFF_BASE_SECONDS * 2,
        db.RETRY_BACKOFF_BASE_SECONDS * 4,
    ]
    assert db.retry_delay_seconds(99) == db.RETRY_BACKOFF_MAX_SECONDS


def test_backed_off_item_is_claimable_once_its_retry_time_passes(no_retry_backoff):
    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"n": 1}, "max_retries": 3}])

    item = db.claim_next_queue_item(queue_id, "job-1")
    db.complete_queue_item(item["id"], False, error_message="boom")

    assert db.seconds_until_next_retry(queue_id) == 0.0
    assert db.claim_next_queue_item(queue_id, "job-1") is not None


def test_seconds_until_next_retry_is_none_for_an_exhausted_queue():
    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"n": 1}, "max_retries": 0}])

    item = db.claim_next_queue_item(queue_id, "job-1")
    db.complete_queue_item(item["id"], False, error_message="boom")

    assert db.list_queue_items(queue_id)[0]["status"] == "failed"
    assert db.seconds_until_next_retry(queue_id) is None


def test_release_queue_item_returns_it_without_consuming_a_retry():
    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"n": 1}}])
    item = db.claim_next_queue_item(queue_id, "job-1")

    db.release_queue_item(item["id"])

    [stored] = db.list_queue_items(queue_id)
    assert stored["status"] == "new"
    assert stored["retry_count"] == 0
    assert stored["locked_by"] is None
    assert db.claim_next_queue_item(queue_id, "job-2") is not None  # immediately available again


def test_queue_item_claim_is_race_safe():
    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"n": 1}}])

    first = db.claim_next_queue_item(queue_id, "job-1")
    second = db.claim_next_queue_item(queue_id, "job-2")
    assert first is not None
    assert second is None


class _RecordingFakeBackend:
    def __init__(self):
        self.calls = []

    def navigate(self, url):
        self.calls.append(url)

    def close(self):
        pass


class _HeartbeatRecordingStore:
    """A minimal fake `store` (see worker.py's Store note) covering only what
    a one-shot _run_job needs, so the heartbeat thread can be exercised
    without a real orchestrator.db at all - the same "fake the dependency"
    approach test_remote_store.py uses for RemoteStore itself."""

    def __init__(self):
        self.heartbeats = []
        self.finished = None

    def is_stop_requested(self, job_id):
        return False

    def set_paused(self, job_id, index, action, variables=None, path=None):
        pass

    def wait_and_clear_resume(self, job_id):
        return True

    def get_global_variables(self):
        return {}

    def add_log(self, job_id, level, message):
        pass

    def heartbeat_job(self, job_id):
        self.heartbeats.append(job_id)

    def finish_job(self, job_id, status, error_message=None):
        self.finished = (status, error_message)


def test_run_job_heartbeats_periodically_while_a_step_is_slow(monkeypatch):
    import time as time_module

    from uiflow.orchestrator import worker

    class _SlowBackend:
        def navigate(self, url):
            time_module.sleep(0.15)

        def close(self):
            pass

    monkeypatch.setattr(worker, "_make_backend", lambda wf: _SlowBackend())
    store = _HeartbeatRecordingStore()
    job = {
        "id": "job-1",
        "name": "demo",
        "workflow_json": json.dumps(
            {"name": "demo", "backend": "web", "steps": [{"action": "navigate", "url": "https://x"}]}
        ),
        "sub_workflows_json": "{}",
        "queue_name": None,
    }

    worker._run_job(job, store=store, heartbeat_interval=0.02)

    assert len(store.heartbeats) >= 2  # ~0.15s of run time / 0.02s interval
    assert store.finished == ("success", None)


def test_run_job_stops_heartbeating_once_the_job_finishes(monkeypatch):
    import time as time_module

    from uiflow.orchestrator import worker

    class _FastBackend:
        def navigate(self, url):
            pass

        def close(self):
            pass

    monkeypatch.setattr(worker, "_make_backend", lambda wf: _FastBackend())
    store = _HeartbeatRecordingStore()
    job = {
        "id": "job-1",
        "name": "demo",
        "workflow_json": json.dumps(
            {"name": "demo", "backend": "web", "steps": [{"action": "navigate", "url": "https://x"}]}
        ),
        "sub_workflows_json": "{}",
        "queue_name": None,
    }

    worker._run_job(job, store=store, heartbeat_interval=0.02)
    count_right_after = len(store.heartbeats)
    time_module.sleep(0.1)  # several more intervals' worth of time

    assert len(store.heartbeats) == count_right_after  # the thread really stopped, not just slowed


def test_queue_driven_job_seeds_item_variables_end_to_end(monkeypatch):
    from uiflow.orchestrator import worker

    fake_backend = _RecordingFakeBackend()
    monkeypatch.setattr(worker, "_make_backend", lambda name: fake_backend)

    queue_id = db.create_queue("greetings")
    db.add_queue_items(queue_id, [{"payload": {"name": "Anna"}}, {"payload": {"name": "Bert"}}])

    job_id = db.create_job(
        "greet",
        {
            "name": "greet",
            "backend": "web",
            "steps": [{"action": "navigate", "url": "https://x/?name={item.name}"}],
        },
        queue_name="greetings",
    )
    job = db.claim_next_job("test-worker")

    worker._run_job(job)

    assert fake_backend.calls == ["https://x/?name=Anna", "https://x/?name=Bert"]
    assert db.get_job(job_id)["status"] == "success"
    assert all(i["status"] == "success" for i in db.list_queue_items(queue_id))


class _SelectivelyFailingBackend:
    """Fails whenever the navigated URL contains 'bad'."""

    def __init__(self, fail_times: int | None = None):
        self.calls = []
        self.fail_times = fail_times  # None = fail every matching call

    def navigate(self, url):
        self.calls.append(url)
        if "bad" not in url:
            return
        if self.fail_times is None:
            raise RuntimeError("nope")
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient")

    def close(self):
        pass


def _queue_job(queue_name: str, items: list[dict], max_retries: int = 0) -> tuple[str, int]:
    queue_id = db.create_queue(queue_name)
    db.add_queue_items(queue_id, [{"payload": p, "max_retries": max_retries} for p in items])
    job_id = db.create_job(
        "greet",
        {
            "name": "greet",
            "backend": "web",
            "steps": [{"action": "navigate", "url": "https://x/?name={item.name}"}],
        },
        queue_name=queue_name,
    )
    return job_id, queue_id


def test_queue_job_reports_error_when_an_item_fails_permanently(monkeypatch):
    from uiflow.orchestrator import worker

    monkeypatch.setattr(worker, "_make_backend", lambda name: _SelectivelyFailingBackend())
    job_id, queue_id = _queue_job("mixed", [{"name": "good"}, {"name": "bad"}])

    worker._run_job(db.claim_next_job("test-worker"))

    job = db.get_job(job_id)
    assert job["status"] == "error"  # NOT success - one item never went through
    assert "1 of 2" in job["error_message"]
    by_status = {i["status"] for i in db.list_queue_items(queue_id)}
    assert by_status == {"success", "failed"}  # the good item was still processed


def test_business_error_marks_an_item_failed_immediately_without_consuming_a_retry():
    from uiflow.orchestrator import worker

    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"betrag": -5}, "max_retries": 3}])
    job_id = db.create_job(
        "invoices",
        {
            "name": "invoices",
            "backend": "web",
            "steps": [
                {
                    "action": "if",
                    "condition": "item['betrag'] < 0",
                    "then": [{"action": "fail", "message": "Ungültiger Betrag", "type": "business"}],
                }
            ],
        },
        queue_name="invoices",
    )

    worker._run_job(db.claim_next_job("test-worker"))

    job = db.get_job(job_id)
    assert job["status"] == "error"
    [item] = db.list_queue_items(queue_id)
    assert item["status"] == "failed"
    assert item["retry_count"] == 0  # a business error must not consume a retry
    assert "Ungültiger Betrag" in item["error_message"]


def test_queue_job_reports_success_when_a_retry_eventually_succeeds(monkeypatch, no_retry_backoff):
    from uiflow.orchestrator import worker

    backend = _SelectivelyFailingBackend(fail_times=1)
    monkeypatch.setattr(worker, "_make_backend", lambda name: backend)
    job_id, queue_id = _queue_job("flaky", [{"name": "bad"}], max_retries=2)

    worker._run_job(db.claim_next_job("test-worker"))

    assert db.get_job(job_id)["status"] == "success"
    [item] = db.list_queue_items(queue_id)
    assert item["status"] == "success"
    assert item["retry_count"] == 1
    assert len(backend.calls) == 2  # failed once, then retried


def test_retried_item_is_counted_once_in_the_job_error(monkeypatch, no_retry_backoff):
    from uiflow.orchestrator import worker

    monkeypatch.setattr(worker, "_make_backend", lambda name: _SelectivelyFailingBackend())
    job_id, queue_id = _queue_job("retries", [{"name": "bad"}], max_retries=2)

    worker._run_job(db.claim_next_job("test-worker"))

    # Three attempts, but only one item - the message must count items.
    assert db.get_job(job_id)["error_message"] == "1 of 1 queue item(s) failed permanently"
    assert db.list_queue_items(queue_id)[0]["retry_count"] == 3


def test_stopped_queue_job_releases_the_in_flight_item(monkeypatch):
    from uiflow.orchestrator import worker

    class _StoppingBackend:
        def __init__(self, job_id):
            self.job_id = job_id

        def navigate(self, url):
            db.request_stop(self.job_id)  # stop arrives mid-item

        def close(self):
            pass

    queue_id = db.create_queue("stopme")
    db.add_queue_items(queue_id, [{"payload": {"name": "a"}}, {"payload": {"name": "b"}}])
    # Two steps: the stop lands while step 1 runs, so the engine sees it before
    # step 2 and cancels the item mid-run - which is the case being tested.
    job_id = db.create_job(
        "greet",
        {
            "name": "greet",
            "backend": "web",
            "steps": [
                {"action": "navigate", "url": "https://x/?name={item.name}"},
                {"action": "navigate", "url": "https://x/second"},
            ],
        },
        queue_name="stopme",
    )
    monkeypatch.setattr(worker, "_make_backend", lambda name: _StoppingBackend(job_id))

    worker._run_job(db.claim_next_job("test-worker"))

    assert db.get_job(job_id)["status"] == "cancelled"
    # Nothing was learned about either item, so neither may be marked failed and
    # neither may have burned a retry.
    for item in db.list_queue_items(queue_id):
        assert item["status"] == "new"
        assert item["retry_count"] == 0
        assert item["locked_by"] is None


def test_credential_names_are_listed_without_storing_the_secret():
    db.add_credential_name("smtp_password")
    db.add_credential_name("imap_password")

    assert db.list_credential_names() == ["imap_password", "smtp_password"]


def test_add_credential_name_is_idempotent():
    db.add_credential_name("x")
    db.add_credential_name("x")

    assert db.list_credential_names() == ["x"]


def test_delete_credential_name():
    db.add_credential_name("x")
    db.delete_credential_name("x")

    assert db.list_credential_names() == []


def test_create_and_list_schedules():
    workflow = {"name": "demo", "backend": "web", "steps": []}
    schedule_id = db.create_schedule("nightly", "0 2 * * *", workflow, queue_name="invoices")

    [schedule] = db.list_schedules()
    assert schedule["id"] == schedule_id
    assert schedule["name"] == "nightly"
    assert schedule["cron_expr"] == "0 2 * * *"
    assert schedule["queue_name"] == "invoices"
    assert schedule["enabled"] == 1
    assert schedule["last_run_at"] is None


def test_set_schedule_enabled_toggles_flag():
    schedule_id = db.create_schedule("nightly", "0 2 * * *", {"name": "demo", "backend": "web", "steps": []})

    db.set_schedule_enabled(schedule_id, False)
    assert db.get_schedule(schedule_id)["enabled"] == 0

    db.set_schedule_enabled(schedule_id, True)
    assert db.get_schedule(schedule_id)["enabled"] == 1


def test_mark_schedule_ran_sets_last_run_at():
    schedule_id = db.create_schedule("nightly", "0 2 * * *", {"name": "demo", "backend": "web", "steps": []})

    db.mark_schedule_ran(schedule_id)

    assert db.get_schedule(schedule_id)["last_run_at"] is not None


def test_delete_schedule():
    schedule_id = db.create_schedule("nightly", "0 2 * * *", {"name": "demo", "backend": "web", "steps": []})

    db.delete_schedule(schedule_id)

    assert db.list_schedules() == []


def test_schedule_is_due_when_next_cron_fire_is_in_the_past():
    from uiflow.orchestrator.worker import _schedule_is_due

    schedule = {
        "cron_expr": "* * * * *",  # every minute
        "last_run_at": None,
        "created_at": "2000-01-01T00:00:00+00:00",
    }

    assert _schedule_is_due(schedule) is True


def test_schedule_is_not_due_right_after_being_marked_ran():
    from datetime import datetime, timezone

    from uiflow.orchestrator.worker import _schedule_is_due

    schedule = {
        "cron_expr": "0 0 1 1 *",  # once a year, Jan 1st
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "created_at": "2000-01-01T00:00:00+00:00",
    }

    assert _schedule_is_due(schedule) is False


def test_run_scheduler_loop_snapshots_referenced_sub_workflows(monkeypatch, tmp_path):
    import threading

    from uiflow.models import Step, Workflow
    from uiflow.orchestrator import worker

    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    monkeypatch.setattr("uiflow.models.WORKFLOWS_DIR", workflows_dir)
    Workflow(name="teilprozess", backend="web", steps=[Step("navigate", {"url": "sub"})]).save(
        workflows_dir / "teilprozess.yaml"
    )

    schedule_id = db.create_schedule(
        "nightly",
        "* * * * *",
        {"name": "demo", "backend": "web", "steps": [{"action": "run_workflow", "workflow": "teilprozess"}]},
    )
    with db.connect() as conn:
        conn.execute("UPDATE schedules SET created_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", schedule_id))

    stop_event = threading.Event()
    original_mark_ran = db.mark_schedule_ran

    def mark_ran_and_stop(sid):
        original_mark_ran(sid)
        stop_event.set()

    monkeypatch.setattr(db, "mark_schedule_ran", mark_ran_and_stop)

    worker.run_scheduler_loop(poll_interval=0, stop_event=stop_event)

    [job] = db.list_jobs()
    assert json.loads(job["sub_workflows_json"])["teilprozess"]["steps"] == [{"action": "navigate", "url": "sub"}]


def test_run_scheduler_loop_enqueues_a_job_for_a_due_schedule(monkeypatch):
    import threading

    from uiflow.orchestrator import worker

    schedule_id = db.create_schedule(
        "nightly", "* * * * *", {"name": "demo", "backend": "web", "steps": []}, queue_name="q1"
    )
    # Force created_at far into the past so the schedule is deterministically due right
    # away - anchoring it to real "now" instead would make the test wait for (and busy-poll
    # until) the next real minute boundary, which is both slow and flaky.
    with db.connect() as conn:
        conn.execute("UPDATE schedules SET created_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", schedule_id))

    stop_event = threading.Event()
    original_mark_ran = db.mark_schedule_ran

    def mark_ran_and_stop(sid):
        original_mark_ran(sid)
        stop_event.set()  # stop the loop right after the one due schedule fired

    monkeypatch.setattr(db, "mark_schedule_ran", mark_ran_and_stop)

    worker.run_scheduler_loop(poll_interval=0, stop_event=stop_event)

    [job] = db.list_jobs()
    assert job["queue_name"] == "q1"
    assert db.get_schedule(schedule_id)["last_run_at"] is not None


def test_run_scheduler_loop_sweeps_stale_jobs_each_iteration(monkeypatch):
    import threading

    from uiflow.orchestrator import worker

    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})
    db.claim_next_job("worker-1")
    _backdate_heartbeat(job_id, seconds_ago=200)

    stop_event = threading.Event()
    original_sweep = db.sweep_stale_jobs

    def sweep_and_stop(timeout_seconds):
        result = original_sweep(timeout_seconds)
        stop_event.set()
        return result

    monkeypatch.setattr(db, "sweep_stale_jobs", sweep_and_stop)

    worker.run_scheduler_loop(poll_interval=0, stop_event=stop_event, stale_job_timeout=90)

    assert db.get_job(job_id)["status"] == "error"


# --- audit log ---------------------------------------------------------------


def test_add_and_list_audit_entries():
    db.add_audit_entry("alice", "admin", "POST /api/globals", 200)
    db.add_audit_entry(None, None, "POST /api/globals", 400)

    entries = db.list_audit_entries()

    assert len(entries) == 2
    assert entries[0]["action"] == "POST /api/globals"
    assert entries[0]["status_code"] == 400
    assert entries[0]["username"] is None
    assert entries[1]["username"] == "alice"
    assert entries[1]["role"] == "admin"


def test_list_audit_entries_newest_first_and_respects_limit():
    for i in range(5):
        db.add_audit_entry("alice", "admin", f"POST /api/x{i}", 200)

    entries = db.list_audit_entries(limit=2)

    assert [e["action"] for e in entries] == ["POST /api/x4", "POST /api/x3"]


# --- global variables -------------------------------------------------------


def test_global_variables_round_trip_with_their_type():
    db.set_global_variable("basis_url", "https://erp.example.com")
    db.set_global_variable("max_betrag", 5000)
    db.set_global_variable("empfaenger", ["a@x.de", "b@x.de"])

    assert db.get_global_variables() == {
        "basis_url": "https://erp.example.com",
        "max_betrag": 5000,
        "empfaenger": ["a@x.de", "b@x.de"],
    }


def test_setting_a_global_twice_updates_it_in_place():
    db.set_global_variable("basis_url", "https://alt")
    db.set_global_variable("basis_url", "https://neu")

    assert db.get_global_variables() == {"basis_url": "https://neu"}
    assert len(db.list_global_variables()) == 1


def test_delete_global_variable():
    db.set_global_variable("x", 1)
    db.delete_global_variable("x")

    assert db.get_global_variables() == {}


def test_a_job_run_sees_the_global_variables(monkeypatch):
    from uiflow.orchestrator import worker

    seen = {}

    class _UrlRecordingBackend:
        def navigate(self, url):
            seen["url"] = url

        def close(self):
            pass

    monkeypatch.setattr(worker, "_make_backend", lambda name: _UrlRecordingBackend())
    db.set_global_variable("basis_url", "https://erp.example.com")
    job_id = db.create_job(
        "demo",
        {"name": "demo", "backend": "web", "steps": [{"action": "navigate", "url": "{global.basis_url}/x"}]},
    )

    worker._run_job(db.claim_next_job("test-worker"))

    assert db.get_job(job_id)["status"] == "success"
    assert seen["url"] == "https://erp.example.com/x"


# --- users (opt-in multi-user/RBAC, see studio/app.py) -----------------------


def test_any_users_exist_is_false_until_one_is_created():
    assert db.any_users_exist() is False

    db.create_user("alice", "hash", "admin")

    assert db.any_users_exist() is True


def test_create_and_get_user():
    db.create_user("alice", "hashed-pw", "admin")

    user = db.get_user("alice")

    assert user["username"] == "alice"
    assert user["password_hash"] == "hashed-pw"
    assert user["role"] == "admin"


def test_get_user_returns_none_for_an_unknown_username():
    assert db.get_user("nobody") is None


def test_create_user_rejects_an_unknown_role():
    with pytest.raises(ValueError):
        db.create_user("alice", "hash", "superadmin")


def test_list_users_never_includes_the_password_hash():
    db.create_user("bob", "super-secret-hash", "viewer")
    db.create_user("alice", "another-hash", "admin")

    users = db.list_users()

    assert users == [
        {"username": "alice", "role": "admin", "created_at": users[0]["created_at"]},
        {"username": "bob", "role": "viewer", "created_at": users[1]["created_at"]},
    ]
    assert all("password_hash" not in u for u in users)


def test_set_user_role_updates_it():
    db.create_user("alice", "hash", "viewer")

    db.set_user_role("alice", "admin")

    assert db.get_user("alice")["role"] == "admin"


def test_set_user_role_rejects_an_unknown_role():
    db.create_user("alice", "hash", "viewer")

    with pytest.raises(ValueError):
        db.set_user_role("alice", "superadmin")


def test_set_user_password_updates_the_hash():
    db.create_user("alice", "old-hash", "viewer")

    db.set_user_password("alice", "new-hash")

    assert db.get_user("alice")["password_hash"] == "new-hash"


def test_delete_user_removes_it():
    db.create_user("alice", "hash", "admin")

    db.delete_user("alice")

    assert db.get_user("alice") is None
    assert db.any_users_exist() is False
