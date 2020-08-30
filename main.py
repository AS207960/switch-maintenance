import dataclasses
import datetime
import json
import pprint
import typing

import pytz
import requests

SWITCH_STATUS_URL = "https://status.nic.ch/availability/service/json"
STATUSPAGE_BASE_URL = "https://api.statuspage.io/v1"
STATUSPAGE_API_KEY = ""
STATUSPAGE_PAGE_ID = ""
STATUSPAGE_COMPONENT_ID = ""


@dataclasses.dataclass
class SWITCHMaintenance:
    systems: typing.List[str]
    environment: str
    from_time: datetime.datetime
    to_time: datetime.datetime
    reason: str
    remark: typing.Optional[str]


def get_timezone_name(timezone: typing.Union[int, str]):
    if timezone in pytz.all_timezones:
        return pytz.timezone(timezone)

    try:
        offset = int(timezone)
        if offset > 0:
            offset = '+' + str(offset)
        else:
            offset = str(offset)
        return 'Etc/GMT' + offset
    except ValueError:
        pass

    set_zones = set()
    for name in pytz.all_timezones:
        tzone = pytz.timezone(name)
        for utcoffset, dstoffset, tzabbrev in getattr(
                tzone, '_transition_info', [[None, None, datetime.datetime.now(tzone).tzname()]]
        ):
            if tzabbrev.upper() == timezone.upper():
                set_zones.add(tzone)

    return min(set_zones, key=lambda z: len(z.zone))


def parse_switch_timestamp(ts):
    parts = ts.split(" ")
    tz = parts[-2]
    actual_tz = get_timezone_name(tz)
    new_ts = " ".join(parts[:-2] + parts[-1:])
    parsed_ts = datetime.datetime.strptime(new_ts, "%a %b %d %H:%M:%S %Y")
    parsed_ts = (parsed_ts - actual_tz.utcoffset(parsed_ts)).replace(tzinfo=pytz.utc)
    return parsed_ts


def get_switch_maintenance():
    today = datetime.date.today()
    today_next_year = today.replace(year=today.year + 1)

    r = requests.get(SWITCH_STATUS_URL, params={
        "environment": "production",
        "start": today.strftime("%d-%m-%Y"),
        "end": today_next_year.strftime("%d-%m-%Y")
    })
    r.raise_for_status()
    data = r.json()["availability"]
    data = filter(lambda m: m["message-type"] == "DATA_MESSAGE", data)
    data = map(lambda m: SWITCHMaintenance(
        systems=m["message"]["data-message"]["concernedSystem"].split(", "),
        environment=m["message"]["data-message"]["environment"],
        from_time=parse_switch_timestamp(m["message"]["data-message"]["from"]),
        to_time=parse_switch_timestamp(m["message"]["data-message"]["to"]),
        reason=m["message"]["data-message"]["reason"],
        remark=m["message"]["data-message"]["remark"],
    ), data)
    return list(data)


def get_statuspage_maintenance():
    out = []
    page = 1

    def get_page(page=1):
        r = requests.get(f"{STATUSPAGE_BASE_URL}/pages/{STATUSPAGE_PAGE_ID}/incidents/scheduled", headers={
            "Authorization": f"OAuth {STATUSPAGE_API_KEY}"
        }, params={
            "page": page
        })
        r.raise_for_status()
        return r.json()

    while True:
        new_data = get_page()
        out.extend(new_data)
        if len(new_data) != 100:
            break
        page += 1

    return list(filter(lambda e: e["impact"] == "maintenance", out))


def main():
    global STATUSPAGE_API_KEY, STATUSPAGE_PAGE_ID, STATUSPAGE_COMPONENT_ID
    with open("secrets/statuspage.json") as f:
        statuspage_data = json.load(f)
        STATUSPAGE_API_KEY = statuspage_data["key"]
        STATUSPAGE_PAGE_ID = statuspage_data["page_id"]
        STATUSPAGE_COMPONENT_ID = statuspage_data["component_id"]

    switch_maintenance = get_switch_maintenance()
    statuspage_maintenance = get_statuspage_maintenance()

    switch_maintenance = list(filter(
        lambda m: m.environment == "production" and "epp.nic.ch" in m.systems, switch_maintenance
    ))
    statuspage_maintenance = list(filter(
        lambda m: m["status"] == "scheduled", statuspage_maintenance
    ))

    for switch_m in switch_maintenance:
        statuspage_m = None
        for m in statuspage_maintenance:
            scheduled_for = datetime.datetime.fromisoformat(m["scheduled_for"].replace("Z", "+00:00"))
            if scheduled_for == switch_m.from_time:
                statuspage_m = m

        incident_data = {
            "scheduled_for": switch_m.from_time.isoformat(),
            "scheduled_until": switch_m.to_time.isoformat(),
            "component_ids": [STATUSPAGE_COMPONENT_ID],
            "name": "SWITCH Maintenance",
            "status": "scheduled",
            "body": "The registry for .ch and .li domains will be undergoing maintenance. "
                    "Expect an inability to manage, register, or transfer domains with these extensions.",
            "impact_override": "maintenance",
            "scheduled_auto_in_progress": True,
            "scheduled_auto_completed": True,
            "auto_transition_to_maintenance_state": True,
            "auto_transition_to_operational_state": True,
            "auto_tweet_on_creation": True,
            "auto_tweet_one_hour_before": True,
            "auto_tweet_at_beginning": True,
            "auto_tweet_on_completion": True,
        }

        if statuspage_m:
            r = requests.patch(
                f"{STATUSPAGE_BASE_URL}/pages/{STATUSPAGE_PAGE_ID}/incidents/{statuspage_m['id']}",
                headers={
                    "Authorization": f"OAuth {STATUSPAGE_API_KEY}"
                }, json={
                    "incident": incident_data
                }
            )
            r.raise_for_status()
        else:
            r = requests.post(
                f"{STATUSPAGE_BASE_URL}/pages/{STATUSPAGE_PAGE_ID}/incidents",
                headers={
                    "Authorization": f"OAuth {STATUSPAGE_API_KEY}"
                }, json={
                    "incident": incident_data
                }
            )
            r.raise_for_status()


if __name__ == "__main__":
    main()
