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


def test_finish_job_sets_status_and_timestamp():
    job_id = db.create_job("demo", {"name": "demo", "backend": "web", "steps": []})
    db.claim_next_job("worker-1")

    db.finish_job(job_id, "success")

    job = db.get_job(job_id)
    assert job["status"] == "success"
    assert job["finished_at"] is not None


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
    db.set_paused(job_id, 2, "click")

    controls = db.get_controls(job_id)
    assert controls["paused_step_index"] == 2
    assert controls["paused_step_action"] == "click"


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


def test_queue_item_retries_before_failing():
    queue_id = db.create_queue("invoices")
    db.add_queue_items(queue_id, [{"payload": {"n": 1}, "max_retries": 1}])

    item = db.claim_next_queue_item(queue_id, "job-1")
    db.complete_queue_item(item["id"], False, error_message="boom")
    [after_first_failure] = db.list_queue_items(queue_id)
    assert after_first_failure["status"] == "new"  # retry_count(1) <= max_retries(1)

    item_again = db.claim_next_queue_item(queue_id, "job-1")
    db.complete_queue_item(item_again["id"], False, error_message="boom again")
    [after_second_failure] = db.list_queue_items(queue_id)
    assert after_second_failure["status"] == "failed"  # retry_count(2) > max_retries(1)


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
