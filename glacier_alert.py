import argparse
from email.message import EmailMessage
from functools import partial
import json
import pandas as pd
from pathlib import Path
import requests
import smtplib
import ssl
from time import sleep
from apscheduler.schedulers.blocking import BlockingScheduler
from login import ADDRESS, PASSWORD

MAX_DAYS_REQUEST = 31

# url base for get requests
API = "https://webapi.xanterra.net/v1/api"
# saved hotel rooms names
INFO = Path(__file__).parent / "info.csv"
# data collected from last run
LAST = Path(__file__).parent / "last.csv"
# all historical data
SAVED = Path(__file__).parent / "saved.csv"


def get_hotel_titles() -> pd.DataFrame:
    resp = requests.get(API + "/property/hotels/glaciernationalparklodges")
    hotels = resp.json().values()
    return pd.DataFrame(
        [(h["code"], h["title"]) for h in hotels], columns=["hotel_code", "hotel_title"]
    )


def get_room_info(hotel_code: str) -> pd.DataFrame:
    resp = requests.get(API + "/property/rooms/glaciernationalparklodges/" + hotel_code)
    rooms = resp.json().values()
    return pd.DataFrame(
        [(r["code"], r["title"], r["occupancyMax"]) for r in rooms], columns=["room_code", "room_title", "max_occupancy"]
    )


def get_hotel_rooms() -> pd.DataFrame:
    hotels = get_hotel_titles()
    rooms = pd.concat(
        [
            get_room_info(code).assign(hotel_code=code)
            for code in hotels["hotel_code"].unique()
        ]
    )
    return rooms.merge(hotels, on=["hotel_code"])


def get_room_availability(
    hotel_code: str, start_date: pd.Timestamp, num_days: int
) -> pd.DataFrame:
    # we want to avoid submit requests too quickly
    sleep(0.010)
    date_str = start_date.strftime("%m/%d/%Y")
    resp = requests.get(
        API + "/availability/rooms/glaciernationalparklodges/" + hotel_code,
        params=dict(
            date=date_str,
            nights=1,
            limit=num_days,
            rate_code="INTERNET",
            is_group=False,
        ),
    )
    now = pd.Timestamp.now()
    try:
        daterooms = resp.json()["availability"].values()
        # we don't want exclusive rates, for example employee discounts
        return pd.DataFrame(
            [
                (
                    pd.to_datetime(obj["date"]),
                    hotel_code,
                    r["roomCode"],
                    r["available"],
                    r["price"],
                    now,
                    pd.to_datetime(r["updated"]),
                )
                for obj in daterooms
                for r in obj["rooms"]
                if r["rateCode"] == "INTERNET"
            ],
            columns=[
                "date",
                "hotel_code",
                "room_code",
                "available",
                "price",
                "sampled",
                "updated",
            ],
        )
    except:
        print(resp)


def make_link(hotel_code: str, date: pd.Timestamp) -> str:
    date_str = date.strftime("%m-%d-%Y")
    req = requests.Request(
        "GET",
        "https://secure.glaciernationalparklodges.com/booking/lodging-select",
        params=dict(
            dateFrom=date_str, nights=1, destination=hotel_code, adults=1, children=0
        ),
    ).prepare()
    return f"<a href='{req.url}'>link</a>"


