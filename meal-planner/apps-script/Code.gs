// =============================================================================
// Meal Planner — Google Apps Script
// Attach this script to your recipe Google Sheet via Extensions > Apps Script
// =============================================================================
//
// Script Properties (set once via Project Settings > Script properties):
//   TODOIST_API_TOKEN   — your Todoist API token
//   TODOIST_PROJECT_ID  — numeric ID of your grocery Todoist project
//   TODOIST_SECTIONS    — JSON map of section names to section IDs
//                         e.g. {"Fruits + Veggies":"id","Dairy":"id","Meats":"id"}
//   GEMINI_API_KEY      — free key from aistudio.google.com
//   SHEET_NAME          — sheet tab name (default: "Sheet1")
//
// Sheet format:
//   Row 1: Recipe name (one per column)
//   Row 2+: Ingredients, one per cell ("2 cups flour", "1 lb chicken", ...)
//   Empty cell = end of that recipe's ingredient list

const PROPS = PropertiesService.getScriptProperties();
const TODOIST_LABEL = 'meal-planner';

// =============================================================================
// Menu
// =============================================================================

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Grocery')
    .addItem('Build Grocery List…', 'openRecipeSidebar_')
    .addItem('Add Recipe from Photo…', 'openPhotoCapture_')
    .addSeparator()
    .addItem('Consolidate List', 'consolidateList_')
    .addItem('Sort List', 'sortList_')
    .addItem('Clear List', 'clearList_')
    .addSeparator()
    .addItem('Create / Update Readme Sheet', 'createReadmeSheet_')
    .addToUi();
}

// Reserved sheet name — excluded from recipe lists and photo capture dropdown
const README_SHEET_NAME = 'readme';

// =============================================================================
// Recipe sidebar — multi-select push
// =============================================================================

function openRecipeSidebar_() {
  const template = HtmlService.createTemplateFromFile('RecipeSidebar');
  template.recipesJson = JSON.stringify(getAllRecipes_());
  SpreadsheetApp.getUi().showSidebar(template.evaluate().setTitle('Grocery List'));
}

/** Called from sidebar on load. Returns [{name, colIndex, sheetName}, ...] */
function getRecipesForSidebar() {
  return getAllRecipes_();
}

/**
 * Called from sidebar when user clicks "Add to Grocery List".
 * @param {{sheetName: string, colIndex: number}[]} selections  Selected recipes
 * @returns {string}  Status message to display in the sidebar
 */
function pushSelectedRecipes(selections) {
  if (!selections || selections.length === 0) {
    return 'No recipes selected.';
  }

  // Group by sheetName so we only open each sheet once
  const bySheet = {};
  selections.forEach(({ sheetName, colIndex }) => {
    if (!bySheet[sheetName]) bySheet[sheetName] = [];
    bySheet[sheetName].push(colIndex);
  });

  const allIngredients = [];
  Object.entries(bySheet).forEach(([sheetName, colIndices]) => {
    const sheet = getSheet_(sheetName);
    const lastRow = sheet.getLastRow();
    colIndices.forEach(colIndex => {
      for (let row = 2; row <= lastRow; row++) {
        const val = sheet.getRange(row, colIndex + 1).getValue();
        if (!val) break;
        allIngredients.push(String(val).trim());
      }
    });
  });

  if (allIngredients.length === 0) {
    return 'No ingredients found in the selected recipes.';
  }

  const categorized = categorizeIngredients_(allIngredients);
  const pushed = createTodoistTasks_(categorized);

  const n = selections.length;
  return `Added ${pushed} ingredients from ${n} recipe${n > 1 ? 's' : ''} to your grocery list.`;
}

// =============================================================================
// Todoist task creation
// =============================================================================

