"""Persistent JSON storage for Campaign Builder dialog sessions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from schemas import Message, Session, SessionDetail


class SessionStore:
    """Tiny file-backed repository for builder sessions and messages."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def list_sessions(self) -> list[Session]:
        with self._lock:
            data = self._read()
            sessions = [Session(**item) for item in data.get("sessions", [])]
            return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    def get_session(self, session_id: str) -> SessionDetail | None:
        with self._lock:
            data = self._read()
            session = self._find_session(data, session_id)
            if session is None:
                return None
            messages = [Message(**item) for item in data.get("messages", []) if item.get("session_id") == session_id]
            messages.sort(key=lambda message: message.created_at)
            return SessionDetail(**session, messages=messages)

    def create_session(
        self,
        *,
        title: str,
        campaign_id: int | None = None,
        status: str = "in_progress",
        session_id: str | None = None,
    ) -> Session:
        with self._lock:
            data = self._read()
            if session_id:
                existing = self._find_session(data, session_id)
                if existing is not None:
                    return Session(**existing)
            now = self._now()
            session = Session(
                id=session_id or str(uuid4()),
                campaign_id=campaign_id,
                title=title,
                created_at=now,
                updated_at=now,
                status=status,
            )
            data.setdefault("sessions", []).append(session.model_dump(mode="json"))
            self._write(data)
            return session

    def ensure_session(
        self,
        *,
        session_id: str | None,
        title: str,
        campaign_id: int | None = None,
        status: str = "in_progress",
    ) -> Session:
        if session_id:
            existing = self.get_session(session_id)
            if existing is not None:
                return Session(**existing.model_dump(exclude={"messages"}))
        return self.create_session(title=title, campaign_id=campaign_id, status=status, session_id=session_id)

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        with self._lock:
            data = self._read()
            session = self._find_session(data, session_id)
            if session is None:
                raise KeyError(session_id)
            message = Message(
                id=str(uuid4()),
                session_id=session_id,
                role=role,
                content=content,
                created_at=self._now(),
                metadata=metadata,
            )
            data.setdefault("messages", []).append(message.model_dump(mode="json"))
            session["updated_at"] = message.created_at.isoformat()
            if metadata:
                if "campaign_id" in metadata:
                    session["campaign_id"] = metadata["campaign_id"]
                if "status" in metadata and metadata["status"]:
                    session["status"] = metadata["status"]
            self._write(data)
            return message

    def update_session(
        self,
        session_id: str,
        *,
        campaign_id: int | None = None,
        status: str | None = None,
        title: str | None = None,
    ) -> Session | None:
        with self._lock:
            data = self._read()
            session = self._find_session(data, session_id)
            if session is None:
                return None
            if campaign_id is not None:
                session["campaign_id"] = campaign_id
            if status is not None:
                session["status"] = status
            if title:
                session["title"] = title
            session["updated_at"] = self._now().isoformat()
            self._write(data)
            return Session(**session)

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {"sessions": [], "messages": []}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError:
            return {"sessions": [], "messages": []}
        return {
            "sessions": data.get("sessions", []),
            "messages": data.get("messages", []),
        }

    def _write(self, data: dict[str, list[dict[str, Any]]]) -> None:
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)

    @staticmethod
    def _find_session(data: dict[str, list[dict[str, Any]]], session_id: str) -> dict[str, Any] | None:
        return next((item for item in data.get("sessions", []) if item.get("id") == session_id), None)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
