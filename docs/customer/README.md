# Customer document pack

Hand these to customers. All files are private and proprietary to LRS Services Pvt Ltd.

| File | What it is |
|---|---|
| `LRS-Vitals-Install-Guide.pdf` (and `.html`) | One-page install and first-time setup guide |
| `LRS-Vitals-What-We-Monitor.pdf` (and `.html`) | Two-page sheet: what is checked, how often, where you see it |
| `samples/db01_daily.pdf` / `.html` | Sample daily report |
| `samples/db01_weekly.pdf` / `.html` | Sample weekly report (growth by date, capacity forecast, comparison) |
| `samples/db01_monthly.pdf` / `.html` | Sample monthly summary report |
| `samples/db01_monthly_comprehensive.pdf` / `.html` | Sample monthly comprehensive report (inventory, roles, services) |
| `samples/db01_monthly_database.pdf` / `.html` | Sample database report for a DBA team |

The samples use invented data for a made-up server `db01` and customer "Example Industries". Open the `.html` files in a browser to
see the interactive charts (hover for value, date and time).

To rebuild the samples with your own customer name and logo, edit `tools/demo_reports.py`, then run on a Linux server:

    python3 tools/demo_reports.py OUTPUT_DIR
