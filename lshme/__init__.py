# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Linux Server Health Monitor Enterprise (LSHME).

Read-only health monitoring, reporting and alerting for Linux servers.
The core runs on the Python 3.6+ standard library (RHEL/Oracle Linux 7 ship
3.6); report libraries are optional and have built-in fallbacks.
"""
import os

APP_NAME = "Linux Server Health Monitor Enterprise"
APP_SHORT = "LSHME"
VERSION = "1.2.0"
VENDOR = "LRS Services Pvt Ltd"
VENDOR_CITY = "Mumbai"
COPYRIGHT = "© 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved."
COPYRIGHT_ASCII = "(c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved."
BRAND_LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "lrs_logo.png")