function createTodoistTasks_(categorized) {
  const token = PROPS.getProperty('TODOIST_API_TOKEN');
  const projectId = PROPS.getProperty('TODOIST_PROJECT_ID');
  const sections = JSON.parse(PROPS.getProperty('TODOIST_SECTIONS') || '{}');
  const sectionNames = Object.keys(sections);
  const fallbackSectionId = sections[sectionNames[0]];

  let pushed = 0;
  categorized.forEach(({ ingredient, section }) => {
    const sectionId = sections[section] || fallbackSectionId;
    const resp = UrlFetchApp.fetch('https://api.todoist.com/api/v1/tasks', {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: `Bearer ${token}` },
      payload: JSON.stringify({
        content: ingredient,
        project_id: projectId,
        section_id: sectionId,
        labels: [TODOIST_LABEL],
      }),
      muteHttpExceptions: true,
    });
    if (resp.getResponseCode() === 200) pushed++;
  });

  return pushed;
}

// =============================================================================
// Ingredient categorization via Gemini
// =============================================================================

function categorizeIngredients_(ingredients) {
  const apiKey = PROPS.getProperty('GEMINI_API_KEY');
  const sections = JSON.parse(PROPS.getProperty('TODOIST_SECTIONS') || '{}');
  const sectionNames = Object.keys(sections);
  const fallback = sectionNames[0] || 'Other';

  const prompt =
    `Categorize each grocery ingredient into one of these sections: ${sectionNames.join(', ')}.\n\n` +
    `Ingredients:\n${ingredients.map((i, n) => `${n + 1}. ${i}`).join('\n')}\n\n` +
    `Respond with a JSON array only — no other text:\n` +
    `[{"ingredient": "...", "section": "..."}, ...]\n\n` +
    `Use the exact section names provided. If unsure, use "${fallback}".`;

  const resp = UrlFetchApp.fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${apiKey}`,
    {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({
        contents: [{ parts: [{ text: prompt }] }],
      }),
      muteHttpExceptions: true,
    }
  );

  const raw = resp.getContentText();
  const body = JSON.parse(raw);

  if (!body.candidates || body.candidates.length === 0) {
    throw new Error('Gemini error: ' + raw);
  }

  const text = body.candidates[0].content.parts[0].text;
  const match = text.match(/\[[\s\S]*\]/);
  if (!match) throw new Error('Could not parse categorization response from Gemini.');
  return JSON.parse(match[0]);
}

// =============================================================================
// Consolidate list (Gemini)
// =============================================================================

function consolidateList_() {
  const tasks = fetchLabeledTasks_();

  if (tasks.length === 0) {
    SpreadsheetApp.getActiveSpreadsheet().toast('Grocery list is empty — nothing to consolidate.', 'Grocery', 4);
    return;
  }

  const apiKey = PROPS.getProperty('GEMINI_API_KEY');
  const sections = JSON.parse(PROPS.getProperty('TODOIST_SECTIONS') || '{}');
  const sectionNames = Object.keys(sections);
  const fallback = sectionNames[0];

  const ingredientLines = tasks.map(t => `- ${t.content}`).join('\n');
  const prompt =
    `You are a grocery list assistant. Consolidate this ingredient list:\n` +
    `- Combine duplicate or equivalent ingredients into one entry\n` +
    `- Sum quantities where possible (e.g. "1 cup olive oil" + "2 tbsp olive oil" → "1 cup + 2 tbsp olive oil")\n` +
    `- Assign each item to one of these sections: ${sectionNames.join(', ')}\n` +
    `- Keep the format "quantity unit ingredient" (e.g. "2 cups flour")\n\n` +
    `Ingredients:\n${ingredientLines}\n\n` +
    `Respond with a JSON array only — no other text:\n` +
    `[{"content": "...", "section": "..."}, ...]\n\n` +
    `Use the exact section names provided. If unsure, use "${fallback}".`;

  const resp = UrlFetchApp.fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${apiKey}`,
    {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] }),
      muteHttpExceptions: true,
    }
  );

  const raw = resp.getContentText();
  const body = JSON.parse(raw);
  if (!body.candidates || body.candidates.length === 0) {
    throw new Error('Gemini error: ' + raw);
  }

  const text = body.candidates[0].content.parts[0].text;
  const match = text.match(/\[[\s\S]*\]/);
  if (!match) throw new Error('Could not parse consolidation response from Gemini.');
  const consolidated = JSON.parse(match[0]);

  const ui = SpreadsheetApp.getUi();
  const confirm = ui.alert(
    'Consolidate Grocery List',
    `Replace ${tasks.length} items with ${consolidated.length} consolidated items?`,
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;

  tasks.forEach(t => deleteTask_(t.id));

  const token = PROPS.getProperty('TODOIST_API_TOKEN');
  const projectId = PROPS.getProperty('TODOIST_PROJECT_ID');
  const fallbackId = sections[sectionNames[0]];

  consolidated.forEach(({ content, section }) => {
    const sectionId = sections[section] || fallbackId;
    UrlFetchApp.fetch('https://api.todoist.com/api/v1/tasks', {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: `Bearer ${token}` },
      payload: JSON.stringify({
        content,
        project_id: projectId,
        section_id: sectionId,
        labels: [TODOIST_LABEL],
      }),
      muteHttpExceptions: true,
    });
  });

  SpreadsheetApp.getActiveSpreadsheet().toast(
    `Consolidated ${tasks.length} items into ${consolidated.length}.`, 'Done', 5
  );
}

