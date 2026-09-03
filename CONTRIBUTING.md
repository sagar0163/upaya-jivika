# Workflow

`main` is protected — no direct pushes. Every change goes through a branch and a PR.

1. Pick a task from [Issues](https://github.com/sagar0163/upaya-jivika/issues).
2. Branch off `main`: `git checkout -b <type>/<short-task-name>` (e.g. `feat/brain-router`).
3. Commit as you go. Keep commits scoped to the task.
4. Push the branch and open a PR against `main` using the PR template. Reference the issue with `Closes #<n>`.
5. Wait for the `CI / build` check to pass (required by branch protection).
6. Squash-merge the PR (repo is squash-only). The branch auto-deletes on merge.
7. Pull `main` locally, then move to the next task.

Do not amend or force-push shared branches once a PR is open.
