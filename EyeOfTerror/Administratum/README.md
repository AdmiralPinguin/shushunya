# Administratum

Administratum is the proactive task department of EyeOfTerror. Its brigadier is
AshurKai. The department owns reminders, todos, routines, watches, and its own
journal.

Rules:

- Administratum never writes directly to Telegram.
- Due tasks become Archive system events.
- Archive formulates the final message through the shared Shushunya persona.
- All proactive output uses session `shushunya-main` and namespace `shushunya`.
- System events set `source=administratum`, `system_event=true`, and
  `intent_detection=false` so they do not create recursive tasks.

HTTP service:

- `GET /health`
- `POST /task`
- `GET /tasks`
- `GET /task/{id}`
- `POST /task/{id}/done`
- `POST /task/{id}/cancel`
- `POST /task/{id}/snooze`
- `POST /watch`
- `GET /watches`
- `POST /watch/{id}/pause`
- `POST /watch/{id}/resume`
- `GET /journal`

Default ports:

- AshurKai API: `7300`
- Heartbeat: background process, one cycle per minute

Intent parsing:

- `intent_parser.py` owns the strict JSON contract used by ArchiveOfHeresy
  before it calls AshurKai.
- The LLM decision happens in ArchiveOfHeresy. AshurKai only stores and
  executes the resulting task/watch.
- Low-confidence or ambiguous routine/watch intents are not silently created;
  Archive asks the owner for confirmation in Shushunya's voice.

Watch behavior:

- Active watches are claimed by the heartbeat when `next_check <= now`.
- HTTP/HTTPS targets are fetched as text, fingerprinted, and compared with the
  previous value.
- The first check records a baseline only. Later checks can trigger on
  `mode=changed`, `mode=contains`, or `mode=always`.
- Watch notifications are delivered only through Archive system events.