// =============================================================================
// Sort list (alphabetical within sections, no LLM)
// =============================================================================

function sortList_() {
  const tasks = fetchLabeledTasks_();

  if (tasks.length === 0) {
    SpreadsheetApp.getActiveSpreadsheet().toast('Grocery list is empty — nothing to sort.', 'Grocery', 4);
    return;
  }

  // Group by section_id, sort each group alphabetically
  const bySectionId = {};
  tasks.forEach(t => {
    if (!bySectionId[t.section_id]) bySectionId[t.section_id] = [];
    bySectionId[t.section_id].push(t);
  });
  Object.values(bySectionId).forEach(group => {
    group.sort((a, b) => a.content.localeCompare(b.content));
  });

  // Delete all, recreate in sorted order (Todoist preserves creation order within a section)
  tasks.forEach(t => deleteTask_(t.id));

  const token = PROPS.getProperty('TODOIST_API_TOKEN');
  const projectId = PROPS.getProperty('TODOIST_PROJECT_ID');

  Object.entries(bySectionId).forEach(([sectionId, group]) => {
    group.forEach(({ content }) => {
      UrlFetchApp.fetch('https://api.todoist.com/api/v1/tasks', {
        method: 'post',
        contentType: 'application/json',
        headers: { Authorization: `Bearer ${token}` },
        payload: JSON.stringify({
          content,
          project_id: projectId,
          section_id: sectionId,
          labels: [TODOIST_LABEL],
        }),
        muteHttpExceptions: true,
      });
    });
  });

  SpreadsheetApp.getActiveSpreadsheet().toast(`Sorted ${tasks.length} items alphabetically.`, 'Done', 4);
}

// =============================================================================
// Clear list
// =============================================================================

