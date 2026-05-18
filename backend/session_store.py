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
            states_by_session = {
                item.get("session_id"): item
                for item in data.get("campaign_states", [])
            }
            sessions = [
                Session(**{
                    **item,
                    **self._state_fields(states_by_session.get(item.get("id"), {})),
                })
                for item in data.get("sessions", [])
            ]
            return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    def get_session(self, session_id: str) -> SessionDetail | None:
        with self._lock:
            data = self._read()
            session = self._find_session(data, session_id)
            if session is None:
                return None
            messages = [Message(**item) for item in data.get("messages", []) if item.get("session_id") == session_id]
            messages.sort(key=lambda message: message.created_at)
            state = next((item for item in data.get("campaign_states", []) if item.get("session_id") == session_id), {})
            return SessionDetail(**{**session, **self._state_fields(state)}, messages=messages)

    def create_session(
        self,
        *,
        title: str,
        campaign_id: int | None = None,
        status: str = "collect_brief",
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
        status: str = "collect_brief",
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

    def upsert_campaign_state(
        self,
        *,
        session_id: str,
        campaign_id: int | None = None,
        draft_flow_json: dict[str, Any] | None = None,
        runtime_status: str = "collect_brief",
        draft_flow_version: int | None = None,
        campaign_brief_json: dict[str, Any] | None = None,
        brief_completeness_json: dict[str, Any] | None = None,
        review_checklist_json: dict[str, Any] | None = None,
        review_status: str | None = None,
        review_checklist_acknowledged: bool = False,
    ) -> None:
        with self._lock:
            data = self._read()
            session = self._find_session(data, session_id)
            if session is None:
                raise KeyError(session_id)
            states = data.setdefault("campaign_states", [])
            state = next((item for item in states if item.get("session_id") == session_id), None)
            now = self._now().isoformat()
            if state is None:
                states.append({
                    "session_id": session_id,
                    "campaign_id": campaign_id,
                    "draft_flow_json": draft_flow_json,
                    "draft_flow_version": draft_flow_version,
                    "campaign_brief_json": campaign_brief_json,
                    "brief_completeness_json": brief_completeness_json,
                    "review_checklist_json": review_checklist_json,
                    "review_status": review_status,
                    "review_checklist_acknowledged": review_checklist_acknowledged,
                    "runtime_status": runtime_status,
                    "created_at": now,
                    "updated_at": now,
                })
            else:
                state.update({
                    "campaign_id": campaign_id,
                    "draft_flow_json": draft_flow_json,
                    "draft_flow_version": draft_flow_version,
                    "campaign_brief_json": campaign_brief_json,
                    "brief_completeness_json": brief_completeness_json,
                    "review_checklist_json": review_checklist_json,
                    "review_status": review_status,
                    "review_checklist_acknowledged": review_checklist_acknowledged,
                    "runtime_status": runtime_status,
                    "updated_at": now,
                })
            if campaign_id is not None:
                session["campaign_id"] = campaign_id
            session["status"] = runtime_status
            session["updated_at"] = now
            self._write(data)

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {"sessions": [], "messages": [], "campaign_states": []}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError:
            return {"sessions": [], "messages": [], "campaign_states": []}
        return {
            "sessions": data.get("sessions", []),
            "messages": data.get("messages", []),
            "campaign_states": data.get("campaign_states", []),
        }

    def _write(self, data: dict[str, list[dict[str, Any]]]) -> None:
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)

    @staticmethod
    def _state_fields(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "campaign_brief": state.get("campaign_brief_json"),
            "draft_flow": state.get("draft_flow_json"),
            "draft_flow_version": state.get("draft_flow_version"),
            "brief_completeness": state.get("brief_completeness_json"),
            "review_checklist": state.get("review_checklist_json"),
            "review_status": state.get("review_status") or "blocked",
            "review_checklist_acknowledged": bool(state.get("review_checklist_acknowledged")),
        }

    @staticmethod
    def _find_session(data: dict[str, list[dict[str, Any]]], session_id: str) -> dict[str, Any] | None:
        return next((item for item in data.get("sessions", []) if item.get("id") == session_id), None)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
