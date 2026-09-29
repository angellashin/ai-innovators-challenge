from deploy.aws import auto_deploy
from deploy.aws.auto_deploy import latest_ci_approved


SHA = "a" * 40


def run(**changes):
    value = {
        "id": 1,
        "name": "CI",
        "event": "push",
        "head_branch": "main",
        "head_sha": SHA,
        "run_number": 1,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
    }
    return value | changes


def test_only_latest_successful_main_push_can_deploy():
    assert latest_ci_approved([run()], SHA)
    assert not latest_ci_approved([run(head_sha="b" * 40)], SHA)
    assert not latest_ci_approved([run(event="pull_request")], SHA)
    assert not latest_ci_approved([run(status="in_progress", conclusion=None)], SHA)
    assert not latest_ci_approved([run(conclusion="failure")], SHA)


def test_newer_attempt_must_pass_even_if_earlier_one_passed():
    runs = [run(run_attempt=1), run(id=2, run_attempt=2, conclusion="failure")]
    assert not latest_ci_approved(runs, SHA)


def test_pending_ci_cannot_change_checkout_or_containers(monkeypatch, tmp_path):
    commands = []

    def fake_git(*args):
        commands.append(args)
        return {
            ("branch", "--show-current"): "main",
            ("status", "--porcelain"): "",
            ("fetch", "origin", "main"): "",
            ("rev-parse", "FETCH_HEAD"): SHA,
            ("rev-parse", "HEAD"): "b" * 40,
        }[args]

    monkeypatch.setattr(auto_deploy, "git", fake_git)
    monkeypatch.setattr(auto_deploy, "ci_approved", lambda sha: False)
    monkeypatch.setattr(auto_deploy, "STATE_FILE", tmp_path / "last-deployed-sha")
    monkeypatch.setattr(
        auto_deploy.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Docker must not run")),
    )

    auto_deploy.main()

    assert ("merge", "--ff-only", "FETCH_HEAD") not in commands
