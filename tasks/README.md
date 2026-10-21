# Bench tasks

Each directory is one task for `tools/run.py`: a `task.json` with the prompt, the check command and the
two arms, plus a `workspace/` copied fresh for every trial. The `with` arm installs or enables the subject;
the `baseline` arm does the same job without it. The check command, not the agent, decides success.

`protected` lists the files the agent must not change (the grader); the harness snapshots them after
the arm's setup, restores them before the check and fails any trial that altered them. Each arm's
`verify` is a shell command that must exit 0 for the trial to count: for the shipped task it proves the
solution imports `dayjs` in the *with* arm and that nothing imports or installs it in the *baseline*.

A bench report whose `task.source` is `airate` must reference a task from this directory by id and
content hash; the intake recomputes the hash and gives weight 0 to a report whose task does not match.
Changing a task changes its hash: reports filed under the old hash stay on file at the weight they got.

Adding a task: open a pull request with the directory, a `subjects` list naming at least one repository
the task is meant for, `protected` and `verify` entries, and a check that a correct solution passes and
an empty workspace fails.