def send_room_updates(changes: pd.DataFrame, recipients: list):
    msg = EmailMessage()
    msg["Subject"] = "Glacier room availability update"
    msg["From"] = ADDRESS
    msg["To"] = ", ".join([ADDRESS] + recipients)
    body = "<pre>"
    changes = changes.sort_index()
    changes["link"] = changes.apply(
        lambda row: make_link(row["hotel_code"], row["date"]), axis=1
    )
    if changes["opened"].sum() > 0:
        opened_str = changes.loc[
            changes["opened"], ["date", "hotel_title", "room_title", "link"]
        ].to_string(index=False, header=False, justify="left")
        body += (
            "The following hotel rooms have become <b>available</b>:<hr><p>"
            + opened_str
            + "</p><hr>"
        )
    if changes["closed"].sum() > 0:
        closed_str = changes.loc[
            changes["closed"], ["date", "hotel_title", "room_title", "link"]
        ].to_string(index=False, header=False, justify="left")
        body += (
            "The following hotel rooms have became <b>unavailable</b>:<hr><p>"
            + closed_str
            + "</p><hr>"
        )
    body += "</pre>"
    msg.set_content(body, "html")
    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", port=587) as s:
        s.starttls(context=context)
        s.login(ADDRESS, PASSWORD)
        s.send_message(msg)


def run_update(
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    alert_on: pd.DataFrame,
    recipients: list,
):
    # get all hotels and rooms
    if INFO.exists():
        info = (
            pd.read_csv(INFO).set_index(["hotel_code", "room_code"]).drop_duplicates()
        )
    else:
        info = get_hotel_rooms().set_index(["hotel_code", "room_code"])
    # read previously-gathered data
    if LAST.exists():
        last = pd.read_csv(LAST, parse_dates=["date"]).set_index(
            ["date", "hotel_code", "room_code"]
        )["available"]
    else:
        last = pd.Series(name="available", dtype=int)
    dates = pd.date_range(start_date, end_date)
    date_chunks = [dates[i:i+MAX_DAYS_REQUEST] for i in range(0, len(dates), MAX_DAYS_REQUEST)]
    new_df = pd.concat(
        [
            get_room_availability(code, min(chunk), len(chunk))
            for code in info.index.unique(level="hotel_code")
            for chunk in date_chunks
        ]
    ).set_index(["date", "hotel_code", "room_code"])

    # get changes
    current = new_df["available"]
    last = last.reindex(index=current.index, fill_value=0)
    changes = pd.DataFrame()
    changes["opened"] = (current > 0) & (last == 0)
    changes["closed"] = (current == 0) & (last > 0)
    changes = changes.reindex(pd.MultiIndex.from_frame(alert_on), fill_value=False)

    # save data
    if SAVED.exists():
        new_df.loc[current != last].to_csv(SAVED, header=False, mode="a")
    else:
        new_df.to_csv(SAVED, header=True)
    current.to_csv(LAST)
    info["latest_price"] = new_df.groupby(["hotel_code", "room_code"])["price"].mean().round(2).reindex(info.index)
    info.to_csv(INFO)

    # send updates
    now_str = pd.Timestamp.now().strftime("%Y-%m-%d %X")
    if changes.sum().sum() > 0:
        send_room_updates(changes.join(info).reset_index(), recipients)
        print(f"Sent email with room updates at {now_str}")
    else:
        print(f"No room updates to send at {now_str}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start_date", type=pd.Timestamp, required=True)
    parser.add_argument("--end_date", type=pd.Timestamp, required=True)
    parser.add_argument("--alerts_file", type=argparse.FileType("r"), required=True)
    parser.add_argument("--save_file", type=argparse.FileType("w"), default=None)
    parser.add_argument("--recipients", type=str, nargs="*")
    # parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    dates = pd.date_range(args.start_date, args.end_date)
    assert len(dates) >= 1
    rows = []
    for entry in json.load(args.alerts_file):
        for date in entry["dates"]:
            for hotel in entry["hotels"]:
                for code in hotel["room_codes"]:
                    rows.append((date, hotel["hotel_code"], code))
    alert_on = pd.DataFrame(rows, columns=["date", "hotel_code", "room_code"])

    func = partial(run_update, args.start_date, args.end_date, alert_on, args.recipients)
    sched = BlockingScheduler()
    # room updates seem to happen around these minutes each hour
    for mins in [11, 26, 41, 56]:
        sched.add_job(func, "cron", minute=mins, second=20)
    sched.start()


if __name__ == "__main__":
    main()
