# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Linux Server Health Monitor Enterprise (LSHME).

Read-only health monitoring, reporting and alerting for Linux servers.
The core runs on the Python 3.6+ standard library (RHEL/Oracle Linux 7 ship
3.6); report libraries are optional and have built-in fallbacks.
"""
import os

APP_NAME = "LRS Vitals"
APP_TAGLINE = "Linux Server Health Monitor"
APP_SHORT = "Vitals"
COMMAND = "vitals"  # also installed as systemhealth and healthcheck
VERSION = "1.4.0"
VENDOR = "Rohit Santosh Trimukhe"
VENDOR_CITY = "Mumbai"
OWNER_EMAIL = "rohittrimukhe2@gmail.com"
COPYRIGHT = "© 2026 Rohit Santosh Trimukhe. All rights reserved."
COPYRIGHT_ASCII = "(c) 2026 Rohit Santosh Trimukhe. All rights reserved."
BRAND_LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "lrs_logo.png")
