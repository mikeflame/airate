# Bench tasks

Two tasks ship today: `js-date-parse` (JavaScript, dayjs versus the Node standard library) and `py-config-validate` (Python, jsonschema versus the standard library). Each directory is one task for `tools/run.py`: a `task.json` with the prompt, the check command and the
two arms, plus a `workspace/` copied fresh for every trial. The `with` arm installs or enables the subject;
the `baseline` arm does the same job without it. The check command, not the agent, decides success.

`protected` lists the files the agent must not change (the grader); the harness snapshots them after
the arm's setup, restores them before the check and fails any trial that altered them. Each arm has a
`policy` command that states the arm condition **independently of correctness**: the subject is installed
(*with*), no third-party package is installed (*baseline*). A trial that breaks its policy is void. The
*with* arm also has a `necessity` command that runs **only after a passing check** and must prove the
subject was needed: the shipped tasks hide the subject's package and require the tests to fail, then
restore it. A passing solution that did not need the subject is void. A wrong answer that kept the policy
is a failed attempt and keeps its cost, in either arm. Verify necessity, not text: a grep for an import is
satisfied by a comment. A solution that imports the subject without using it still passes, so the prompt
and the task design carry the rest. `subjects` names the repositories the
task is written for: the harness refuses any other subject, and the intake gives weight 0 to a report
that pairs the task with one.

`timeout_s` bounds the agent: on expiry the harness kills the agent's whole process group and only then
grades, so work finished after the deadline does not count and the trial's duration is the deadline. A
process that starts its own session escapes the group; tasks should not rely on agents that do.

Void trials are excluded from the arm's solve count and spend and from the choice of comparison unit;
a modified grader, by contrast, is a failure. Prompts should make the arm condition unmistakable; the
first Python run showed an agent told that jsonschema was installed hand-rolling the validation in two
trials of three.

The grader must state the task's whole contract. The first Python grader never probed invalid field
types, so a validator that raised on `mode: []` passed every test and the comparison rewarded cheaper,
incomplete validation; its reference now fails that case and the grader covers invalid types across every
field. Add cases for every type a field could receive, and keep a test that a deliberately incomplete
solution fails.

Success is the check's verdict alone: a trial whose agent crashed or timed out after writing a passing
solution is a solved trial with missing metrics. Cost per solved task is the arm's total spend divided by
its solved trials, so a task whose check is flaky or whose agent budget is too small punishes both arms.

A bench report whose `task.source` is `airate` must reference a task from this directory by id and
content hash; the intake recomputes the hash and gives weight 0 to a report whose task does not match.
Changing a task changes its hash: reports filed under the old hash stay on file at the weight they got.

Adding a task: open a pull request with the directory, a `subjects` list naming at least one repository
the task is meant for, `protected` and `verify` entries, and a check that a correct solution passes and
an empty workspace fails.
