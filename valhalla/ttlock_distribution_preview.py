"""Preview next-day recipients; no sending and no TTLock API call."""
import os
from datetime import datetime, timezone
from ttlock_recipients import recipients_for_tomorrow

def preview(at=None, db_path=None):
    day, recipients = recipients_for_tomorrow(
        db_path or os.environ["VALHALLA_DB_PATH"],
        at or datetime.now(timezone.utc))
    return {"booking_day":day.isoformat(),"recipient_count":len(recipients)}

if __name__=="__main__":
    print(preview())
