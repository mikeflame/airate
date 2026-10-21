# Bench tasks

Two tasks ship today: `js-date-parse` (JavaScript, dayjs versus the Node standard library) and `py-config-validate` (Python, jsonschema versus the standard library). Each directory is one task for `tools/run.py`: a `task.json` with the prompt, the check command and the
two arms, plus a `workspace/` copied fresh for every trial. The `with` arm installs or enables the subject;
the `baseline` arm does the same job without it. The check command, not the agent, decides success.

`protected` lists the files the agent must not change (the grader); the harness snapshots them after
the arm's setup, restores them before the check and fails any trial that altered them. Each arm's
`verify` is a shell command that must exit 0 for the trial to count. Verify **necessity, not text**: a
grep for an import is satisfied by a comment. The shipped task hides `node_modules/dayjs` and requires
the tests to fail without it (*with* arm), and hides `node_modules` entirely and requires the tests to
pass (*baseline* arm); both restore what they hid. A solution that imports the subject without using it
still passes, so the prompt and the task design carry the rest. `subjects` names the repositories the
task is written for: the harness refuses any other subject, and the intake gives weight 0 to a report
that pairs the task with one.

`timeout_s` bounds the agent: on expiry the harness kills the agent's whole process group and only then
grades, so work finished after the deadline does not count and the trial's duration is the deadline. A
process that starts its own session escapes the group; tasks should not rely on agents that do.

A trial that fails its arm's `verify` is void rather than failed: it never measured the arm, so it is
excluded from the arm's solve count and spend (a modified grader, by contrast, is a failure). Prompts
should therefore make the arm condition unmistakable; the first Python run showed an agent told that
jsonschema was installed hand-rolling the validation in two trials of three.

Success is the check's verdict alone: a trial whose agent crashed or timed out after writing a passing
solution is a solved trial with missing metrics. Cost per solved task is the arm's total spend divided by
its solved trials, so a task whose check is flaky or whose agent budget is too small punishes both arms.

A bench report whose `task.source` is `airate` must reference a task from this directory by id and
content hash; the intake recomputes the hash and gives weight 0 to a report whose task does not match.
Changing a task changes its hash: reports filed under the old hash stay on file at the weight they got.

Adding a task: open a pull request with the directory, a `subjects` list naming at least one repository
the task is meant for, `protected` and `verify` entries, and a check that a correct solution passes and
an empty workspace fails.
