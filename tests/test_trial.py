from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from food_registry_bot.llm_access.trial import TrialQuota


def test_trial_quota_has_separate_limits_and_keeps_the_current_reset_when_settings_change():
    quota = TrialQuota()
    now = datetime(2026, 11, 26, 23, 58, tzinfo=timezone.utc)
    reset = now + timedelta(minutes=2)
    for _ in range(7):
        assert quota.consume(1, "food", now=now, timezone_name="Asia/Tbilisi", start_hour=4) is None
    assert quota.consume(1, "food", now=now, timezone_name="UTC", start_hour=6) == reset

    def send_message(_):
        return quota.consume(1, "conversation", now=now, timezone_name="Asia/Tbilisi", start_hour=4)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(send_message, range(24)))
    assert results.count(None) == 20
    assert results.count(reset) == 4
    assert quota.remaining(1, now) == {"food": 0, "conversation": 0}
    assert quota.remaining(2, now) == {"food": 7, "conversation": 20}
    assert quota.remaining(1, reset) == {"food": 7, "conversation": 20}
    assert quota.consume(1, "food", now=reset, timezone_name="UTC", start_hour=6) is None
