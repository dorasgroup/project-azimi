# GitNexus Integration

GitNexus is used as a local code-intelligence companion for this project. The local GitNexus repository is expected at:

```text
D:\Github\GitNexus
```

The CLI package entrypoint is under:

```text
D:\Github\GitNexus\gitnexus
```

## Purpose

- Index this project as implementation grows.
- Inspect architecture and dependency impact before larger refactors.
- Keep a local bridge server available for visual code exploration.

## Local Server

The local GitNexus bridge can be started with:

```powershell
.\scripts\dev\start-gitnexus.ps1
```

Default URL:

```text
http://127.0.0.1:4747
```

## Index This Project

From the project root:

```powershell
cd D:\pr\07
npx gitnexus analyze
```

If using the local GitNexus checkout directly:

```powershell
cd D:\Github\GitNexus\gitnexus
npm run gitnexus:refresh -- --repo D:\pr\07
```

## Notes

- Do not copy GitNexus source into this repository.
- Do not commit GitNexus runtime caches or generated local indexes.
- Use GitNexus for project review and implementation-risk checks as the codebase grows.
