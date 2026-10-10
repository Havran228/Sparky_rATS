-- Tax-year parameters are data (ADR-007). The payroll engine reads this table.
-- 2026 figures: Finančná správa information for 2026 and the ADR-007 levy rates.
-- 2025 figures: Finančná správa NČZD for 2025, two tax brackets, employee health 4%.

ALTER TABLE projects ADD COLUMN model_name TEXT;
ALTER TABLE projects ADD COLUMN prompt_version TEXT;

INSERT INTO tax_params (tax_year, params_json, updated_at) VALUES (
    2025,
    '{"employee_social":[{"code":"sickness","rate":"0.014"},{"code":"old_age","rate":"0.040"},{"code":"disability","rate":"0.030"},{"code":"unemployment","rate":"0.010"}],"employee_social_cap":"15730","employee_health_rate":"0.04","employer_social":[{"code":"sickness","rate":"0.014"},{"code":"old_age","rate":"0.140"},{"code":"disability","rate":"0.030"},{"code":"unemployment","rate":"0.010"},{"code":"accident","rate":"0.008"},{"code":"guarantee","rate":"0.0025"},{"code":"reserve_fund","rate":"0.0475"}],"employer_social_cap":"15730","employer_health_rate":"0.11","nczd_monthly":"479.48","nczd_full_until_annual":"25426.27","nczd_phaseout_constant":"12110.36","nczd_phaseout_divisor":"4","brackets":[{"up_to":"4036.79","rate":"0.19"},{"up_to":null,"rate":"0.25"}]}',
    '2026-01-01T00:00:00Z'
);

INSERT INTO tax_params (tax_year, params_json, updated_at) VALUES (
    2026,
    '{"employee_social":[{"code":"sickness","rate":"0.014"},{"code":"old_age","rate":"0.040"},{"code":"disability","rate":"0.030"},{"code":"unemployment","rate":"0.010"}],"employee_social_cap":"16764","employee_health_rate":"0.05","employer_social":[{"code":"sickness","rate":"0.014"},{"code":"old_age","rate":"0.140"},{"code":"disability","rate":"0.030"},{"code":"unemployment","rate":"0.010"},{"code":"accident","rate":"0.008"},{"code":"guarantee","rate":"0.0025"},{"code":"reserve_fund","rate":"0.0475"}],"employer_social_cap":"16764","employer_health_rate":"0.11","nczd_monthly":"497.23","nczd_full_until_annual":"26083.13","nczd_phaseout_constant":"14661.11","nczd_phaseout_divisor":"3","brackets":[{"up_to":"3665.28","rate":"0.19"},{"up_to":"5029.10","rate":"0.25"},{"up_to":"6250.86","rate":"0.30"},{"up_to":null,"rate":"0.35"}]}',
    '2026-01-01T00:00:00Z'
);