function clearList_() {
  const tasks = fetchLabeledTasks_();

  if (tasks.length === 0) {
    SpreadsheetApp.getActiveSpreadsheet().toast('Grocery list is already empty.', 'Grocery', 4);
    return;
  }

  const ui = SpreadsheetApp.getUi();
  const confirm = ui.alert(
    'Clear Grocery List',
    `Delete all ${tasks.length} items from your grocery list?`,
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;

  tasks.forEach(t => deleteTask_(t.id));
  SpreadsheetApp.getActiveSpreadsheet().toast(`Cleared ${tasks.length} items.`, 'Done', 4);
}

// =============================================================================
// Photo capture / recipe parser
// =============================================================================

function openPhotoCapture_() {
  const template = HtmlService.createTemplateFromFile('PhotoCapture');
  template.sheetNamesJson = JSON.stringify(getSheetNames_());
  SpreadsheetApp.getUi().showModalDialog(
    template.evaluate().setWidth(420).setHeight(360),
    'Add Recipe from Photo'
  );
}

/**
 * Called from PhotoCapture.html after the user selects a photo.
 * @param {string} base64Data  Base64-encoded image data (no data: prefix)
 * @param {string} mimeType    e.g. "image/jpeg"
 * @param {string} sheetName   Target sheet tab name
 * @returns {string}           Recipe name that was added
 */
function parseRecipeImage(base64Data, mimeType, sheetName) {
  const apiKey = PROPS.getProperty('GEMINI_API_KEY');

  const prompt =
    'Extract the recipe from this image. Return a JSON object with:\n' +
    '- "name": the recipe name (string)\n' +
    '- "ingredients": array of strings, each formatted as "quantity unit ingredient"\n' +
    '  e.g. ["2 cups flour", "1 lb chicken thighs", "3 cloves garlic"]\n\n' +
    'Return only the JSON object, no other text.';

  const resp = UrlFetchApp.fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${apiKey}`,
    {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({
        contents: [{
          parts: [
            { inlineData: { mimeType: mimeType, data: base64Data } },
            { text: prompt },
          ],
        }],
      }),
      muteHttpExceptions: true,
    }
  );

  const body = JSON.parse(resp.getContentText());
  const text = body.candidates[0].content.parts[0].text;

  const match = text.match(/\{[\s\S]*\}/);
  if (!match) throw new Error('Could not parse recipe from image. Try a clearer photo.');
  const recipe = JSON.parse(match[0]);

  const sheet = getSheet_(sheetName);
  const nextCol = sheet.getLastColumn() + 1;
  sheet.getRange(1, nextCol).setValue(recipe.name);
  recipe.ingredients.forEach((ingredient, idx) => {
    sheet.getRange(idx + 2, nextCol).setValue(ingredient);
  });

  return recipe.name;
}

// =============================================================================
// Todoist API helpers
// =============================================================================

/** Fetch all meal-planner-labeled tasks, handling cursor pagination. */
function fetchLabeledTasks_() {
  const token = PROPS.getProperty('TODOIST_API_TOKEN');
  const projectId = PROPS.getProperty('TODOIST_PROJECT_ID');
  const tasks = [];
  let cursor = null;

  do {
    let url = `https://api.todoist.com/api/v1/tasks?project_id=${encodeURIComponent(projectId)}&label=${encodeURIComponent(TODOIST_LABEL)}`;
    if (cursor) url += `&cursor=${encodeURIComponent(cursor)}`;

    const resp = UrlFetchApp.fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      muteHttpExceptions: true,
    });
    const data = JSON.parse(resp.getContentText());
    (data.results || []).forEach(t => tasks.push(t));
    cursor = data.next_cursor || null;
  } while (cursor);

  return tasks;
}

function deleteTask_(id) {
  const token = PROPS.getProperty('TODOIST_API_TOKEN');
  UrlFetchApp.fetch(`https://api.todoist.com/api/v1/tasks/${id}`, {
    method: 'delete',
    headers: { Authorization: `Bearer ${token}` },
    muteHttpExceptions: true,
  });
}

// =============================================================================
// Sheet helpers
// =============================================================================

function getSheet_(sheetName) {
  const name = sheetName || PROPS.getProperty('SHEET_NAME') || 'Sheet1';
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
  if (!sheet) throw new Error(`Sheet "${name}" not found.`);
  return sheet;
}

/** Returns all recipe sheet tab names (excludes the readme sheet). */
function getSheetNames_() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheets()
    .map(s => s.getName())
    .filter(n => n.toLowerCase() !== README_SHEET_NAME);
}

/**
 * Returns all recipes from all sheets, preserving sheet order and column order.
 * Each entry: {name, colIndex, sheetName}
 */
