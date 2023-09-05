# glacial-alerter
Script to send periodic updates about room availability for Glacial National Park

By default, the script runs every 15 minutes. These times should be around the times that the website publishes room updates. It sends emails whenever the number of available rooms changes between zeros and non-zero for any (date, hotel, room) tuple as specified in the alerts file. It saves historical availability data for all rooms over the specified date range.

## email account
You must specify a gmail account in a file called `login.py` with content:
```
ADDRESS = "my_address"
PASSWORD = "my_app_password"
```
See [this](https://support.google.com/mail/answer/185833?hl=en) for generating an app password.

## hotel room codes
To find the codes corresponding to hotels and rooms, refer to `titles.csv`. This file is saved the first time that the script runs an update and read from thereafter.

## alerts file syntax
The alerts file contains json objects with lists of dates and lists of odjects with a hotel_code and list of room codes. See `alert.json`.

## example usage
```
python3 glacier_alert.py --start_date 2024-08-31 --end_date 2024-09-09 --alerts_file alert.json --recipients some.address@gmail.com
```
