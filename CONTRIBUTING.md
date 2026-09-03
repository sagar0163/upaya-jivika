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

## Commit messages (Conventional Commits)

Versioning is automated by [release-please](https://github.com/googleapis/release-please), which reads the **squash-merge commit message** (i.e. the PR title, since that becomes the squash commit subject) to decide version bumps and to write the changelog. Use this format for PR titles:

- `feat: ...` — new feature → minor version bump
- `fix: ...` — bug fix → patch version bump
- `feat!: ...` or `fix!: ...` (or a `BREAKING CHANGE:` footer) → major version bump
- `docs:`, `chore:`, `refactor:`, `test:`, `ci:` — no version bump, but still shows in the changelog

On every push to `main`, release-please opens/updates a "Release PR" with the bumped version and changelog. Merging that PR tags the release and publishes a GitHub Release — no manual tagging needed.
