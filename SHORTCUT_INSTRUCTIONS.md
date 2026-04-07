# iOS Shortcut: Apple Health Export

This Shortcut exports your Apple Health sleep and heart rate data to iCloud Drive,
where the dashboard's Python collector picks it up automatically.

## Step 1: Create the Shortcut

1. Open the **Shortcuts** app on your iPhone
2. Tap **+** to create a new Shortcut
3. Name it **"Health Export"**

### Add these actions in order:

**Action 1: Find Health Samples (Sleep)**
- Tap "Add Action" → search "Find Health Samples"
- Type: **Sleep Analysis**
- Filter: Start Date is in the last **7 days**
- Sort by: Start Date (Newest First)

**Action 2: Set Variable**
- Set variable name to **sleepData**

**Action 3: Find Health Samples (Heart Rate)**
- Tap "Add Action" → search "Find Health Samples"
- Type: **Heart Rate**
- Filter: Start Date is in the last **1 day**
- Sort by: Start Date (Newest First)
- Limit: **200** (to avoid massive files)

**Action 4: Set Variable**
- Set variable name to **hrData**

**Action 5: Text**
- Add a Text action with this template:

```
{
  "exported": "Current Date (ISO 8601)",
  "sleep": [
    Repeat with each item in sleepData:
    {
      "date": "Start Date (ISO 8601)",
      "total_minutes": "Duration (minutes)",
      "source": "Source Name",
      "deep_minutes": 0,
      "rem_minutes": 0,
      "light_minutes": 0,
      "awake_minutes": 0
    }
    End Repeat
  ],
  "heart_rate": [
    Repeat with each item in hrData:
    {
      "timestamp": "Start Date (ISO 8601)",
      "bpm": "Value",
      "context": "resting",
      "source": "Source Name"
    }
    End Repeat
  ]
}
```

> **Note:** Apple Health Shortcuts cannot break down sleep stages (deep/REM/light).
> Only total sleep duration is available. Sleep stage data comes from Garmin.

**Action 6: Save File**
- Save the text output to: **iCloud Drive > HealthExport** folder
- Filename: `health_export.json`
- Ask Where to Save: **OFF**
- Overwrite: **ON**

## Step 2: Create the HealthExport Folder

1. Open the **Files** app on your iPhone
2. Navigate to **iCloud Drive**
3. Create a new folder called **HealthExport**

## Step 3: Automate It

1. In Shortcuts, go to the **Automation** tab
2. Tap **+** → **Time of Day**
3. Set time to **7:30 AM** (or whenever you're usually using your phone)
4. Set to **Daily**
5. Select your "Health Export" shortcut
6. Toggle **"Run Immediately"** ON (no confirmation needed)

## Step 4: Verify on Mac

After the Shortcut runs, check that the file appears at:
```
~/Library/Mobile Documents/com~apple~CloudDocs/HealthExport/health_export.json
```

You can test immediately by running the Shortcut manually in the Shortcuts app.

## Limitations

- **Sleep stages**: Shortcuts can only get total sleep duration, not deep/REM/light breakdown.
  Your Garmin provides sleep stage data.
- **Phone must be unlocked**: The automation runs when your phone is unlocked at the scheduled time.
  If it misses, it'll catch up next day (the export always covers the last 7 days).
- **Heart rate limit**: We limit to 200 samples per day to keep file sizes reasonable.

## Fallback: Health Auto Export App

If the Shortcut approach proves unreliable, the "Health Auto Export" app ($4.99 one-time)
can automatically export all Apple Health data to a local folder or REST endpoint without
any manual intervention.
