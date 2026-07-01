# Project Workspace

This directory is the project root:

`/media/shushunya/SHUSHUNYA/shushunya`

Work for this project must stay inside this directory unless the user explicitly asks otherwise.

## Active Work Focus

Current active architecture work is Ceraxia, the code-brigade governor under
`EyeOfTerror/Mechanicum/Ceraxia`, plus her Mechanicum code workers.

`Mechanicum/ShushunyaAgent` is parked. Do not resume ShushunyaAgent arena
stress tests, supervisor tuning, or standalone agent changes from old context
unless the user explicitly asks to work on ShushunyaAgent again in the current
conversation. Old AgentArena logs and previous ShushunyaAgent failures are not
the active task.

## File Permissions

Keep all project files readable across local Linux users and desktop sessions.

Default permissions for this project:

- Directories: `755`
- Regular files: `644`
- Shell/Python entrypoint scripts: `755`

After creating or moving files, run:

```bash
./fix-permissions.sh
```

Do not leave project files as owner-only `600` or directories as owner-only `700`, because the project is accessed from different users, sessions, and operating system recovery contexts.
