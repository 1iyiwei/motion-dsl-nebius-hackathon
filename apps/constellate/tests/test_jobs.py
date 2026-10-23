from constellate import jobs


def test_create_args_always_sets_cpu_platform():
    a = jobs.create_args(jobs.hello_spec(), "project-x", dry_run=True)
    assert a[a.index("--platform") + 1] == jobs.DEFAULT_PLATFORM
    assert a[a.index("--preset") + 1] == jobs.DEFAULT_PRESET
    assert "--dry-run" in a and "--async" not in a
    assert "--no-browser" in a


def test_create_args_real_run_is_async():
    a = jobs.create_args(jobs.JobSpec(name="n", image="i", env={"K": "V"}), "project-x")
    assert "--async" in a and "--dry-run" not in a
    assert a[a.index("--env") + 1] == "K=V"
