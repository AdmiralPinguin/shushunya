# Project Workspace

This directory is the project root:

`/media/shushunya/SHUSHUNYA/shushunya`

Work for this project must stay inside this directory unless the user explicitly asks otherwise.

## Active Work Focus

Current active architecture work routes through `EyeOfTerror/Warmaster`.
User-facing chat/task entry must go through Warmaster and its governors/workers;
do not recreate or route work through the removed standalone mobile agent.

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
