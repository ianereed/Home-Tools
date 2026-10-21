"""HTML fragments for the Fuel page (dark dashboard theme). Pure functions of the plan payload --
every narrative string comes from payload notes/flags, never from text written here about a person."""
from __future__ import annotations

import html
from datetime import date

from . import foods as F

CSS = """
<style>
.fuel-card{background:#161a23;border:1px solid rgba(255,255,255,.07);border-radius:10px;padding:10px 12px;margin:6px 0}
.fuel-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
@media (max-width:760px){.fuel-grid{grid-template-columns:1fr}}
.fuel-slot{background:#12151d;border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:8px 10px}
.fuel-head{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.fuel-time{color:#8b93a7;font-variant-numeric:tabular-nums;font-size:.8rem}
.fuel-role{color:#6ea8fe;font-size:.75rem}
.fuel-name{font-weight:600;margin:3px 0 1px;color:#e6e9ef}
.fuel-tgt{color:#8b93a7;font-size:.75rem}
.fuel-ing{list-style:none;padding:0;margin:5px 0;font-size:.8rem;color:#cdd3e0}
.fuel-ing li{display:flex;justify-content:space-between;gap:8px;border-bottom:1px dotted rgba(255,255,255,.07);padding:1px 0;flex-wrap:wrap}
.fuel-ing .q{font-variant-numeric:tabular-nums;white-space:nowrap;color:#e6e9ef}
.fuel-ing small{color:#8b93a7}
.fuel-method{color:#aab2c5;font-size:.75rem;margin:4px 0 0}
.fuel-built{color:#8b93a7;font-size:.72rem;border-top:1px dashed rgba(255,255,255,.08);padding-top:4px;margin-top:4px}
.fuel-badge{font-size:.68rem;background:rgba(255,255,255,.07);padding:1px 6px;border-radius:999px;color:#aab2c5;font-weight:400}
.fuel-notes{margin:4px 0;padding-left:16px;font-size:.8rem;color:#aab2c5}
.fuel-flag{color:#fbbf24}
.fuel-chip{display:inline-block;font-size:.78rem;background:rgba(110,168,254,.12);color:#cdd3e0;border-radius:6px;padding:1px 7px;margin:0 4px 4px 0}
.fuel-chip.done{background:rgba(74,222,128,.14)}
.fuel-chip.lift{background:rgba(167,139,250,.14)}
.fuel-bar{height:8px;background:rgba(255,255,255,.07);border-radius:4px;overflow:hidden}
.fuel-bar>span{display:block;height:100%;background:#6ea8fe}
.fuel-bar.over>span{background:#fbbf24}
.fuel-row{display:grid;grid-template-columns:110px 1fr 120px;gap:8px;align-items:center;font-size:.8rem;color:#cdd3e0;margin:3px 0}
.fuel-row .v{text-align:right;font-variant-numeric:tabular-nums;color:#e6e9ef}
.fuel-groc{columns:3 220px;column-gap:18px}
.fuel-groc .sec{break-inside:avoid;margin-bottom:10px}
.fuel-groc h4{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:#8b93a7;margin:4px 0}
</style>
"""


def esc(x) -> str:
    return html.escape(str(x))


def g(x) -> str:
    return f"{x:,.0f}"


def unit_hint(name: str, grams: float) -> str:
    food = F.FOODS[name]
    unit, gpu = food.unit, food.g_per_unit
    n = grams / gpu
    if unit in ("large egg", "date", "tortilla", "slice", "medium", "scoop", "can (drained)", "pita"):
        q = round(n * 2) / 2
        return f"~{q:g} {unit}{'s' if q > 1 and unit != 'medium' else ''}" if q else ""
    if unit == "block (14 oz)":
        return f"~{n:.2f} block"
    q = round(n * 4) / 4
    if not q:
        return ""
    frac = {0.25: "¼", 0.5: "½", 0.75: "¾"}
    whole, part = int(q), q - int(q)
    return f"~{(str(whole) if whole else '') + frac.get(part, '') or '0'} {unit}"


def _hm(m) -> str:
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def session_chip(s: dict) -> str:
    hm = _hm
    t = f"{hm(s['start'])}–{hm(s['end'])}" if s.get("start") is not None else "time TBD"
    if s.get("completed"):
        return (f'<span class="fuel-chip done">✓ {esc(s["name"])} · {round(s["minutes"])} min · '
                f'{g(s.get("active_kcal", 0))} active kcal</span>')
    if s["kind"] == "strength":
        return f'<span class="fuel-chip lift">{esc(s.get("name", "Strength"))} · {t}</span>'
    tag = {"projected": " · projected", "generated": " · planner ride"}.get(s.get("source", ""), "")
    return f'<span class="fuel-chip">{esc(s["name"])} · {round(s["minutes"])} min · {t}{tag}</span>'


def ingredient_rows(ing: dict, sides: list) -> str:
    rows = []
    for name, grams in ing.items():
        side = ' <span class="fuel-badge">side</span>' if name in sides else ""
        rows.append(f"<li><span>{esc(name)}{side}</span><span class='q'>{g(grams)} g <small>{esc(unit_hint(name, grams))}</small></span></li>")
    return "<ul class='fuel-ing'>" + "".join(rows) + "</ul>"


