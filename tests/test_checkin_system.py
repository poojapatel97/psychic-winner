from datetime import date, datetime, timezone
import unittest

from checkin_system import CheckInService, MAX_NOTES_LENGTH


class CheckInServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CheckInService()
        self.reference_time = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)

    def test_create_and_history_view_with_trends(self) -> None:
        self.service.create_checkin(
            player_id="p1",
            goal_status="on_track",
            reading_status="in_progress",
            notes="Worked on chapter 1 goal.",
            checkin_at=self.reference_time,
        )
        self.service.create_checkin(
            player_id="p1",
            goal_status="completed",
            reading_status="done",
            notes="Completed chapter 1 goal.",
            checkin_at=datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc),
        )

        view = self.service.get_player_history_view("p1")
        self.assertEqual(view["player_id"], "p1")
        self.assertEqual(len(view["checkins"]), 2)
        self.assertEqual(view["trends"]["goal_completion_rate"], 0.5)
        self.assertEqual(view["trends"]["reading_adherence_rate"], 0.5)

    def test_duplicate_checkin_same_period_fails(self) -> None:
        self.service.create_checkin(
            player_id="p1",
            goal_status="on_track",
            reading_status="in_progress",
            notes="First daily check-in.",
            checkin_at=self.reference_time,
            cadence="daily",
        )
        with self.assertRaises(ValueError):
            self.service.create_checkin(
                player_id="p1",
                goal_status="completed",
                reading_status="done",
                notes="Second daily check-in.",
                checkin_at=datetime(2026, 6, 2, 20, 0, tzinfo=timezone.utc),
                cadence="daily",
            )

    def test_validation_rules(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create_checkin(
                player_id="",
                goal_status="on_track",
                reading_status="done",
                notes="x",
            )
        with self.assertRaises(ValueError):
            self.service.create_checkin(
                player_id="p1",
                goal_status="invalid",  # type: ignore[arg-type]
                reading_status="done",
                notes="x",
            )
        with self.assertRaises(ValueError):
            self.service.create_checkin(
                player_id="p1",
                goal_status="on_track",
                reading_status="done",
                notes="x" * (MAX_NOTES_LENGTH + 1),
            )

    def test_update_checkin(self) -> None:
        created = self.service.create_checkin(
            player_id="p1",
            goal_status="blocked",
            reading_status="not_started",
            notes="Need help to start.",
            mood_score=4,
            checkin_at=self.reference_time,
        )
        updated = self.service.update_checkin(
            created["id"],
            goal_status="on_track",
            reading_status="in_progress",
            notes="Unblocked and moving.",
            mood_score=7,
        )
        self.assertEqual(updated["goal_status"], "on_track")
        self.assertEqual(updated["reading_status"], "in_progress")
        self.assertEqual(updated["mood_score"], 7)

    def test_reminders_for_missing_checkins(self) -> None:
        self.service.create_checkin(
            player_id="p1",
            goal_status="on_track",
            reading_status="in_progress",
            notes="Checked in.",
            checkin_at=self.reference_time,
            cadence="daily",
        )
        missing = self.service.players_missing_checkin(
            ["p1", "p2", "p3"], cadence="daily", as_of=self.reference_time
        )
        self.assertEqual(missing, ["p2", "p3"])

    def test_coach_dashboard_filters_missed_and_stalled(self) -> None:
        for day in (1, 2, 3):
            self.service.create_checkin(
                player_id="stalled",
                goal_status="blocked",
                reading_status="in_progress",
                notes=f"Day {day} blocked.",
                checkin_at=datetime(2026, 6, day, 12, 0, tzinfo=timezone.utc),
            )
        self.service.create_checkin(
            player_id="active",
            goal_status="completed",
            reading_status="done",
            notes="All good.",
            checkin_at=self.reference_time,
        )
        dashboard = self.service.coach_dashboard(
            ["stalled", "active", "missing"],
            cadence="daily",
            as_of=self.reference_time,
            stalled_window=3,
        )
        self.assertIn("missing", dashboard["missed_checkins"])
        reasons = {entry["player_id"]: entry["reason"] for entry in dashboard["stalled_progress"]}
        self.assertEqual(reasons["stalled"], "blocked_goals")

    def test_success_metrics(self) -> None:
        self.service.create_checkin(
            player_id="p1",
            goal_status="completed",
            reading_status="done",
            notes="done",
            checkin_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        self.service.create_checkin(
            player_id="p2",
            goal_status="on_track",
            reading_status="in_progress",
            notes="moving",
            checkin_at=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc),
        )
        self.service.create_checkin(
            player_id="p1",
            goal_status="on_track",
            reading_status="done",
            notes="steady",
            checkin_at=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
        )
        metrics = self.service.success_metrics(
            ["p1", "p2"],
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 2),
            cadence="daily",
        )
        self.assertEqual(metrics["checkin_completion_rate"], 0.75)
        self.assertEqual(len(metrics["goal_completion_trend"]), 2)
        first_period = metrics["goal_completion_trend"][0]
        self.assertEqual(first_period["period_key"], "2026-06-01")

    def test_rollout_phases_defined(self) -> None:
        phases = CheckInService.rollout_phases()
        self.assertEqual(len(phases), 3)
        self.assertEqual(phases[0].name, "phase_1")


if __name__ == "__main__":
    unittest.main()
