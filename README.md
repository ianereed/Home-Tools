# Home-Tools

Personal tooling repo. Four independent projects:

---

## Projects

### `health-dashboard/`
Streamlit dashboard for personal health and recovery data from Apple Watch, Suunto, and Garmin/Strava. Runs locally on a Mac, accessible from iPhone via Tailscale.

See [`health-dashboard/README.md`](health-dashboard/README.md) for setup and usage.

### `event-aggregator/`
Local pipeline that monitors messaging sources (Gmail, Slack, iMessage, WhatsApp, Discord) for mentions of upcoming events and writes them to Google Calendar. All LLM extraction runs via a local Ollama process — message content never leaves the machine.

See [`event-aggregator/README.md`](event-aggregator/README.md) for setup and usage.

### `colorado-trip/`
Python scripts for building and managing a Colorado trip itinerary in Google Sheets.

See [`colorado-trip/research-context.md`](colorado-trip/research-context.md) for context.

### `meal-planner/`
Google Sheets + Todoist grocery automation. A dynamic menu in the Sheet pushes any recipe's ingredients to Todoist, categorized by aisle section via Claude. A Python script consolidates the list when multiple recipes overlap. A photo capture modal lets you add new recipes by pointing your camera at a cookbook page.

See [`meal-planner/apps-script/SETUP.md`](meal-planner/apps-script/SETUP.md) for setup.
