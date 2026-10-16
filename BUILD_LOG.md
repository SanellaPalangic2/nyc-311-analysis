# Build log: demand-forecasting module

What broke while building `forecast/`, why, and how it was fixed. Also records
data surprises that changed the plan. Newest entries at the bottom of each phase.

## Phase 1: Data (2026-10-15)

**1. `Timestamp + Timedelta` raises a DeprecationWarning.**
- Symptom: `pd.Timestamp("2025-03-09") + pd.Timedelta(days=1)` warns "The 'generic' unit
  for NumPy timedelta is deprecated, and will raise an error in the future".
- Cause: the local `.venv` has pandas 2.3.3 with numpy 2.5.4. `requirements.txt` pins
  pandas 3.0.6, so the venv doesn't match the pinned versions. pandas 2.3 was released
  before numpy 2.5 and trips over its new deprecation.
- Fix: use `pd.DateOffset(days=1)` instead (no warning, same result). The venv was not
  changed. Reinstalling from `requirements.txt` would remove the mismatch.

**2. DST bug in the expected-hours check (caught in review, before it ran).**
- Symptom: the first version added 24 real hours to local midnight. On the autumn DST
  day (2025-11-02, a 25-hour day) that ends at 23:00, so it would have reported a
  complete day as missing an hour.
- Fix: build the range up to the *next local midnight* instead. Tests cover
  2025-03-09 (23 clock hours), 2025-11-02 (24 distinct clock hours) and a normal day.

**3. `AttributeError: 'datetime.date' object has no attribute 'date'`.**
- Cause: the `holidays` package already returns `datetime.date` keys.
- Fix: use the keys as they are.

**4. `TypeError: keys must be str ... not datetime.date` when saving JSON.**
- Cause: `json.dumps` accepts dates as values (via `default=`) but never as dict keys.
- Fix: dates used as keys are converted with `str()` where the dicts are built.

**5. Phase 1 script took 68 s.**
- Cause: finding the complaint type behind each outlier day recomputed the
  same-weekday baseline for about 200 complaint types, once per outlier day.
- Fix: `same_weekday_baseline` now works on a whole table at once (numpy
  `nanmedian` over shifted copies). That step became instant; the remaining time is
  the SQL queries over 3.6M rows.

**6. Data surprise: one location produces most of the spikes.**
- Finding: 17 of the 29 outlier days in the total series coincide with bursts from a
  single location in ZIP 10466 (Noise - Residential, essentially all via the mobile
  app, up to several thousand a day). Together, hotspot location-days make up 3.4% of
  the year's requests and 7.2% of NYPD's.
- Decision: keep them in the main series (they took a normal time to close, so
  they were handled as real work). Add `sql/forecast/04_daily_hotspots.sql` with a
  same-day rule (more than 100 requests of one type from one location in one day) so
  later phases can also report accuracy without them. The rule uses only that day's
  data, so it adds no look-ahead. Results barely depend on the threshold: 50, 100 and
  200 catch 129k, 124k and 122k requests.

**7. Chart labels collided.**
- The first daily chart labelled 6 single days, and the January ones overlapped.
- Fix: group consecutive outlier days into episodes, label the 5 strongest at
  their peak, and stack labels that are within 30 days of each other.

**8. `forecast/data.py` renamed to `forecast/daily_series.py`.**
- Cause: `data` is also the name of the project's `data/` folder. Imports worked,
  but only because of how Python resolves namespace packages, which is fragile and
  confusing to read.

**9. Report text contained hand-typed numbers, one of them wrong.**
- The first draft said "two holidays were above baseline"; the data says three. It
  also typed in the hotspot location's counts.
- Fix: every number in the text now comes from the JSON. The hotspot facts come
  from a new query, `sql/forecast/05_hotspot_profile.sql`.

## Phase 2: Baselines (2026-10-15)

**1. Design decision: separate validation and test cutoffs.**
- Phase 3 will tune settings, and tuning on the same windows we report would overstate accuracy.
- So `forecast/backtest.py` defines 9 validation cutoffs (Aug 13 to Oct 8) and 9 test cutoffs
  (Oct 22 to Dec 17). No day is forecast in both sets; a test in `tests/test_forecast_baselines.py`
  checks this.

**2. `AttributeError` waiting to happen: `fc.PROCESSED_DIR` (caught before running).**
- `baselines.py` referenced `fc.PROCESSED_DIR`, which lives in `config`, not `fc_common`.
- Fix: save next to the daily table (`fc.DAILY_FILE.parent`).

**3. The new tests hit Phase 1 problem #1 again (21 DeprecationWarnings).**
- The tests used `pd.Timedelta(days=...)` for date arithmetic. Same pandas 2.3 + numpy 2.5 issue.
- Fix: `pd.DateOffset` in the tests too. The suite now runs with no warnings.

**4. `SyntaxError: unterminated triple-quoted f-string` in `report.py`.**
- Cause: the scripted edit that inserted the Phase 2 section dropped the closing `"""`.
- Fix: closed the string by hand and re-rendered.

**5. A chart subtitle claimed something the data doesn't show.**
- The error-by-horizon subtitle said naive is "badly off whenever the weekday changes". For the
  citywide total that's not true: its weekly pattern is weak (weekends about 7% below the week's
  average), and naive beats seasonal naive on test.
- Fix: the subtitle now says each point averages only 9 forecasts, which is the honest caveat.

## Phase 3: Models (2026-10-15)

**1. New dependency: `statsmodels==0.15.0`** (Holt-Winters), pinned in `requirements.txt`. Gradient
boosting uses scikit-learn, which was already a dependency, so no LightGBM/XGBoost was needed.