function getAllRecipes_() {
  const all = [];
  getSheetNames_().forEach(sheetName => {
    getRecipeNames_(sheetName).forEach(r => all.push(Object.assign({}, r, { sheetName })));
  });
  return all;
}

function getRecipeNames_(sheetName) {
  const sheet = getSheet_(sheetName);
  const lastCol = sheet.getLastColumn();
  if (lastCol === 0) return [];

  const row1 = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  const results = [];
  row1.forEach((name, idx) => {
    if (name) results.push({ name: String(name), colIndex: idx });
  });
  return results;
}

// =============================================================================
// Readme sheet
// =============================================================================

/**
 * Creates (or overwrites) a "readme" sheet tab with usage instructions.
 * Run once from the Grocery menu after setup.
 */
function createReadmeSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(README_SHEET_NAME);
  if (sheet) {
    sheet.clear();
  } else {
    sheet = ss.insertSheet(README_SHEET_NAME);
    // Move to first position
    ss.setActiveSheet(sheet);
    ss.moveActiveSheet(1);
  }

  const GITHUB_URL = 'https://github.com/ianereed/Home-Tools/tree/main/meal-planner';
  const rows = [
    // [text, fontSize, bold, italic, hexColor]
    ['Meal Planner', 20, true, false, '#1a73e8'],
    ['', 11, false, false, null],
    ['A Google Sheets + Todoist grocery automation tool.', 12, false, true, '#5f6368'],
    ['Source code and setup guide: ' + GITHUB_URL, 11, false, false, '#1a73e8'],
    ['', 11, false, false, null],
    ['HOW IT WORKS', 11, true, false, '#202124'],
    ['Each sheet tab holds up to 20 recipes (one recipe per column).', 11, false, false, null],
    ['Row 1 = recipe name. Rows 2+ = ingredients ("2 cups flour", "1 lb chicken").', 11, false, false, null],
    ['', 11, false, false, null],
    ['GROCERY MENU', 11, true, false, '#202124'],
    ['Build Grocery List   - Pick recipes from any sheet; ingredients are pushed to Todoist.', 11, false, false, null],
    ['Add Recipe from Photo - Take or upload a photo; Gemini extracts the recipe automatically.', 11, false, false, null],
    ['Consolidate List     - Merge duplicate ingredients and sum quantities via Gemini.', 11, false, false, null],
    ['Sort List            - Alphabetize items within each Todoist section.', 11, false, false, null],
    ['Clear List           - Delete all meal-planner tasks from Todoist.', 11, false, false, null],
    ['', 11, false, false, null],
    ['BULK IMPORT (terminal)', 11, true, false, '#202124'],
    ['python bulk_import.py ~/photos/ --sheet "Asian" --yes', 11, false, false, null],
    ['Processes a folder of recipe photos via Gemini and writes them to the named sheet.', 11, false, false, null],
    ['', 11, false, false, null],
    ['TIPS', 11, true, false, '#202124'],
    ['Add new sheet tabs anytime — the tool discovers them automatically.', 11, false, false, null],
    ['Renaming tabs is safe; names are never cached.', 11, false, false, null],
    ['This sheet is excluded from all recipe lists and dropdowns.', 11, false, false, null],
  ];

  rows.forEach((row, i) => {
    const [text, fontSize, bold, italic, color] = row;
    const cell = sheet.getRange(i + 1, 1);
    cell.setValue(text);
    cell.setFontSize(fontSize);
    cell.setFontWeight(bold ? 'bold' : 'normal');
    cell.setFontStyle(italic ? 'italic' : 'normal');
    if (color) cell.setFontColor(color);
  });

  // Widen column A so text isn't clipped
  sheet.setColumnWidth(1, 680);

  // Freeze row 1 and set tab color
  sheet.setFrozenRows(1);
  sheet.setTabColor('#1a73e8');

  SpreadsheetApp.getActiveSpreadsheet().toast('Readme sheet created.', 'Done', 3);
}

