"""Singleton candidate profile (ADR-016)."""

from __future__ import annotations

from dataclasses import dataclass

from app.clock import utc_now
from app.db.database import Database
from app.llm.schemas.profile import CandidateProfile


@dataclass(frozen=True)
class StoredProfile:
    profile: CandidateProfile
    updated_at: str | None

    def public_dict(self) -> dict[str, object]:
        payload = self.profile.model_dump(mode="json")
        payload["updated_at"] = self.updated_at
        return payload


class ProfileRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self) -> StoredProfile:
        row = self._db.query_one(
            "SELECT profile_json, updated_at FROM candidate_profile WHERE id = 1"
        )
        if row is None:
            return StoredProfile(profile=CandidateProfile(), updated_at=None)
        return StoredProfile(
            profile=CandidateProfile.model_validate_json(str(row["profile_json"])),
            updated_at=str(row["updated_at"]),
        )

    def save(self, profile: CandidateProfile) -> StoredProfile:
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO candidate_profile (id, profile_json, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                profile_json = excluded.profile_json,
                updated_at = excluded.updated_at
            """,
            (profile.model_dump_json(), now),
        )
        return self.get()
