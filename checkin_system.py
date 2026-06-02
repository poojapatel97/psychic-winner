from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import sqlite3
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence

GoalStatus = Literal["on_track", "blocked", "completed"]
ReadingStatus = Literal["not_started", "in_progress", "done"]
Cadence = Literal["daily", "weekly"]

GOAL_STATUSES = {"on_track", "blocked", "completed"}
READING_STATUSES = {"not_started", "in_progress", "done"}
CADENCES = {"daily", "weekly"}
MAX_NOTES_LENGTH = 500


@dataclass(frozen=True)
class RolloutPhase:
    name: str
    description: str


def _to_utc_iso(value: Optional[datetime] = None) -> str:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _period_key(checkin_at: datetime, cadence: Cadence) -> str:
    if cadence == "daily":
        return checkin_at.date().isoformat()
    if cadence == "weekly":
        year, week, _ = checkin_at.isocalendar()
        return f"{year}-W{week:02d}"
    raise ValueError(f"Unsupported cadence: {cadence}")


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class CheckInService:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS check_ins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id TEXT NOT NULL,
                checkin_at TEXT NOT NULL,
                cadence TEXT NOT NULL,
                period_key TEXT NOT NULL,
                goal_status TEXT NOT NULL,
                reading_status TEXT NOT NULL,
                notes TEXT NOT NULL,
                mood_score INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(player_id, cadence, period_key)
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_check_ins_player_time
            ON check_ins(player_id, checkin_at)
            """
        )
        self.conn.commit()

    def _validate_inputs(
        self,
        *,
        player_id: Optional[str] = None,
        goal_status: Optional[str] = None,
        reading_status: Optional[str] = None,
        notes: Optional[str] = None,
        mood_score: Optional[int] = None,
        cadence: Optional[str] = None,
    ) -> None:
        if player_id is not None and not player_id.strip():
            raise ValueError("player_id is required")
        if goal_status is not None and goal_status not in GOAL_STATUSES:
            raise ValueError(f"goal_status must be one of {sorted(GOAL_STATUSES)}")
        if reading_status is not None and reading_status not in READING_STATUSES:
            raise ValueError(f"reading_status must be one of {sorted(READING_STATUSES)}")
        if cadence is not None and cadence not in CADENCES:
            raise ValueError(f"cadence must be one of {sorted(CADENCES)}")
        if notes is not None:
            if not notes.strip():
                raise ValueError("notes are required")
            if len(notes) > MAX_NOTES_LENGTH:
                raise ValueError(f"notes must be <= {MAX_NOTES_LENGTH} characters")
        if mood_score is not None and not (1 <= mood_score <= 10):
            raise ValueError("mood_score must be between 1 and 10")

    def create_checkin(
        self,
        *,
        player_id: str,
        goal_status: GoalStatus,
        reading_status: ReadingStatus,
        notes: str,
        mood_score: Optional[int] = None,
        cadence: Cadence = "daily",
        checkin_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        self._validate_inputs(
            player_id=player_id,
            goal_status=goal_status,
            reading_status=reading_status,
            notes=notes,
            mood_score=mood_score,
            cadence=cadence,
        )
        checkin_at = checkin_at or datetime.now(timezone.utc)
        if checkin_at.tzinfo is None:
            checkin_at = checkin_at.replace(tzinfo=timezone.utc)
        checkin_at_iso = _to_utc_iso(checkin_at)
        now_iso = _to_utc_iso()
        period_key = _period_key(checkin_at, cadence)
        try:
            cursor = self.conn.execute(
                """
                INSERT INTO check_ins (
                    player_id, checkin_at, cadence, period_key,
                    goal_status, reading_status, notes, mood_score,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id.strip(),
                    checkin_at_iso,
                    cadence,
                    period_key,
                    goal_status,
                    reading_status,
                    notes.strip(),
                    mood_score,
                    now_iso,
                    now_iso,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Check-in already exists for player '{player_id}' in cadence '{cadence}' period '{period_key}'"
            ) from exc
        return self.get_checkin(cursor.lastrowid)

    def get_checkin(self, checkin_id: int) -> Dict[str, Any]:
        row = self.conn.execute("SELECT * FROM check_ins WHERE id = ?", (checkin_id,)).fetchone()
        if row is None:
            raise ValueError(f"check-in {checkin_id} does not exist")
        return dict(row)

    def update_checkin(
        self,
        checkin_id: int,
        *,
        goal_status: Optional[GoalStatus] = None,
        reading_status: Optional[ReadingStatus] = None,
        notes: Optional[str] = None,
        mood_score: Optional[int] = None,
        cadence: Optional[Cadence] = None,
        checkin_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        current = self.get_checkin(checkin_id)
        self._validate_inputs(
            goal_status=goal_status,
            reading_status=reading_status,
            notes=notes,
            mood_score=mood_score,
            cadence=cadence,
        )

        new_cadence = cadence or current["cadence"]
        new_checkin_at = checkin_at or _parse_iso(current["checkin_at"])
        if new_checkin_at.tzinfo is None:
            new_checkin_at = new_checkin_at.replace(tzinfo=timezone.utc)
        new_period_key = _period_key(new_checkin_at, new_cadence)

        updates: Dict[str, Any] = {
            "goal_status": goal_status if goal_status is not None else current["goal_status"],
            "reading_status": reading_status if reading_status is not None else current["reading_status"],
            "notes": notes.strip() if notes is not None else current["notes"],
            "mood_score": mood_score if mood_score is not None else current["mood_score"],
            "cadence": new_cadence,
            "checkin_at": _to_utc_iso(new_checkin_at),
            "period_key": new_period_key,
            "updated_at": _to_utc_iso(),
        }
        try:
            self.conn.execute(
                """
                UPDATE check_ins
                SET goal_status = :goal_status,
                    reading_status = :reading_status,
                    notes = :notes,
                    mood_score = :mood_score,
                    cadence = :cadence,
                    checkin_at = :checkin_at,
                    period_key = :period_key,
                    updated_at = :updated_at
                WHERE id = :id
                """,
                {**updates, "id": checkin_id},
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Check-in already exists for player '{current['player_id']}' in cadence '{new_cadence}' period '{new_period_key}'"
            ) from exc
        return self.get_checkin(checkin_id)

    def get_player_history(self, player_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM check_ins
            WHERE player_id = ?
            ORDER BY checkin_at ASC
            LIMIT ?
            """,
            (player_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def _compute_trends(self, checkins: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(checkins)
        if total == 0:
            return {
                "total_checkins": 0,
                "goal_completion_rate": 0.0,
                "goal_on_track_rate": 0.0,
                "reading_adherence_rate": 0.0,
            }

        completed_goals = sum(1 for item in checkins if item["goal_status"] == "completed")
        on_track_goals = sum(1 for item in checkins if item["goal_status"] == "on_track")
        done_readings = sum(1 for item in checkins if item["reading_status"] == "done")
        return {
            "total_checkins": total,
            "goal_completion_rate": completed_goals / total,
            "goal_on_track_rate": on_track_goals / total,
            "reading_adherence_rate": done_readings / total,
        }

    def get_player_history_view(self, player_id: str) -> Dict[str, Any]:
        checkins = self.get_player_history(player_id, limit=1000)
        return {
            "player_id": player_id,
            "checkins": checkins,
            "trends": self._compute_trends(checkins),
        }

    def players_missing_checkin(
        self,
        player_ids: Iterable[str],
        *,
        cadence: Cadence = "daily",
        as_of: Optional[datetime] = None,
    ) -> List[str]:
        self._validate_inputs(cadence=cadence)
        as_of = as_of or datetime.now(timezone.utc)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        key = _period_key(as_of, cadence)
        rows = self.conn.execute(
            "SELECT DISTINCT player_id FROM check_ins WHERE cadence = ? AND period_key = ?",
            (cadence, key),
        ).fetchall()
        checked_in = {row["player_id"] for row in rows}
        return [player_id for player_id in player_ids if player_id not in checked_in]

    def coach_dashboard(
        self,
        player_ids: Iterable[str],
        *,
        cadence: Cadence = "daily",
        as_of: Optional[datetime] = None,
        stalled_window: int = 3,
    ) -> Dict[str, Any]:
        as_of = as_of or datetime.now(timezone.utc)
        missed = self.players_missing_checkin(player_ids, cadence=cadence, as_of=as_of)
        stalled: List[Dict[str, str]] = []
        for player_id in player_ids:
            recent = self.conn.execute(
                """
                SELECT goal_status, reading_status
                FROM check_ins
                WHERE player_id = ?
                ORDER BY checkin_at DESC
                LIMIT ?
                """,
                (player_id, stalled_window),
            ).fetchall()
            if len(recent) < stalled_window:
                continue
            blocked_count = sum(1 for row in recent if row["goal_status"] == "blocked")
            reading_done = any(row["reading_status"] == "done" for row in recent)
            if blocked_count >= 2:
                stalled.append({"player_id": player_id, "reason": "blocked_goals"})
            elif not reading_done:
                stalled.append({"player_id": player_id, "reason": "reading_stall"})

        return {
            "as_of": _to_utc_iso(as_of),
            "cadence": cadence,
            "missed_checkins": missed,
            "stalled_progress": stalled,
        }

    def success_metrics(
        self,
        player_ids: Sequence[str],
        *,
        start_date: date,
        end_date: date,
        cadence: Cadence = "daily",
    ) -> Dict[str, Any]:
        if end_date < start_date:
            raise ValueError("end_date must be >= start_date")
        self._validate_inputs(cadence=cadence)
        if cadence == "daily":
            period_count = (end_date - start_date).days + 1
        else:
            start_year, start_week, _ = start_date.isocalendar()
            end_year, end_week, _ = end_date.isocalendar()
            period_count = (end_year - start_year) * 52 + (end_week - start_week) + 1
        expected = period_count * len(player_ids)

        rows = self.conn.execute(
            """
            SELECT period_key,
                   COUNT(*) AS total,
                   SUM(CASE WHEN goal_status = 'completed' THEN 1 ELSE 0 END) AS goal_completed,
                   SUM(CASE WHEN reading_status = 'done' THEN 1 ELSE 0 END) AS reading_done
            FROM check_ins
            WHERE date(checkin_at) BETWEEN date(?) AND date(?)
              AND cadence = ?
            GROUP BY period_key
            ORDER BY period_key ASC
            """,
            (start_date.isoformat(), end_date.isoformat(), cadence),
        ).fetchall()

        actual = sum(row["total"] for row in rows)
        trends = [
            {
                "period_key": row["period_key"],
                "goal_completion_rate": row["goal_completed"] / row["total"] if row["total"] else 0.0,
                "reading_adherence_rate": row["reading_done"] / row["total"] if row["total"] else 0.0,
            }
            for row in rows
        ]
        return {
            "checkin_completion_rate": actual / expected if expected else 0.0,
            "goal_completion_trend": trends,
            "reading_adherence_trend": trends,
        }

    @staticmethod
    def rollout_phases() -> List[RolloutPhase]:
        return [
            RolloutPhase("phase_1", "Baseline manual check-in flow"),
            RolloutPhase("phase_2", "Automated daily/weekly reminders"),
            RolloutPhase("phase_3", "Trend analysis and coach dashboard enhancements"),
        ]