**2. The pandas 2.3 + numpy 2.5 warning, third form: column + `DateOffset`.**
- `past["cutoff"] + pd.DateOffset(days=14)` (a whole column) raised the same DeprecationWarning, which
  the run promotes to an error (`-W error::DeprecationWarning`). The 3-minute run crashed at the
  interval step.
- Fix: `np.timedelta64(14, "D")`, a timedelta with an explicit unit.

**3. Design decision: month is numeric, not categorical, in gradient boosting.**
- Test cutoffs are Oct-Dec, so training never contains a November or December. As a category, an
  unseen month is treated as "missing"; as a number it at least maps to the nearest month seen.
  Neither can learn yearly seasonality (see Phase 1).

**4. Result that contradicts the validation choice (reported, not "fixed").**
- Gradient boosting won on validation (MAE 478 vs 527 for the 4-week average), but on test the
  4-week average wins (1,533 vs 1,765 for gradient boosting). The 80% intervals covered 88% of
  validation days but only 40.5% of test days.
- Nothing was re-tuned or swapped after seeing the test results. Instead an error breakdown was added
  (`error_sources` in `evaluate_models.py`): outlier days make up about 40% of every model's test
  error, and single-lag models do much worse when a lag day was an event (for example, the
  Dec 15 snow spike copied into Dec 22 and Dec 29).

**5. Report text errors caught in review.**
- The tuning table called every grid row "(tuned)"; now only the comparison table does.
- The SARIMA paragraph said "160-350 days per fit"; the real range is 64 (first calibration cutoff)
  to 351 (last test cutoff).

## Phase 4: Per-agency forecasts (2026-10-15)

**1. Phase 3 settings were only saved as text.**
- `phase3_models.json` had descriptions like "relative target, depth 4, learning rate 0.1" but not the
  parameters, so Phase 4 couldn't rebuild the models without parsing text.
- Fix: `evaluate_models.py` now also saves `chosen_params`. Rerunning Phase 3 reproduced every number
  exactly (test interval coverage 40.476% both times), which confirms the fixed seeds work.

**2. Phase 4 took 14 minutes.**
- Profiling showed the time is in scikit-learn's gradient-boosting fit itself (about 2 s per fit with
  300 trees), times 37 weekly cutoffs times 7 agency series. It isn't a bug in our code (feature building
  is a small fraction).
- Not changed: early stopping or fewer trees would change the model that Phase 3 chose. Instead, charts
  can be redrawn from the saved JSON without refitting (done once for the staffing chart).

**3. `KeyError: 'excl_hotspots/DOT'` in the report.**
- Cause: the per-agency explanations were a dict of f-strings, so all five were built for every agency,
  including NYPD's "without bursts" number for DOT, which has no such variant.
- Fix: the explanations are lambdas, so only the requested agency's text is built.

**4. Report text that overstated the data (caught in review).**
- "The 4-week average is the best or close to it for most agencies": it was best on test for 2 of 5.
- "Validation favoured models that track recent days closely": true for HPD and DSNY (gradient boosting
  chosen), not for DOT and NYPD (4-week average chosen).
- "HPD and DOT need about 40% fewer staff at weekends": the data shows 40% fewer *requests*; staff
  needs depend on productivity data we don't have.
- "DSNY is hardest because a single weather event accounts for most of its error": now computed:
  event days plus copied events account for 67.6% of its error, led by the December snow.
- Each sentence is now built from the numbers, and the staffing chart's subtitle computes its ranges
  instead of saying "about half".

**5. New query for explanations: `sql/forecast/06_agency_test_drivers.sql`.**
- Gives each agency's largest complaint type in the test period and on its busiest test day, so claims
  like "DSNY's spike was snow" (3,296 Snow or Ice requests on 2025-12-15) come from data.
- The DEP/DOT spike on 2025-10-30 (Sewer and Traffic Signal complaints) looks like heavy rain, but no
  weather data is used, so the report says so instead of asserting it.

## Phase 5: Communication (2026-10-15)

**1. The project venv can't run the dashboard or its tests.**
- `.venv` has no `streamlit` (the app's packages live in `app/requirements.txt`, which also pins
  pandas 3.0.6 vs the venv's 2.3.3; see Phase 1 #1). The 21 existing app tests couldn't run there.
- Fix: a separate, throwaway venv built from `app/requirements.txt` (the same packages Render
  installs), outside the repo. All 21 existing app tests passed before any change, and all 36 (21 + 15
  new Forecast-page tests) after.

**2. No browser available to look at the new page.**
- Fix: charts were rendered to PNG by running the page through Streamlit's `AppTest` and saving each
  Altair chart with `vl-convert` (installed only in the throwaway venv), then checked by eye. The real
  server was also started locally: `/_stcore/health` returned `ok`, the page returned HTTP 200, and the
  log had no errors.

**3. `forecast/summary.py` would have shadowed `analysis/summary.py`.**
- Both folders are on the import path (`pytest.ini`, `fc_common.py`), so `import summary` would be
  ambiguous. Renamed to `forecast/write_summary.py` before anything imported it.

**4. Wording in the generated text was checked against the numbers again.**
- A resume bullet first said the burst source explained "most volume spikes". Replaced with the exact,
  computed fact: removing it cuts outlier days from 29 to 11.
- The page's model-comparison caption first named the test winner in fixed text. It now computes the
  winner for whichever data version is selected.

**5. README edited in two ways.**
- The "Demand Forecasting" section is generated between marker comments by `forecast/write_summary.py`,
  the same pattern as `analysis/summary.py`, so its numbers can't drift from the results.
- Hand edits elsewhere: intro, stack, dashboard page list, reproduce steps and project tree. Checked
  with a diff (82 lines added, 6 changed) and the file's CRLF line endings were kept.
