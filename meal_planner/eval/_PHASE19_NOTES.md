# Phase 19 — Golden instruction notes

Generated 2026-05-30 by Claude (Opus 4.7) reading each `IMG_*.JPG` directly
via the `Read` tool. Each `IMG_*.golden.json` was extended with an
`instructions` field; the title/ingredients/tags fields were not touched.

## All 12 goldens have non-null instructions

No images were ingredient-only; every photo in this corpus shows at least
some cooking instructions. (The plan reserved `instructions: null` for
ingredient-only cards, but none qualified.)

## Partial transcriptions

Three photos have text running off the visible edge of the image. The
golden captures the clearly-readable portion only; later steps not
visible in the photo are not invented. Bake-off comparisons against these
goldens should be interpreted with the understanding that the local model
*may* legitimately produce text the golden cannot judge (because the
golden does not represent the full source).

| Image | Visible portion | Cut-off |
|---|---|---|
| `IMG_9960.JPG` (Instant Pot Butter Chicken) | Steps 1, 2, 7, 8, 9 + partial "USING LEFTOVER SAUCE" bullets | Steps 3-6 of main instructions, right margin of bullets in sauce section |
| `IMG_9963.JPG` (Ginger Fried Rice) | Through "Step 2 - Cut chicken" with three sub-bullets | Everything after the chicken-cutting bullets |
| `IMG_9964.JPG` (Best Chocolate Chip Cookies) | Steps 1-5 with reasonable detail | Right margin of step 5 and any subsequent steps; some interior text in step 3 was hard to read |

## Other transcription notes

- **IMG_9958 (Mom's Dan Gung):** The photo shows handwritten edits that
  override printed text (e.g., "salts of chicken" struck through, replaced
  with "chicken stock"; "cover with a plate" struck through, replaced
  with "then put lid on pot"). The golden uses the corrected/handwritten
  version since that represents the user's actual recipe.
- **IMG_9962 (Chicken Juk):** Has multiple handwritten additions —
  vinegar amount (2-3 tbsp), sesame oil + soy sauce additions, "serve
  chicken sauce on the side". Captured in parentheticals.
- **IMG_9959 (Sushi):** The photo's "sushi vinegar" and "preparing the
  rice" sections were combined into a single `instructions` field with
  preserved structure. Note that the sushi-vinegar mixing quantities
  appear in the instructions (not the ingredients list, since the
  existing golden's ingredients only lists the sushi components).
- **IMG_9965 (Tiramisu):** Handwritten "Notes" section at the top of the
  photo (Moka pot coffee, Pavesini cookies count, tupperware dimensions)
  is captured as a note line near the start of instructions.
