from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception
from datetime import timedelta

# Google Sheets configuration
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "12RwT-xCtOP4g0T2jlW0zGs33Xj_ydiWvnLaJKHSjUtk")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "https://docs.google.com/spreadsheets/d/12RwT-xCtOP4g0T2jlW0zGs33Xj_ydiWvnLaJKHSjUtk/edit?usp=sharing")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Sheet1")


def format_oncall_list(oncall_list):
    lines = []
    for o in oncall_list:
        primary_phone = _format_phone(o['name'], o['phone'])
        lines.append(f"• *{o['name']}* (primary, {primary_phone}) — {o['week_start_date']} → {o['week_end_date']}")
        if o.get("secondary_name") and o.get("secondary_phone"):
            secondary_phone = _format_phone(o['secondary_name'], o['secondary_phone'])
            lines.append(f"• *{o['secondary_name']}* (secondary, {secondary_phone}) — {o['week_start_date']} → {o['week_end_date']}")
    return "\n".join(lines)


def get_oncall_list_from_sheet():
    if GOOGLE_SHEET_ID:
        csv_url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq?tqx=out:csv"
    elif GOOGLE_SHEET_URL:
        if "/d/" in GOOGLE_SHEET_URL:
            sheet_id = GOOGLE_SHEET_URL.split("/d/")[1].split("/")[0]
            csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
        else:
            app.logger.error("Invalid Google Sheet URL format")
            return False
    else:
        app.logger.error("No Google Sheet ID or URL provided")
        return False
    
    # Fetch CSV data from public sheet
    response = requests.get(csv_url)
    response.raise_for_status()
    
    # Parse CSV data
    csv_data = StringIO(response.text)
    reader = csv.DictReader(csv_data)
    records = list(reader)
    
    return records


def _format_phone(name, phone):
    """Return E.164 number. Tark/US numbers get +1 prefix, others get +91."""
    if name and "tark" in name:
        return f"+16502781187"
    if name and "piyush" in name:
        return f"+971567556544"
    return f"+91{phone}"


def get_oncall_numbers(name=None):
    """Return a list of E.164 phone numbers for the current oncall (primary + secondary).

    If name is given, return numbers for that specific person only.
    Returns [] on failure.
    """
    if not GOOGLE_SHEET_ID and not GOOGLE_SHEET_URL:
        app.logger.warning("GOOGLE_SHEET_ID or GOOGLE_SHEET_URL not configured")
        return []

    try:
        records = get_oncall_list_from_sheet()

        if name:
            if name.startswith("tark"):
                return ["+16502781187"]
            if name.startswith("piyush"):
                return ["+971567556544"]

            for record in records:
                primary_name = record.get("Name", "").strip().lower()
                secondary_name = record.get("Secondary", "").strip().lower()
                lookup = name.strip().lower()

                if primary_name == lookup:
                    return [_format_phone(name, record.get("Phone", "").strip())]
                elif secondary_name == lookup:
                    return [_format_phone(name, record.get("S-Phone", "").strip())]
            return []

        current_date = datetime.now().strftime("%Y-%m-%d")
        date_matched = False

        app.logger.info(f"Looking up oncall owner for date: {current_date}")

        for record in records:
            record_date = record.get("Date", "").strip()
            if not record_date:
                continue

            if record_date == current_date:
                date_matched = True

            if date_matched:
                oncall_name = record.get("Name", "").strip()
                oncall_phone = record.get("Phone", "").strip()
                if oncall_name and oncall_phone:
                    numbers = [_format_phone(oncall_name, oncall_phone)]
                    s_phone = record.get("S-Phone", "").strip()
                    s_name = record.get("Secondary", "").strip()
                    if s_phone:
                        numbers.append(_format_phone(s_name, s_phone))
                    app.logger.info(f"oncall for {current_date}: primary={oncall_name}, secondary={s_name or 'none'}")
                    return numbers

        app.logger.warning(f"No oncall owner found for date {current_date}")
        return []

    except Exception as e:
        app.logger.error(f"Error reading from Google Sheet: {e}")
        log_exception(e)
        return []


# Backward-compat shim — returns the first (primary) number as a string or False
def get_oncall_number(name=None):
    numbers = get_oncall_numbers(name)
    return numbers[0] if numbers else False


def get_oncall_user_list():
    try:
        today = datetime.now().date()
        days_since_monday = today.weekday()  # Monday is 0
        week_start = today - timedelta(days=days_since_monday)
        
        # Calculate the end date (5 weeks from start of current week)
        week_end = week_start + timedelta(weeks=5, days=-1)  # End of 5th week (Sunday)
        
        app.logger.info(f"Looking up oncall owners from {week_start} to {week_end}")
        records = get_oncall_list_from_sheet()
        
        oncall_list = []
        
        for record in records:
            record_date_str = record.get("Date", "").strip()
            if not record_date_str:
                continue
            
            try:
                record_date = datetime.strptime(record_date_str, "%Y-%m-%d").date()
                
                # Check if the date falls within the 5-week range
                if week_start <= record_date <= week_end:
                    # Calculate the week start (Monday) and week end (Sunday) for this record
                    days_since_monday = record_date.weekday()  # Monday is 0
                    record_week_start = record_date - timedelta(days=days_since_monday)
                    record_week_end = record_week_start + timedelta(days=6)  # Sunday
                    
                    oncall_name = record.get("Name", "").strip()
                    oncall_phone = record.get("Phone", "").strip()
                    secondary_name = record.get("Secondary", "").strip()
                    secondary_phone = record.get("S-Phone", "").strip()

                    if oncall_name and oncall_phone:
                        oncall_list.append({
                            "week_start_date": record_week_start.strftime("%Y-%m-%d"),
                            "week_end_date": record_week_end.strftime("%Y-%m-%d"),
                            "name": f"@{oncall_name.lower()}",
                            "phone": oncall_phone,
                            "secondary_name": f"@{secondary_name.lower()}" if secondary_name else None,
                            "secondary_phone": secondary_phone if secondary_phone else None,
                        })
            except ValueError:
                app.logger.warning(f"Invalid date format in record: {record_date_str}")
                continue
        
        # Remove duplicates based on week_start_date, week_end_date, name, and phone
        seen = set()
        unique_oncall_list = []
        for entry in oncall_list:
            entry_key = (entry["week_start_date"], entry["week_end_date"], entry["name"], entry["phone"], entry.get("secondary_name"), entry.get("secondary_phone"))
            if entry_key not in seen:
                seen.add(entry_key)
                unique_oncall_list.append(entry)
        
        oncall_list = unique_oncall_list
        
        if oncall_list:
            app.logger.info(f"Found {len(oncall_list)} unique oncall owner(s) for weeks {week_start} to {week_end}")
            return format_oncall_list(oncall_list)
        else:
            app.logger.warning(f"No oncall owners found for weeks {week_start} to {week_end}")
            return []
            
    except Exception as e:
        app.logger.error(f"Error reading from Google Sheet: {e}")
        log_exception(e)
        return []