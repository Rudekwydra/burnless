from __future__ import annotations

import json
import pathlib
import uuid
import hashlib
from datetime import datetime
from datetime import timezone


def append_event(root, event_type, data, scope=None, actor="cli") -> bool:
	try:
		root_path = pathlib.Path(root)
		root_path.mkdir(parents=True, exist_ok=True)

		events_file = root_path / "events.jsonl"

		if scope is None:
			project_root = str(root_path.resolve().parent)
			project_root_hash = "sha256:" + hashlib.sha256(project_root.encode()).hexdigest()
			scope = {
				"project_root": project_root,
				"project_root_hash": project_root_hash,
				"session_id": None
			}

		envelope = {
			"schema_version": 1,
			"event_id": uuid.uuid4().hex,
			"ts": datetime.now(timezone.utc).isoformat(),
			"event_type": event_type,
			"scope": scope,
			"actor": actor,
			"data": data
		}

		with open(events_file, 'a', encoding='utf-8') as f:
			f.write(json.dumps(envelope) + '\n')
		return True
	except Exception:
		return False


def read_events(root, event_type=None, limit=None) -> list[dict]:
	root_path = pathlib.Path(root)
	events_file = root_path / "events.jsonl"

	if not events_file.exists():
		return []

	events = []
	try:
		with open(events_file, 'r', encoding='utf-8') as f:
			for line in f:
				line = line.strip()
				if not line:
					continue
				try:
					event = json.loads(line)
					if event_type is None or event.get("event_type") == event_type:
						events.append(event)
				except (json.JSONDecodeError, ValueError):
					pass
	except Exception:
		pass

	if limit is not None:
		events = events[-limit:]

	return events
