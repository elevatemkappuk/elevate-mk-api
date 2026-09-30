from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase, override_settings

from notifications.management.commands.process_background_jobs import run_background_worker


@override_settings(
    BACKGROUND_WORKER_POLL_SECONDS=3,
    TRANSACTIONAL_EMAIL_WORKER_BATCH_SIZE=4,
    BREVO_SYNC_WORKER_BATCH_SIZE=7,
)
class CombinedBackgroundWorkerTests(SimpleTestCase):
    def test_transactional_batch_runs_before_marketing_batch_with_independent_limits(self):
        stop_event = Mock()
        stop_event.is_set.side_effect = [False, True]
        sleep = Mock()
        events = []

        with patch("notifications.management.commands.process_background_jobs.process_transactional_email_jobs", side_effect=lambda **kwargs: (events.append("transactional"), ["transactional"])[1]) as transactional, patch("notifications.management.commands.process_background_jobs.process_brevo_sync_jobs", side_effect=lambda **kwargs: (events.append("marketing"), ["marketing"])[1]) as marketing:
            run_background_worker(stop_event=stop_event, sleep_fn=sleep)

        self.assertEqual(transactional.call_args, call(limit=4))
        self.assertEqual(marketing.call_args, call(limit=7))
        self.assertEqual(events, ["transactional", "marketing"])
        sleep.assert_called_once_with(0.1)

    def test_transactional_failure_does_not_block_marketing_batch(self):
        stop_event = Mock()
        stop_event.is_set.side_effect = [False, True]

        with patch("notifications.management.commands.process_background_jobs.process_transactional_email_jobs", side_effect=RuntimeError("transactional")), patch("notifications.management.commands.process_background_jobs.process_brevo_sync_jobs", return_value=[] ) as marketing:
            run_background_worker(stop_event=stop_event, sleep_fn=Mock())

        marketing.assert_called_once()

    def test_marketing_failure_isolated_and_empty_batches_use_poll_interval(self):
        stop_event = Mock()
        stop_event.is_set.side_effect = [False, True]
        sleep = Mock()

        with patch("notifications.management.commands.process_background_jobs.process_transactional_email_jobs", return_value=[]), patch("notifications.management.commands.process_background_jobs.process_brevo_sync_jobs", side_effect=RuntimeError("marketing")):
            run_background_worker(stop_event=stop_event, sleep_fn=sleep)

        sleep.assert_called_once_with(3.0)