// =============================================================================
// Bulk import web app (used by bulk_import.py)
// =============================================================================

/**
 * Health-check endpoint. Lets bulk_import.py verify the web app is live.
 * GET https://script.google.com/macros/s/<ID>/exec
 */
function doGet(e) {
  return _jsonResponse({ ok: true }, 200);
}

/**
 * Bulk recipe import endpoint. Called by bulk_import.py with a JSON body:
 *   { "recipes": [ { "name": "...", "ingredients": ["..."] }, ... ], "secret": "..." }
 *
 * Deploy via: Extensions > Apps Script > Deploy > New deployment > Web app
 *   Execute as: Me   |   Who has access: Anyone
 *
 * Optional: set BULK_IMPORT_SECRET in Script Properties to gate access.
 */
function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);

    // Optional shared-secret check
    const secret = PROPS.getProperty('BULK_IMPORT_SECRET');
    if (secret && body.secret !== secret) {
      return _jsonResponse({ error: 'Unauthorized' }, 401);
    }

    const recipes = body.recipes;
    if (!Array.isArray(recipes) || recipes.length === 0) {
      return _jsonResponse({ error: 'No recipes provided' }, 400);
    }

    const sheetName = body.sheet_name;
    if (!sheetName) {
      return _jsonResponse({ error: 'sheet_name is required' }, 400);
    }

    const result = bulkImportRecipes_(recipes, sheetName);
    return _jsonResponse(result, 200);

  } catch (err) {
    return _jsonResponse({ error: err.message }, 500);
  }
}

/**
 * Write an array of {name, ingredients[]} objects to the given sheet.
 * Skips recipes that already exist (by name, case-insensitive) or when the
 * 20-column limit is reached.
 *
 * @param {Array<{name: string, ingredients: string[]}>} recipes
 * @param {string} sheetName  Target sheet tab name
 * @returns {{written: number, skipped: number, errors: string[], current_col_count: number}}
 */
function bulkImportRecipes_(recipes, sheetName) {
  const MAX_COLS = 20;
  const sheet = getSheet_(sheetName);
  const errors = [];
  let written = 0;
  let skipped = 0;

  // Read existing names once; keep a local copy to catch within-batch duplicates
  const existingNames = getRecipeNames_(sheetName).map(r => r.name.toLowerCase());

  recipes.forEach(recipe => {
    // Validate shape
    if (!recipe.name || !Array.isArray(recipe.ingredients) || recipe.ingredients.length === 0) {
      errors.push('Invalid recipe shape: ' + JSON.stringify(recipe).substring(0, 80));
      skipped++;
      return;
    }

    // Duplicate name check (case-insensitive)
    if (existingNames.includes(recipe.name.toLowerCase())) {
      errors.push('Duplicate name skipped: "' + recipe.name + '"');
      skipped++;
      return;
    }

    // Column limit check
    const nextCol = sheet.getLastColumn() + 1;
    if (nextCol > MAX_COLS) {
      errors.push('Column limit (' + MAX_COLS + ') reached — "' + recipe.name + '" not written');
      skipped++;
      return;
    }

    // Write name + ingredients
    sheet.getRange(1, nextCol).setValue(recipe.name);
    recipe.ingredients.forEach((ingredient, idx) => {
      sheet.getRange(idx + 2, nextCol).setValue(ingredient);
    });

    // Update local cache so subsequent recipes in this batch see the new name
    existingNames.push(recipe.name.toLowerCase());
    written++;
  });

  return { written, skipped, errors, current_col_count: sheet.getLastColumn() };
}

/** Wraps a response object as JSON output. Note: Apps Script always returns HTTP 200;
 *  the `status` field in the body is what callers should check for errors. */
function _jsonResponse(data, statusCode) {
  const payload = Object.assign({ status: statusCode }, data);
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
