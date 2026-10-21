# Bench tasks

Each directory is one task for `tools/run.py`: a `task.json` with the prompt, the check command and the
two arms, plus a `workspace/` copied fresh for every trial. The `with` arm installs or enables the subject;
the `baseline` arm does the same job without it. The check command, not the agent, decides success.

A bench report whose `task.source` is `airate` must reference a task from this directory by id and
content hash; the intake recomputes the hash and gives weight 0 to a report whose task does not match.

Adding a task: open a pull request with the directory, a `subjects` list naming at least one repository
the task is meant for, and a check that a correct solution passes and an empty workspace fails.
