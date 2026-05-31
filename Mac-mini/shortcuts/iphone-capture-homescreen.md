# iPhone Recipe Capture — Home-Screen Setup

**Phase 21 v2.** The recipe-intake flow lives on the Mini Ops console
at `homeserver:8503/?tab=capture`. This doc walks through adding it as
a one-tap icon on the iPhone home screen.

Two iPhones share the same setup; each is configured once.

---

## What it does

Tap the icon → Safari opens the Capture tab → take/select a photo →
pick what to do (Save / Save + Send / Send only) → wait ~10 s →
result appears on the page.

The icon is a Safari "web clip," not an app. The native Camera app is
unaffected — normal photos work exactly like before.

---

## One-time setup (per iPhone)

### 1. Confirm Tailscale is connected

Open the **Tailscale** app on the iPhone. The toggle should be on and
the status should show as connected. `homeserver` resolves via
Tailscale MagicDNS — no Tailscale, no `homeserver`.

Quick check: open Safari, browse to
`http://homeserver:8504/healthz`. You should see `{"ok": true}`.
If you get a connection error, fix Tailscale before continuing.

### 2. Open the Capture tab in Safari

Browse to:

```
http://homeserver:8503/?tab=capture
```

The page should load with the "Capture a recipe" heading visible.

### 3. Add to Home Screen

- Tap the **Share** icon (square with up-arrow, bottom-center).
- Scroll the action sheet, tap **Add to Home Screen**.
- Name it something short — "Capture Recipe" works.
- Tap **Add** in the top right.

A new icon appears on the home screen. Tapping it opens Safari
directly on the Capture tab.

### 4. Test it

Tap the icon → the page loads → tap **Browse Files** (or the camera
icon depending on iOS version) under the file upload widget → take or
select a photo of a recipe → pick **Save to library** for the first
test → tap upload.

After ~10 s, you should see "Saved recipe #N. Open it on the Recipes
tab." Open the Recipes tab in the console (laptop or phone) and
confirm the new recipe is there.

---

## Daily use

1. Tap the home-screen icon.
2. Tap the photo widget; iOS will offer **Take Photo** or **Photo
   Library** — both work.
3. Pick the intent:
   - **Save to library** — recipe is parsed and stored. Nothing
     happens in Todoist.
   - **Save and send to Todoist** — recipe is stored AND ingredients
     get pushed to the Grocery List (Todoist).
   - **Send to Todoist only** — ingredients go to Todoist, recipe is
     NOT kept in the library. Useful for one-off shopping (friend's
     handout, magazine).
4. Adjust **Servings** if the recipe will be cooked for a different
   crowd than the original calls for.
5. Tap upload → wait → see result.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Page won't load | Tailscale disconnected | Open Tailscale app, toggle on |
| Page loads but spinner runs forever | Mini's `jobs-consumer` or `console` LaunchAgent down (rare — Streamlit calls the runner directly, but a hung Gemini API call could block) | SSH to mini, `launchctl kickstart -kp gui/$UID/com.home-tools.console` |
| "Extraction failed (ollama_error)" | Gemini API key bad, quota exhausted, or transient outage | Check `meal_planner/.env` `GEMINI_API_KEY`; check [Google AI Studio quota](https://aistudio.google.com/) |
| "Extraction failed (parse_fail)" | Gemini returned text that didn't parse as JSON | Retake the photo with better lighting / less skew |
| "Already processed (sha=…)" | Identical photo already uploaded | Expected. Look in **Recent intakes** on the same page |
| "Recipe saved but Todoist push failed" | `TODOIST_SECTIONS` env not set on the consumer, or Todoist API down | Recipe is saved — retry the send from the Recipes tab |

## Notes

- HEIC photos work natively. iPhone Camera defaults to HEIC; the
  upload accepts it and Gemini 2.5 Flash decodes it without conversion.
- The home-screen icon doesn't keep you signed in to anything because
  there's no sign-in — Tailscale is the auth perimeter. If someone
  steals the unlocked phone they can hit the dashboard, but they could
  also open any other Tailnet resource.
- To rotate access for a lost phone: in the Tailscale admin, remove
  the device. No per-device tokens to revoke.
