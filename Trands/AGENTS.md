# Codex Project Rules

## Primary Rule

For every user request, implement exactly the requested change with the smallest possible amount of repository exploration, code modification, command execution, and context usage.

The user's task defines the scope.

Do not expand the scope unless explicitly requested.

## Context

Use `PROJECT_CONTEXT.md` when project architecture, paths, technologies, or conventions are needed.

Do not rediscover information already documented in `PROJECT_CONTEXT.md`.

Do not scan the whole repository to understand the project.

## Repository Exploration

Start with the most likely relevant file/component.

Only inspect additional files when they are directly required.

For follow-up edits, start with:

1. current `git diff`
2. currently modified files
3. directly related files

Do not restart repository exploration for every follow-up request.

Never recursively inspect heavy/generated directories such as:

- `node_modules/`
- `.next/`
- `dist/`
- `build/`
- `out/`
- `coverage/`
- `.cache/`
- `tmp/`
- `logs/`
- `backups/`
- `.git/`
- generated output directories

unless the user's task explicitly requires them.

## Editing

Prefer surgical changes.

Do not:

- refactor unrelated code
- redesign unrelated UI
- rename unrelated code
- reorganize directories
- fix unrelated warnings/errors
- add extra features
- create unnecessary abstractions
- modify unrelated files
- update documentation unnecessarily

Preserve the existing architecture and patterns.

Reuse existing components, utilities, styles, services, and dependencies whenever possible.

## Dependencies

Do not install, remove, or update dependencies unless explicitly required.

Do not modify package or lock files unless the requested task requires it.

## Verification

Use the cheapest verification appropriate for the change.

For visual/UI/text/CSS/icon/spacing changes:

- normally review the diff only
- do not run a production build
- do not run the full test suite

For logic changes:

- prefer targeted checks
- prefer targeted tests
- prefer checking only affected files/features

Run a full build or full test suite only when:

- explicitly requested by the user, or
- genuinely necessary to validate the requested change

Do not run expensive commands automatically just because a task is complete.

## Git

Do not automatically:

- commit
- push
- create branches
- merge
- rebase
- tag
- stash

Use `git diff` when useful for incremental work.

## Release / Deployment

Never automatically:

- create a release
- deploy
- publish
- upload to production
- restart production services
- create deployment packages
- modify CI/CD

Only do these when explicitly requested.

## Database

Do not automatically:

- create or run migrations
- seed/reset databases
- modify production data
- perform destructive database operations

unless explicitly required.

## Task Completion

Once the requested change works, stop.

Do not continue exploring or improving unrelated parts of the project.

Final response should be short and contain only:

1. what changed
2. files changed
3. any important issue requiring user attention

## Priority

When several valid solutions exist, prefer the one requiring:

- fewer files read
- fewer files changed
- fewer commands
- less context
- less execution time
- fewer tokens

while keeping the implementation correct and maintainable.

Explicit instructions in the current user request override these defaults.
