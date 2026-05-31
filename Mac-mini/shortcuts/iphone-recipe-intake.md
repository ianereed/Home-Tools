# iPhone Recipe Intake — Apple Shortcut

**Phase 21 deliverable.** A photo-driven recipe capture flow: take a photo
on the iPhone, pick what to do with it, and let the mini handle extraction +
filing + optional Todoist push.

Two iPhones can share the same Shortcut definition; each is set up once.

---

## What it does

1. You take a photo of a recipe (magazine page, cookbook, friend's card).
2. The Shortcut asks: **Save to library** / **Save + send to Todoist** /
   **Send to Todoist only**.
3. It POSTs the photo + your choice to the mini at
   `http://homeserver:8504/iphone-intake`.
4. The mini extracts the recipe via Gemini, then:
   - **save** → recipe saved at `http://homeserver:8503/?tab=recipes`.
   - **save_and_shop** → recipe saved AND ingredients pushed to Todoist.
   - **shop_only** → ingredients pushed to Todoist, no recipe persisted.
5. A notification fires on the iPhone with the result.

---

## One-time setup (per iPhone)

### 1. Get the auth token

The Shortcut needs `HOME_TOOLS_HTTP_TOKEN`. On the mini, run:

```bash
security find-generic-password -a 'home-tools' -s 'jobs_http_token' -w \
    "$HOME/Library/Keychains/login.keychain-db"
```

(That's the same token `jobs/run-http.sh` exports at boot.) Copy the
output — you'll paste it into the Shortcut below.

### 2. Confirm the phone is on the tailnet

Open the Tailscale app on the iPhone. `homeserver` must resolve via
MagicDNS. To verify, open Safari and load
`http://homeserver:8504/healthz`. You should see `{"ok": true}`.

### 3. Build the Shortcut

Open the **Shortcuts** app → tap **+** → name it
*"Capture Recipe"*.

#### Action 1 — Take Photo
- **Take Photo**
- Tap the action's `›` → set "Show Camera Preview" **On** and
  "Camera" to **Back**.

#### Action 2 — Pick the intent
- **Choose from Menu**
- Prompt: `What do you want to do?`
- Menu items (in order):
  - `Save to library`
  - `Save + send to Todoist`
  - `Send to Todoist only`

For each menu branch, set an internal text variable so the rest of the
Shortcut can read it:

- Inside *"Save to library"*: **Text** action with value `save`, then
  **Set Variable** named `intent` to that Text.
- Inside *"Save + send to Todoist"*: **Text** `save_and_shop`, **Set
  Variable** `intent`.
- Inside *"Send to Todoist only"*: **Text** `shop_only`, **Set
  Variable** `intent`.

After **End Menu**, add **Get Variable** for `intent` so subsequent
actions can reference it.

#### Action 3 — Save the token

Add a **Text** action with the token string from step 1 as its value.
Then **Set Variable** named `token`.

> Caveat: Shortcuts has no proper secret store. The token is in
> plaintext inside this Shortcut. If the phone is lost, rotate the
> mini's token (re-run `jobs/install.sh` or regenerate manually).

#### Action 4 — POST to the mini

- **Get Contents of URL**
  - URL: `http://homeserver:8504/iphone-intake`
  - Method: `POST`
  - Headers:
    - `Authorization`: `Bearer <token variable>` (use **Magic Variable**
      to insert `token`)
  - Request Body: **Form**
    - `photo` → the Photo from Action 1 (Magic Variable; pick "Photo")
    - `intent` → the `intent` variable (Magic Variable)
    - `servings` → `4` (or leave blank; the server defaults to 4)

This returns a JSON response. The Shortcut's "Contents of URL"
output is the response body.

#### Action 5 — Get the task id

- **Get Dictionary Value**
  - Get: `Value`
  - Key: `id`
  - Dictionary: the output of Action 4

Store as `task_id` via **Set Variable**.

#### Action 6 — Poll for result

- **Repeat** (up to 30 times)
  - **Wait** 2 seconds
  - **Get Contents of URL**
    - URL: `http://homeserver:8504/jobs/<task_id variable>` (insert
      `task_id` as a Magic Variable)
    - Method: `GET`
    - Headers: `Authorization`: `Bearer <token variable>`
  - **Get Dictionary Value** → Key: `status` → Dictionary: the GET response
  - **If** the Dictionary Value `is not` `pending`:
    - **Exit Repeat**
  - End If
- End Repeat

#### Action 7 — Notify

- **Show Notification**
  - Title: `Recipe intake`
  - Body: the final `status` value (or the full response body).

That's the full Shortcut. Tap **Done**.

### 4. Test it

Snap a photo of any recipe, pick *Save to library*. After ~10 seconds
you should get a "Recipe intake — success" notification, and the recipe
should appear at `http://homeserver:8503/?tab=recipes`.

---

## Response shape (for debugging)

`POST /iphone-intake` returns one of:

| HTTP | Body                                                         | Meaning                          |
| ---- | ------------------------------------------------------------ | -------------------------------- |
| 202  | `{"id": "<task>", "sha": "<16hex>", "status": "enqueued"}`   | Accepted — poll `/jobs/<id>`.    |
| 200  | `{"id": null, "sha": "<16hex>", "status": "duplicate"}`      | Same photo already processed.    |
| 400  | `{"error": "..."}`                                           | Bad request (missing field etc.) |
| 401  | `{"error": "missing bearer token"}` / `bad token`            | Auth failed.                     |
| 500  | `{"error": "..."}`                                           | Server-side problem.             |

`GET /jobs/<id>` returns:

| Body                                                    | Meaning                                          |
| ------------------------------------------------------- | ------------------------------------------------ |
| `{"status": "pending", "result": null, "error": null}`  | Still running — keep polling.                    |
| `{"status": "success", "result": {...}, "error": null}` | Done. `result.status` is `ok` / `todoist_failed` / `parse_fail` / `ollama_error`. |
| `{"status": "error", "result": null, "error": "..."}`   | Worker crashed.                                  |

---

## Troubleshooting

| Symptom                            | Likely cause                                                | Fix                                                          |
| ---------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------ |
| Notification says "401 bad token"  | Paste error or token rotated.                               | Re-pull the token (setup step 1) and update the `token` Text action. |
| Polling times out at 30 iterations | Recipe extraction took >60s, or the consumer crashed.       | SSH to the mini, `tail Home-Tools/logs/jobs-consumer.err`.   |
| Same photo keeps getting "duplicate" | The photo's SHA is in `photos_intake`.                    | Expected. Re-photograph or use the console to delete the row if you really want to re-ingest. |
| `result.status = "ollama_error"`   | Gemini API key bad or quota exhausted.                      | Check `meal_planner/.env` on the mini for `GEMINI_API_KEY`.  |
| `result.status = "todoist_failed"` | `TODOIST_SECTIONS` env missing on the consumer.             | See `jobs/install.sh` for the section-map setup.             |
| "Cannot connect" in Safari at `:8504/healthz` | Phone not on tailnet, or LaunchAgent down.       | Tailscale app → reconnect; on mini, `launchctl list \| grep jobs-http`. |