def slot_html(sl: dict) -> str:
    t = sl["target"]
    role = f'<span class="fuel-role">{esc(sl["role"])}</span>' if sl.get("role") else ""
    head = f'<div class="fuel-head"><span class="fuel-time">{esc(sl["time"])}</span><b>{esc(sl["name"])}</b>{role}</div>'
    tgt = f'<div class="fuel-tgt">target P {t["p"]:.0f} · C {t["c"]:.0f} · F {t["f"]:.0f} g</div>'
    if sl["mode"] == "out":
        body = ("<p class='fuel-method'>Eating out: order a protein-forward entrée, about "
                f"{t['c']:.0f} g carbs (≈{t['c'] / 45:.1f} cups rice/pasta/potato) and vegetables, sauces on the side. "
                "Restaurant food is salty; keep the rest of the day low.</p>")
        return f'<div class="fuel-slot">{head}{tgt}{body}</div>'
    if sl["mode"] == "trip" or "meal" not in sl:
        return f'<div class="fuel-slot">{head}{tgt}</div>'
    m = sl["meal"]
    n = m["nutrients"]
    adj = f' <span class="fuel-badge">{esc(m["adjusted"])}</span>' if m.get("adjusted") else ""
    built = (f'<div class="fuel-built">{g(n["kcal"])} kcal · P {n["p"]:.0f} · C {n["c"]:.0f} · F {n["f"]:.0f} · '
             f'fiber {n["fiber"]:.0f} · sat {n["sat"]:.0f} · Na {g(n["na"])} mg · chol {g(n.get("chol", 0))} mg</div>')
    return (f'<div class="fuel-slot">{head}<div class="fuel-name">{esc(m["name"])}{adj}</div>{tgt}'
            f'{ingredient_rows(m["ingredients"], m.get("sides", []))}<p class="fuel-method">{esc(m["method"])}</p>{built}</div>')


def meals_html(meals: dict) -> str:
    parts = [slot_html(sl) for sl in meals["slots"]]
    rf = meals.get("ride_fuel")
    if rf:
        parts.append(f'<div class="fuel-slot"><div class="fuel-head"><span class="fuel-time">on bike</span><b>Ride fuel</b>'
                     f'<span class="fuel-role">{rf["gph"]} g carbs/h</span></div><div class="fuel-tgt">{rf["carb_g"]:.0f} g carbs: '
                     f'~{rf["ingredients"].get("sports drink mix", 0)} g drink mix, or dates / bananas</div></div>')
    return '<div class="fuel-grid">' + "".join(parts) + "</div>"


def notes_html(day: dict, energy: dict, meals: dict | None) -> str:
    items = [f"<li>{esc(n['text'])}</li>" for n in day.get("notes", [])]
    items += [f"<li>{esc(f)}</li>" for f in energy.get("flags", []) if not f.startswith("fat capped")]
    items += [f'<li class="fuel-flag">{esc(f)}</li>' for f in (meals or {}).get("flags", [])]
    return f'<ul class="fuel-notes">{"".join(items)}</ul>' if items else ""


def bar_row(label: str, value: float | None, target: float, unit: str, ceiling: bool = False) -> str:
    """value vs target; ceiling=True means the target is a limit (over = amber). target None = shown, no bar."""
    if target is None:
        return (f'<div class="fuel-row"><span>{esc(label)}</span><div class="fuel-bar"><span style="width:0"></span></div>'
                f'<span class="v">{"—" if value is None else g(value)} {unit}</span></div>')
    if value is None:
        return (f'<div class="fuel-row"><span>{esc(label)}</span><div class="fuel-bar"><span style="width:0"></span></div>'
                f'<span class="v">— / {g(target)} {unit}</span></div>')
    pct = 0 if not target else min(100, 100 * value / target)
    over = ceiling and value > target
    return (f'<div class="fuel-row"><span>{esc(label)}</span><div class="fuel-bar{" over" if over else ""}">'
            f'<span style="width:{pct:.0f}%"></span></div><span class="v">{g(value)} / {g(target)} {unit}</span></div>')


def grocery_html(agg: dict) -> str:
    sec_of = {name: sec for sec, names in F.GROCERY_SECTION.items() for name in names}
    by_sec: dict[str, list] = {}
    for name, grams in agg.items():
        by_sec.setdefault(sec_of.get(name, "Other"), []).append((name, grams))
    out = []
    for sec in list(F.GROCERY_SECTION) + ["Other"]:
        items = by_sec.get(sec)
        if not items:
            continue
        lis = []
        for name, grams in sorted(items):
            raw = grams * F.RAW_PER_COOKED.get(name, 1.0)
            if name in F.RAW_PER_COOKED:
                label = "dry" if F.RAW_PER_COOKED[name] < 1 else "raw"
                buy = f"{raw / 453.6:.1f} lb {label}" if raw >= 227 else f"{raw:.0f} g {label}"
            elif F.FOODS[name].unit in ("medium", "large egg", "date", "tortilla", "slice", "scoop", "can (drained)", "pita"):
                buy = unit_hint(name, grams).replace("~", "≈ ")
            else:
                buy = f"{grams / 453.6:.1f} lb" if grams >= 454 else f"{grams:.0f} g"
            hint = F.GROCERY_HINTS.get(name)
            lis.append(f"<li><span>{esc(name)}</span><span class='q'>{esc(buy)}</span>"
                       + (f"<small style='flex-basis:100%'>{esc(hint)}</small>" if hint else "") + "</li>")
        out.append(f"<div class='sec'><h4>{esc(sec)}</h4><ul class='fuel-ing'>{''.join(lis)}</ul></div>")
    return "<div class='fuel-groc'>" + "".join(out) + "</div>"


def fdate(ds: str, fmt: str = "%a %b %-d") -> str:
    return date.fromisoformat(ds).strftime(fmt)
