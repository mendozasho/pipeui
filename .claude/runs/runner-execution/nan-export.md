# Addendum — Transformed-report export scrubs NaN/inf (#262)
Issue: #262   Branch: feature/runner-execution-nan
to-code addendum. Bug found in manual testing: append transforms on null-containing data
(customers) ran fine but the transformed-report export 500'd (`nan not JSON compliant`), so
no columns appeared. Root cause: get_staging_rows serialized NaN/inf straight to JSON.
Missed because slice-5 export tests used null-free synthetic data.

## Fix (red-green)
- `get_staging_rows` scrubs NaN/NaT/None/inf -> JSON null via new `_json_safe` helper.
- Test `test_api_staging.py::test_staging_export_scrubs_nan_and_inf_to_null` uses null+inf data
  (red: 500 nan not JSON compliant; green: 200, nulls render as JSON null, real values kept).

## Verification
Full suite 440 passed (1 pre-existing macOS setrlimit). Live: customers transform export returns 200 with appended columns.
