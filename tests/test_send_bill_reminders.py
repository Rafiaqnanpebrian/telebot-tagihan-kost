import unittest
from datetime import date, timedelta

from send_bill_reminders import reminder_type_for_due_date


class ReminderTypeForDueDateTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 25)

    def test_returns_three_days_before_reminder(self):
        due_date = self.today + timedelta(days=3)
        self.assertEqual(reminder_type_for_due_date(due_date, self.today), "h_minus_3")

    def test_returns_due_date_reminder(self):
        self.assertEqual(reminder_type_for_due_date(self.today, self.today), "due_date")

    def test_returns_one_day_overdue_reminder(self):
        due_date = self.today - timedelta(days=1)
        self.assertEqual(reminder_type_for_due_date(due_date, self.today), "h_plus_1")

    def test_ignores_dates_outside_reminder_schedule(self):
        due_date = self.today + timedelta(days=2)
        self.assertIsNone(reminder_type_for_due_date(due_date, self.today))


if __name__ == "__main__":
    unittest.main()
