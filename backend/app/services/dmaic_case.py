from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
import zipfile
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

MAX_FILES = 150
MAX_UNCOMPRESSED_BYTES = 35 * 1024 * 1024
MAX_TEXT_CHARS = 120_000


def _safe_name(name: str) -> str:
    # Keep only the basename for direct uploads; never use user names as output paths.
    return PurePosixPath(str(name).replace('\\', '/')).name[:180] or 'uploaded-file'

def _display_name(name: str) -> str:
    # Preserve a short relative archive path to disambiguate same-named files.
    parts = [p for p in PurePosixPath(str(name).replace('\\', '/')).parts if p not in ('', '.', '..', '/') and not re.match(r'^[A-Za-z]:$', p)]
    return '/'.join(parts[-3:])[:220] or _safe_name(name)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')


def _parse_payloads(files: list[tuple[str, bytes]]) -> tuple[dict[str, pd.DataFrame], dict[str, str], dict[str, Any], list[dict]]:
    dataframes: dict[str, pd.DataFrame] = {}
    documents: dict[str, str] = {}
    structured: dict[str, Any] = {}
    inventory: list[dict] = []
    expanded: list[tuple[str, bytes]] = []
    total = 0
    for raw_name, raw in files:
        name = _safe_name(raw_name)
        if name.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    members = [m for m in zf.infolist() if not m.is_dir()]
                    if len(members) > MAX_FILES:
                        raise ValueError(f"ZIP '{name}' contains too many files (limit {MAX_FILES}).")
                    for member in members:
                        if member.file_size > MAX_UNCOMPRESSED_BYTES:
                            raise ValueError(f"ZIP member '{_safe_name(member.filename)}' exceeds the per-file extraction limit.")
                        total += member.file_size
                        if total > MAX_UNCOMPRESSED_BYTES:
                            raise ValueError('ZIP expanded content exceeds the 35 MB limit.')
                        # Ignore unsafe / irrelevant archive entries and keep only a display path.
                        expanded.append((_display_name(member.filename), zf.read(member)))
            except zipfile.BadZipFile as exc:
                raise ValueError(f"'{name}' has a .zip extension but is not a valid ZIP archive.") from exc
        else:
            total += len(raw)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError('Total uploaded content exceeds the 35 MB processing limit.')
            expanded.append((name, raw))
    if len(expanded) > MAX_FILES:
        raise ValueError(f'Too many files after archive expansion (limit {MAX_FILES}).')

    for raw_name, raw in expanded:
        name = _display_name(raw_name)
        lower = name.lower()
        record = {'filename': name, 'bytes': len(raw), 'kind': 'document', 'status': 'indexed'}
        try:
            if lower.endswith(('.csv', '.tsv')):
                text = _decode(raw)
                sep = '\t' if lower.endswith('.tsv') else ','
                if not lower.endswith('.tsv'):
                    try:
                        dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;|\t')
                        sep = dialect.delimiter
                    except csv.Error:
                        pass
                try:
                    frame = pd.read_csv(io.StringIO(text), sep=sep, low_memory=False)
                except Exception as exc:
                    record.update(status='error', error=f'Could not parse delimited data: {type(exc).__name__}')
                    inventory.append(record)
                    continue
                dataframes[name] = frame
                record.update(kind='dataset', rows=int(len(frame)), columns=[str(c) for c in frame.columns])
            elif lower.endswith(('.xlsx', '.xls')):
                xls = pd.ExcelFile(io.BytesIO(raw))
                for sheet in xls.sheet_names:
                    frame = pd.read_excel(xls, sheet_name=sheet)
                    key = f'{name}::{sheet}'
                    dataframes[key] = frame
                    record.setdefault('sheets', []).append({'name': sheet, 'rows': int(len(frame)), 'columns': [str(c) for c in frame.columns]})
                record.update(kind='workbook', rows=sum(len(dataframes[f'{name}::{s}']) for s in xls.sheet_names), sheets=xls.sheet_names)
            elif lower.endswith('.json'):
                obj = json.loads(_decode(raw))
                structured[name] = obj
                record['kind'] = 'structured_json'
                if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                    dataframes[name] = pd.DataFrame(obj)
                    record.update(kind='dataset', rows=len(dataframes[name]), columns=list(map(str, dataframes[name].columns)))
                elif isinstance(obj, dict):
                    # JSON APIs often wrap rows under data/records/items.
                    for key in ('data', 'records', 'items', 'results'):
                        value = obj.get(key)
                        if isinstance(value, list) and value and isinstance(value[0], dict):
                            dataframes[name] = pd.DataFrame(value)
                            record.update(kind='dataset', rows=len(dataframes[name]), columns=list(map(str, dataframes[name].columns)))
                            break
            elif lower.endswith('.pdf'):
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(raw))
                text = '\n'.join(page.extract_text() or '' for page in reader.pages)
                documents[name] = text[:MAX_TEXT_CHARS]
                record.update(pages=len(reader.pages), chars=len(documents[name]))
            elif lower.endswith('.docx'):
                from docx import Document
                doc = Document(io.BytesIO(raw))
                text = '\n'.join(p.text for p in doc.paragraphs)
                for table in doc.tables:
                    text += '\n' + '\n'.join(' | '.join(cell.text for cell in row.cells) for row in table.rows)
                documents[name] = text[:MAX_TEXT_CHARS]
                record['chars'] = len(documents[name])
            elif lower.endswith(('.txt', '.md', '.rst')):
                text = _decode(raw)[:MAX_TEXT_CHARS]
                documents[name] = text
                record['chars'] = len(text)
                if lower.endswith(('project_charter.json', 'manifest.json')):
                    try: structured[name] = json.loads(text)
                    except json.JSONDecodeError: pass
            else:
                record.update(status='skipped', error='Unsupported file type; supported: CSV, TSV, XLSX, XLS, JSON, TXT, MD, PDF, DOCX, ZIP.')
        except Exception as exc:
            record.update(status='error', error=f'{type(exc).__name__}: {str(exc)[:220]}')
        inventory.append(record)
    return dataframes, documents, structured, inventory


def _pick(dataframes: dict[str, pd.DataFrame], names: tuple[str, ...], columns: tuple[str, ...] = ()) -> tuple[str | None, pd.DataFrame | None]:
    for name, frame in dataframes.items():
        if any(token in name.lower() for token in names):
            return name, frame
    if columns:
        for name, frame in dataframes.items():
            lower = {str(c).lower() for c in frame.columns}
            if all(any(required in col for col in lower) for required in columns):
                return name, frame
    return None, None


def _column(frame: pd.DataFrame | None, *tokens: str) -> str | None:
    if frame is None: return None
    for col in frame.columns:
        lc = str(col).strip().lower()
        if any(token in lc for token in tokens): return str(col)
    return None


def _num(frame: pd.DataFrame, col: str | None) -> pd.Series:
    if col is None or col not in frame: return pd.Series(dtype=float)
    return pd.to_numeric(frame[col], errors='coerce').replace([np.inf, -np.inf], np.nan).dropna()


def _pvalue(value: Any) -> Any:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None

def _round(value: Any, digits: int = 6) -> Any:
    try:
        if value is None or not np.isfinite(float(value)): return None
        return round(float(value), digits)
    except (TypeError, ValueError): return None


def _proportion_ci(x: int, n: int, confidence: float = .95) -> list[float] | None:
    if n <= 0: return None
    z = float(stats.norm.ppf(1 - (1-confidence)/2))
    p = x/n
    denom = 1 + z*z/n
    center = (p + z*z/(2*n))/denom
    half = z*math.sqrt((p*(1-p)/n) + z*z/(4*n*n))/denom
    return [_round(max(0, center-half)), _round(min(1, center+half))]


def _flagged_rate(frame: pd.DataFrame, value_col: str, lsl: float | None, usl: float | None) -> dict:
    vals = pd.to_numeric(frame[value_col], errors='coerce')
    valid = vals.notna() & np.isfinite(vals)
    if lsl is None and usl is None:
        return {'status': 'not_calculated', 'reason': 'Specification limits not supplied.'}
    oos = pd.Series(False, index=frame.index)
    if lsl is not None: oos |= vals < lsl
    if usl is not None: oos |= vals > usl
    oos &= valid
    n = int(valid.sum()); count = int(oos.sum())
    return {'status': 'calculated', 'n': n, 'out_of_spec_count': count, 'defect_rate': _round(count/n) if n else None,
            'yield': _round(1-count/n) if n else None, 'yield_ci_95': _proportion_ci(n-count, n),
            'defect_ci_95': _proportion_ci(count, n), 'lsl': lsl, 'usl': usl}


def _baseline_analysis(name: str, frame: pd.DataFrame, lsl: float | None, usl: float | None) -> dict:
    value_col = _column(frame, 'diameter', 'measurement', 'measure', 'value', 'outcome', 'cycle_time', 'lead_time')
    if not value_col:
        numeric = frame.select_dtypes(include='number').columns.tolist()
        value_col = str(numeric[0]) if numeric else None
    if not value_col:
        return {'source': name, 'status': 'blocked', 'reason': 'No numeric outcome column identified.'}
    vals_raw = pd.to_numeric(frame[value_col], errors='coerce').replace([np.inf, -np.inf], np.nan)
    vals = vals_raw.dropna()
    local_lsl, local_usl = lsl, usl
    for col in frame.columns:
        if local_lsl is None and str(col).lower() in ('lsl', 'lsl_mm', 'lower_specification_limit'):
            series = _num(frame, str(col)); local_lsl = float(series.iloc[0]) if len(series) else None
        if local_usl is None and str(col).lower() in ('usl', 'usl_mm', 'upper_specification_limit'):
            series = _num(frame, str(col)); local_usl = float(series.iloc[0]) if len(series) else None
    spec = _flagged_rate(frame, value_col, local_lsl, local_usl)
    flag_col = _column(frame, 'defect', 'defective', 'reject', 'nonconform')
    flag_validation = {'status': 'not_available'}
    if flag_col and spec.get('status') == 'calculated':
        flags = pd.to_numeric(frame[flag_col], errors='coerce')
        valid_flags = flags.isin([0, 1]) & vals_raw.notna()
        calculated = pd.Series(False, index=frame.index)
        if local_lsl is not None: calculated |= vals_raw < local_lsl
        if local_usl is not None: calculated |= vals_raw > local_usl
        mismatch = valid_flags & (flags.astype('float').fillna(-1) != calculated.astype(int))
        mismatch_rows = frame.index[mismatch].tolist()
        flag_validation = {'status': 'passed' if not mismatch_rows else 'mismatch_found', 'flag_column': flag_col,
                           'valid_binary_flags': int(valid_flags.sum()), 'mismatch_count': len(mismatch_rows),
                           'mismatch_part_ids_or_rows': [str(frame.iloc[i][_column(frame, 'part_id', 'unit_id', 'sample_id')]) if _column(frame, 'part_id', 'unit_id', 'sample_id') else str(i+1) for i in mismatch_rows[:30]],
                           'note': 'Flags are checked against strict outside-specification rules (< LSL or > USL). Equality to a limit is conforming under this rule.'}
    n = int(len(vals)); mean = float(vals.mean()) if n else None; sd = float(vals.std(ddof=1)) if n > 1 else None
    defects = spec.get('out_of_spec_count') if spec.get('status') == 'calculated' else None
    sigma = None
    sigma_status = 'not_calculated'
    if defects is not None and n:
        p = defects/n
        if 0 < p < 1:
            sigma = _round(float(stats.norm.ppf(1-p) + 1.5))
            sigma_status = 'calculated_with_1_5_shift_convention'
        elif p == 0:
            sigma_status = 'undefined_zero_observed_defects_does_not_mean_zero_future_risk'
        else:
            sigma_status = 'undefined_all_observed_units_defective'

    normality = {'status': 'not_calculated'}
    if 3 <= n <= 5000 and vals.nunique() > 1:
        sw = stats.shapiro(vals.to_numpy())
        normality = {'status': 'calculated', 'method': 'Shapiro-Wilk', 'p_value': _pvalue(sw.pvalue), 'interpretation': 'Evidence against normality at alpha 0.05; investigate shape and method suitability.' if sw.pvalue < .05 else 'No significant evidence against normality at alpha 0.05; this does not prove normality.'}
    result = {'source': name, 'status': 'calculated', 'outcome_column': value_col, 'n_rows': int(len(frame)), 'n_valid': n,
              'invalid_or_missing_measurements': int(len(frame)-n), 'mean': _round(mean), 'median': _round(vals.median()) if n else None,
              'sample_std_dev': _round(sd), 'min': _round(vals.min()) if n else None, 'max': _round(vals.max()) if n else None,
              'q1': _round(vals.quantile(.25)) if n else None, 'q3': _round(vals.quantile(.75)) if n else None,
              'specification': spec, 'defect_flag_validation': flag_validation, 'normality_diagnostic': normality,
              'sigma_benchmark_with_1_5_shift': sigma, 'sigma_benchmark_status': sigma_status,
              'dpmo_one_opportunity_per_unit': _round(defects/n*1_000_000) if defects is not None and n else None,
              'dpmo_assumption': 'One CTQ opportunity per unit assumed for the supplied single-characteristic case; change this if the operational defect opportunity definition differs.',
              'sigma_convention': 'Yield-based normal benchmark + 1.5 sigma shift. This is a convention-based benchmark, not an observed physical sigma level.'}
    # Cp/Cpk and Pp/Ppk are shown as provisional estimates only; capability blocked when fewer than 2 observations or no two limits.
    if n > 1 and sd and sd > 0 and local_lsl is not None and local_usl is not None:
        within_sd = None
        if n >= 2:
            arr = vals.to_numpy(dtype=float)
            mrbar = float(np.abs(np.diff(arr)).mean()) if len(arr) > 1 else None
            within_sd = mrbar / 1.128 if mrbar and mrbar > 0 else None
        result['capability'] = {
            'pp': _round((local_usl-local_lsl)/(6*sd)),
            'ppk': _round(min(local_usl-mean, mean-local_lsl)/(3*sd)),
            'cp': _round((local_usl-local_lsl)/(6*within_sd)) if within_sd else None,
            'cpk': _round(min(local_usl-mean, mean-local_lsl)/(3*within_sd)) if within_sd else None,
            'within_sigma_estimator': 'Average moving range of 2 divided by d2=1.128; assumes row order represents process order.',
            'status': 'provisional_only', 'limitations': ['Do not interpret capability as predictive until stability, measurement system, sampling, and distribution assumptions are acceptable.']}
    else:
        result['capability'] = {'status': 'not_calculated', 'reason': 'Need at least two valid measurements, nonzero variation, and both specification limits.'}
    return result


def _group_rates(frame: pd.DataFrame, group_col: str, defect_col: str, min_n: int = 1) -> list[dict]:
    defects = pd.to_numeric(frame[defect_col], errors='coerce')
    rows = []
    for group, idx in frame.groupby(group_col, dropna=False).groups.items():
        f = defects.loc[idx]
        f = f[f.isin([0, 1])]
        n = len(f); x = int(f.sum()) if n else 0
        if n >= min_n:
            rows.append({'group': 'Missing' if pd.isna(group) else str(group), 'n': int(n), 'defects': x, 'defect_rate': _round(x/n) if n else None, 'rate_ci_95': _proportion_ci(x, n)})
    return sorted(rows, key=lambda r: (r['defect_rate'] if r['defect_rate'] is not None else -1), reverse=True)


def _association_tests(frame: pd.DataFrame) -> list[dict]:
    defect_col = _column(frame, 'defect', 'defective', 'reject', 'nonconform')
    if not defect_col: return []
    defect = pd.to_numeric(frame[defect_col], errors='coerce')
    valid_def = defect.isin([0,1])
    findings = []
    categorical_tokens = ('machine', 'shift', 'operator', 'batch', 'setup_check', 'offset', 'tool_age_group', 'status')
    for col in frame.columns:
        if str(col) == defect_col: continue
        if not any(t in str(col).lower() for t in categorical_tokens): continue
        work = pd.DataFrame({'group': frame[col].astype('string').fillna('Missing'), 'defect': defect}).loc[valid_def]
        table = pd.crosstab(work['group'], work['defect']).reindex(columns=[0,1], fill_value=0)
        if len(table) < 2 or table.values.sum() == 0: continue
        try:
            chi = stats.chi2_contingency(table.values, correction=False)
            if table.shape == (2, 2) and (chi.expected_freq < 5).any():
                _, pvalue = stats.fisher_exact(table.values)
                test_name = 'Fisher exact test (exploratory; sparse 2x2 table)'
            else:
                pvalue = chi.pvalue
                test_name = 'Chi-square independence (exploratory)'
            findings.append({'factor': str(col), 'test': test_name, 'p_value': _pvalue(pvalue), 'levels': _group_rates(frame.loc[valid_def], str(col), defect_col),
                             'interpretation': 'Association screen only; multiple testing, synthetic data, selection effects, and confounding limit inference.'})
        except Exception: pass
    numeric_tokens = ('tool_age', 'coolant', 'vibration', 'temperature')
    for col in frame.columns:
        if not any(t in str(col).lower() for t in numeric_tokens): continue
        x = pd.to_numeric(frame[col], errors='coerce')
        a = x[(defect == 1) & x.notna()]; b = x[(defect == 0) & x.notna()]
        if len(a) >= 2 and len(b) >= 2:
            test = stats.ttest_ind(a, b, equal_var=False)
            findings.append({'factor': str(col), 'test': 'Welch t-test comparing defect vs conforming units', 'defect_mean': _round(a.mean()), 'conforming_mean': _round(b.mean()), 'p_value': _pvalue(test.pvalue),
                             'interpretation': 'Exploratory association only; does not establish causation.'})
    return sorted(findings, key=lambda x: x.get('p_value', 1.0))


def _gauge_rr(frame: pd.DataFrame, source: str, lsl: float | None = None, usl: float | None = None) -> dict:
    part = _column(frame, 'part_id', 'part', 'sample_id')
    app = _column(frame, 'appraiser', 'operator', 'inspector')
    trial = _column(frame, 'trial', 'replicate')
    measure = _column(frame, 'measured_diameter', 'measurement', 'reading', 'value')
    if not all([part, app, trial, measure]): return {'source': source, 'status': 'blocked', 'reason': 'Gauge R&R requires part, appraiser, trial, and measured-value columns.'}
    d = frame[[part, app, trial, measure]].copy()
    d[measure] = pd.to_numeric(d[measure], errors='coerce')
    d = d.dropna()
    # A balanced crossed design is required for this classical ANOVA decomposition.
    counts = d.groupby([part, app])[trial].nunique()
    p, a, r = d[part].nunique(), d[app].nunique(), int(counts.median()) if len(counts) else 0
    balanced = bool(len(counts) == p*a and counts.nunique() == 1 and d.groupby([part, app, trial]).size().max() == 1)
    if not balanced or p < 2 or a < 2 or r < 2:
        return {'source': source, 'status': 'blocked', 'reason': 'Classical crossed Gauge R&R requires a complete balanced parts × appraisers × trials design with at least 2 of each.', 'parts': int(p), 'appraisers': int(a), 'trials_per_cell_median': r, 'balanced': balanced}
    grand = d[measure].mean(); pm = d.groupby(part)[measure].mean(); am = d.groupby(app)[measure].mean(); cell = d.groupby([part, app])[measure].mean()
    ss_part = a*r * float(((pm-grand)**2).sum())
    ss_app = p*r * float(((am-grand)**2).sum())
    ss_int = 0.0
    for (part_id, app_id), cm in cell.items(): ss_int += r * float(cm - pm.loc[part_id] - am.loc[app_id] + grand)**2
    joined = d.join(cell.rename('_cell'), on=[part, app])
    ss_repeat = float(((joined[measure]-joined['_cell'])**2).sum())
    df_part, df_app, df_int, df_repeat = p-1, a-1, (p-1)*(a-1), p*a*(r-1)
    ms_part, ms_app, ms_int, ms_repeat = ss_part/df_part, ss_app/df_app, ss_int/df_int, ss_repeat/df_repeat
    var_repeat = max(ms_repeat, 0.0)
    var_interaction = max((ms_int-ms_repeat)/r, 0.0)
    var_appraiser = max((ms_app-ms_int)/(p*r), 0.0)
    var_part = max((ms_part-ms_int)/(a*r), 0.0)
    var_grr = var_repeat + var_interaction + var_appraiser
    var_total = var_grr + var_part
    sd_grr, sd_total = math.sqrt(var_grr), math.sqrt(var_total)
    ndc = 1.41*math.sqrt(var_part)/sd_grr if sd_grr > 0 else None
    return {'source': source, 'status': 'calculated', 'method': 'Classical crossed Gauge R&R ANOVA variance components', 'parts': int(p), 'appraisers': int(a), 'trials_per_cell': int(r), 'balanced_design': balanced,
            'variance_components': {'repeatability': _round(var_repeat), 'appraiser': _round(var_appraiser), 'part_by_appraiser_interaction': _round(var_interaction), 'total_gauge_rr': _round(var_grr), 'part_to_part': _round(var_part), 'total_variation': _round(var_total)},
            'percent_study_variation': {'repeatability': _round(100*math.sqrt(var_repeat)/sd_total) if sd_total else None, 'reproducibility_including_interaction': _round(100*math.sqrt(var_appraiser+var_interaction)/sd_total) if sd_total else None, 'total_gauge_rr': _round(100*sd_grr/sd_total) if sd_total else None, 'part_to_part': _round(100*math.sqrt(var_part)/sd_total) if sd_total else None},
            'percent_tolerance': _round(100*6*sd_grr/(usl-lsl)) if lsl is not None and usl is not None and usl > lsl else None, 'ndc_estimate': _round(ndc), 'interpretation': 'Use % study variation and ndc as screening indicators; acceptance thresholds depend on the industry, intended use, and customer requirements.',
            'limitations': ['Balanced crossed ANOVA decomposition assumes the same parts are measured by each appraiser across repeated trials.', 'Part selection should span the actual process variation.', 'A supplied reference/part mean column is not used in the variance-component calculation.', 'Percent tolerance is not calculated because the specification tolerance is not passed into this function.']}


def _pilot_analysis(frame: pd.DataFrame, source: str) -> dict:
    factor_a = _column(frame, 'tool_replacement_policy', 'factor_a')
    factor_b = _column(frame, 'offset_verification_method', 'factor_b')
    ycol = _column(frame, 'diameter_mm', 'measurement', 'outcome', 'value')
    if not all([factor_a, factor_b, ycol]): return {'source': source, 'status': 'blocked', 'reason': 'Need two factor columns and a numeric outcome column.'}
    d = frame[[factor_a, factor_b, ycol]].copy(); d[ycol] = pd.to_numeric(d[ycol], errors='coerce'); d = d.dropna()
    levels_a = sorted(d[factor_a].astype(str).unique()); levels_b = sorted(d[factor_b].astype(str).unique())
    if len(levels_a) != 2 or len(levels_b) != 2: return {'source': source, 'status': 'blocked', 'reason': 'This pilot analyzer currently requires exactly two levels for each factor.', 'levels_a': levels_a, 'levels_b': levels_b}
    a_map = {levels_a[0]: -1, levels_a[1]: 1}; b_map = {levels_b[0]: -1, levels_b[1]: 1}
    av = d[factor_a].astype(str).map(a_map).to_numpy(float); bv = d[factor_b].astype(str).map(b_map).to_numpy(float); y = d[ycol].to_numpy(float)
    X = np.column_stack([np.ones(len(y)), av, bv, av*bv])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None); residual = y-X@beta; df = len(y)-X.shape[1]
    if df <= 0: return {'source': source, 'status': 'blocked', 'reason': 'Insufficient residual degrees of freedom.'}
    mse = float(residual@residual/df); cov = mse*np.linalg.inv(X.T@X); tcrit = float(stats.t.ppf(.975, df))
    names = [('factor_a_main_effect', 1), ('factor_b_main_effect', 2), ('interaction_effect', 3)]
    effects = {}
    for label, idx in names:
        effect = 2*beta[idx]; se = 2*math.sqrt(max(cov[idx, idx], 0)); tval = effect/se if se else None
        pval = 2*stats.t.sf(abs(tval), df) if tval is not None else None
        effects[label] = {'effect_on_mean': _round(effect), 'standard_error': _round(se), 'ci_95': [_round(effect-tcrit*se), _round(effect+tcrit*se)], 'p_value': _pvalue(pval)}
    cells = []
    for aa in levels_a:
        for bb in levels_b:
            sub = d[(d[factor_a].astype(str)==aa)&(d[factor_b].astype(str)==bb)][ycol]
            cells.append({'factor_a': aa, 'factor_b': bb, 'n': int(len(sub)), 'mean_outcome': _round(sub.mean()), 'sd': _round(sub.std(ddof=1)), 'observed_out_of_spec': int(((sub < pd.to_numeric(d.get('lsl_mm', pd.Series([np.nan]*len(d))), errors='coerce').iloc[0]) | (sub > pd.to_numeric(d.get('usl_mm', pd.Series([np.nan]*len(d))), errors='coerce').iloc[0])).sum()) if 'lsl_mm' in d and 'usl_mm' in d else None})
    return {'source': source, 'status': 'calculated', 'method': 'Balanced/unbalanced 2×2 linear factorial model with coded factors and interaction; t-based coefficient intervals', 'outcome': ycol, 'factor_a': {'column': factor_a, 'levels': levels_a}, 'factor_b': {'column': factor_b, 'levels': levels_b}, 'n': len(d), 'residual_df': int(df), 'cell_summary': cells, 'effects': effects,
            'interpretation': 'Effect estimates are conditional on this pilot model. Inspect randomization, balance, residuals, independence, and practical effect size. Synthetic pilot data do not prove a real-world improvement.'}


def _p_chart(frame: pd.DataFrame, source: str) -> dict:
    ncol = _column(frame, 'sample_size', 'subgroup_size', 'inspected', 'denominator')
    xcol = _column(frame, 'defect_count', 'defects', 'reject_count', 'nonconforming')
    if not ncol or not xcol: return {'source': source, 'status': 'blocked', 'reason': 'p-chart requires defect counts and sample sizes per time subgroup.'}
    n = pd.to_numeric(frame[ncol], errors='coerce'); x = pd.to_numeric(frame[xcol], errors='coerce')
    valid = n.notna() & x.notna() & (n > 0) & (x >= 0) & (x <= n) & (x.round() == x) & (n.round() == n)
    if not valid.all():
        return {'source': source, 'status': 'blocked', 'reason': 'Invalid subgroup rows: counts and sample sizes must be nonnegative integers with 0 <= defects <= n and n > 0.', 'invalid_rows': int((~valid).sum())}
    p = x/n; pbar = float(x.sum()/n.sum()); sigma = np.sqrt(pbar*(1-pbar)/n)
    lcl = np.maximum(0, pbar-3*sigma); ucl = np.minimum(1, pbar+3*sigma)
    signal_idx = [int(i) for i, flag in enumerate((p < lcl) | (p > ucl)) if flag]
    tcol = _column(frame, 'date', 'day', 'week', 'time', 'period')
    rows = [{'subgroup': str(frame.iloc[i][tcol]) if tcol else i+1, 'n': int(n.iloc[i]), 'defects': int(x.iloc[i]), 'p': _round(p.iloc[i]), 'lcl': _round(lcl.iloc[i]), 'ucl': _round(ucl.iloc[i]), 'outside_limits': i in signal_idx} for i in range(len(frame))]
    supplied_rate_col = _column(frame, 'defect_rate', 'proportion_defective', 'p_value')
    rate_validation = {'status':'not_available'}
    if supplied_rate_col:
        supplied = pd.to_numeric(frame[supplied_rate_col], errors='coerce')
        compare = supplied.notna()
        mismatches = compare & ((supplied-p).abs() > 1e-9)
        rate_validation = {'status':'passed' if not mismatches.any() else 'mismatch_found', 'column':supplied_rate_col, 'compared_rows':int(compare.sum()), 'mismatch_count':int(mismatches.sum()), 'mismatch_subgroups':[str(frame.iloc[i][tcol]) if tcol else i+1 for i in np.where(mismatches.to_numpy())[0][:30]]}
    return {'source': source, 'status': 'calculated', 'chart': 'p-chart, 3-sigma limits with variable n', 'subgroups': len(frame), 'total_units': int(n.sum()), 'total_defects': int(x.sum()), 'center_line': _round(pbar), 'supplied_rate_validation': rate_validation, 'signals_outside_limits': signal_idx, 'signal_rows': [rows[i] for i in signal_idx], 'rows': rows,
            'limitations': ['Only points outside 3-sigma limits are tested; Western Electric/Nelson run/trend rules are not implemented.', 'Control limits describe process behavior and are not customer specification limits.', 'A p-chart assumes reasonably consistent defect classification and independent units within subgroups.']}


def _financial_model(frame: pd.DataFrame, charter: dict | None) -> dict:
    params = {}
    pcol = _column(frame, 'parameter', 'assumption', 'metric'); vcol = _column(frame, 'value', 'amount', 'number')
    if pcol and vcol:
        for _, row in frame.iterrows():
            try: params[str(row[pcol]).strip().lower()] = float(row[vcol])
            except (ValueError, TypeError): pass
    params.update({str(k).lower(): v for k,v in (charter or {}).get('financial_assumptions', {}).items() if k not in params})
    volume = params.get('annual_production_units'); baseline = params.get('baseline_defect_rate'); target = params.get('target_defect_rate'); cost = params.get('avoidable_cost_per_defective_unit_inr'); impl = params.get('implementation_cost_inr')
    missing = [k for k,v in [('annual_production_units',volume),('baseline_defect_rate',baseline),('target_defect_rate',target),('avoidable_cost_per_defective_unit_inr',cost)] if v is None]
    if missing: return {'status':'blocked','missing_inputs':missing,'note':'No financial savings are estimated without explicit volume, rates, and cost inputs.'}
    if not (0 <= baseline <= 1 and 0 <= target <= 1 and volume >= 0 and cost >= 0): return {'status':'blocked','reason':'Financial assumptions outside valid ranges.'}
    annual_baseline_defects = volume*baseline; annual_target_defects = volume*target; avoided = annual_baseline_defects-annual_target_defects
    gross = avoided*cost; net = gross-(impl or 0)
    return {'status':'forecast_only','inputs':{'annual_production_units':volume,'baseline_defect_rate':baseline,'target_defect_rate':target,'avoidable_cost_per_defective_unit_inr':cost,'implementation_cost_inr':impl},
            'annual_baseline_defects_forecast':_round(annual_baseline_defects,2),'annual_target_defects_forecast':_round(annual_target_defects,2),'annual_defects_avoided_forecast':_round(avoided,2),
            'gross_annual_savings_forecast_inr':_round(gross,2),'first_year_net_forecast_inr':_round(net,2),'simple_payback_months_forecast':_round((impl/gross*12) if impl and gross>0 else (0 if not impl else None),2),
            'caveats':['Forecast from supplied assumptions, not realized savings.', 'Assumes each avoided defective unit produces the full stated avoidable cost benefit.', 'Finance must validate cost definitions, volume, ramp-up, implementation and recurring costs, and double counting.']}


def _charter(structured: dict[str, Any], documents: dict[str, str]) -> dict | None:
    for name, obj in structured.items():
        if 'charter' in name.lower() and isinstance(obj, dict): return obj
    for name, obj in structured.items():
        if isinstance(obj, dict) and any(k in obj for k in ('project_title','problem_statement','goal')): return obj
    return None


def _find_spec(charter: dict | None, prompt: str, docs: dict[str, str]) -> tuple[float | None, float | None, float | None, str]:
    ctq = (charter or {}).get('customer_ctq') or {}
    lsl = ctq.get('lsl', ctq.get('LSL_mm')); usl = ctq.get('usl', ctq.get('USL_mm')); target = ctq.get('target', ctq.get('target_mm'))
    if lsl is not None and usl is not None:
        return float(lsl), float(usl), float(target) if target is not None else None, 'project_charter'
    text = prompt + '\n' + '\n'.join(docs.values())
    patterns = [(r'LSL\s*[=:]\s*(-?\d+(?:\.\d+)?)', 'lsl'), (r'USL\s*[=:]\s*(-?\d+(?:\.\d+)?)', 'usl'), (r'Target\s*[=:]\s*(-?\d+(?:\.\d+)?)', 'target')]
    found = {}
    for pattern, key in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m: found[key] = float(m.group(1))
    return found.get('lsl'), found.get('usl'), found.get('target'), 'prompt_or_reference_text' if found else 'not_supplied'


def _charter_deliverables(charter: dict | None, prompt: str, docs: dict[str, str]) -> dict:
    charter = charter or {}
    problem = charter.get('problem_statement') or 'Not explicitly supplied; requires process-owner confirmation.'
    goal = charter.get('goal') or charter.get('goal_statement') or 'Not explicitly supplied; define a measurable target and timeframe.'
    title = charter.get('project_title') or charter.get('name') or 'Six Sigma improvement project'
    ctq = charter.get('customer_ctq') or {}
    sipoc = {'suppliers':['Material / input suppliers (draft; confirm with process owner)'],'inputs':['Raw material, machine setup, tools, work instructions, measurement system (draft)'],'process':['Receive input → set up process → execute operation → inspect output → disposition (draft)'],'outputs':['Processed product/service and inspection result (draft)'],'customers':['Next operation / external customer (draft)']}
    return {'title':title,'company':charter.get('company'),'department':charter.get('department'),'problem_statement':problem,'goal_statement':goal,'primary_metric':charter.get('primary_metric') or 'To be agreed and operationally defined',
            'ctq':ctq,'scope_in':charter.get('scope_in',[]),'scope_out':charter.get('scope_out',[]),'duration_weeks':charter.get('project_duration_weeks'),
            'sipoc':sipoc,'voc_ctq':{'voice_of_customer':'Customer voice evidence was not present as a distinct source; do not invent customer quotations.','ctq_tree':[{'need':'Conformance to stated product/process requirement','driver':ctq.get('characteristic','Confirm CTQ'),'measure':ctq.get('characteristic','Define operational measurement'),'specification':{'lsl':ctq.get('lsl'),'target':ctq.get('target'),'usl':ctq.get('usl'),'unit':ctq.get('unit')}}]},
            'measurement_plan':[{'measure':'Primary outcome','definition':charter.get('primary_metric') or 'Confirm the operational definition','source':'Baseline and post-change dataset if supplied','frequency':'Specify during Measure','owner':'Process owner + data analyst','status':'draft; confirm operational definition and sampling plan'},
                                {'measure':'Measurement system','definition':'Repeatability, reproducibility, resolution, calibration','source':'Gauge R&R / calibration records','frequency':'Before capability claims','owner':'Metrology / MSA specialist','status':'Gauge R&R analysis available only if valid crossed data is supplied'}]}


def _close_check(metric: str, reported: Any, recomputed: Any, source: str, tolerance: float = 1e-6) -> dict:
    try:
        a, b = float(reported), float(recomputed)
        ok = math.isfinite(a) and math.isfinite(b) and abs(a-b) <= tolerance * max(1.0, abs(b))
        return {'metric':metric,'source':source,'reported':_round(a,10),'independently_recomputed':_round(b,10),'absolute_difference':_round(abs(a-b),12),'status':'passed' if ok else 'failed','tolerance':tolerance}
    except (TypeError, ValueError):
        return {'metric':metric,'source':source,'reported':reported,'independently_recomputed':recomputed,'status':'not_comparable','reason':'One value is unavailable or non-numeric.'}


def _calculation_cross_checks(baseline: dict, post: dict | None, gauge: dict | None, pilot: dict | None, pchart: dict | None, financial: dict | None, baseline_frame: pd.DataFrame | None, post_frame: pd.DataFrame | None, pilot_frame: pd.DataFrame | None) -> dict:
    checks = []
    if baseline_frame is not None and baseline.get('status') == 'calculated':
        col = baseline['outcome_column']; raw = pd.to_numeric(baseline_frame[col], errors='coerce').replace([np.inf,-np.inf],np.nan); vals = raw.dropna().to_numpy(float)
        if len(vals):
            checks.append(_close_check('baseline mean', baseline.get('mean'), float(np.mean(vals)), baseline['source']))
            if len(vals)>1: checks.append(_close_check('baseline sample standard deviation', baseline.get('sample_std_dev'), float(np.std(vals, ddof=1)), baseline['source']))
            spec = baseline.get('specification') or {}
            if spec.get('status') == 'calculated':
                oos = int((((raw < spec['lsl']) if spec.get('lsl') is not None else pd.Series(False,index=raw.index)) | ((raw > spec['usl']) if spec.get('usl') is not None else pd.Series(False,index=raw.index))).sum())
                checks.append(_close_check('baseline out-of-spec count', spec.get('out_of_spec_count'), oos, baseline['source'], tolerance=0))
                checks.append(_close_check('baseline defect rate', spec.get('defect_rate'), oos/len(vals), baseline['source']))
                checks.append(_close_check('baseline yield', spec.get('yield'), 1-oos/len(vals), baseline['source']))
                checks.append(_close_check('baseline DPMO (one opportunity/unit)', baseline.get('dpmo_one_opportunity_per_unit'), oos/len(vals)*1_000_000, baseline['source']))
    if post_frame is not None and post and post.get('status') == 'calculated':
        col = post['outcome_column']; raw = pd.to_numeric(post_frame[col], errors='coerce').replace([np.inf,-np.inf],np.nan); vals = raw.dropna(); spec = post.get('specification') or {}
        if spec.get('status') == 'calculated' and len(vals):
            oos = int((((raw < spec['lsl']) if spec.get('lsl') is not None else pd.Series(False,index=raw.index)) | ((raw > spec['usl']) if spec.get('usl') is not None else pd.Series(False,index=raw.index))).sum())
            checks.append(_close_check('post-change out-of-spec count', spec.get('out_of_spec_count'), oos, post['source'], tolerance=0))
            checks.append(_close_check('post-change defect rate', spec.get('defect_rate'), oos/len(vals), post['source']))
    if gauge and gauge.get('status') == 'calculated':
        vc = gauge['variance_components']; grr = sum(float(vc[k] or 0) for k in ('repeatability','appraiser','part_by_appraiser_interaction'))
        checks.append(_close_check('Gauge R&R variance-component sum', vc.get('total_gauge_rr'), grr, gauge['source']))
    if pilot and pilot.get('status') == 'calculated' and pilot_frame is not None:
        a_col, b_col, y_col = pilot['factor_a']['column'], pilot['factor_b']['column'], pilot['outcome']
        d = pilot_frame[[a_col,b_col,y_col]].copy(); d[y_col]=pd.to_numeric(d[y_col],errors='coerce'); d=d.dropna(); la=pilot['factor_a']['levels']; lb=pilot['factor_b']['levels']
        cell = {(a,b):float(d.loc[(d[a_col].astype(str)==a)&(d[b_col].astype(str)==b),y_col].mean()) for a in la for b in lb}
        direct = {'factor_a_main_effect':((cell[(la[1],lb[0])]+cell[(la[1],lb[1])])/2)-((cell[(la[0],lb[0])]+cell[(la[0],lb[1])])/2),
                  'factor_b_main_effect':((cell[(la[0],lb[1])]+cell[(la[1],lb[1])])/2)-((cell[(la[0],lb[0])]+cell[(la[1],lb[0])])/2),
                  'interaction_effect':0.5*((cell[(la[1],lb[1])]-cell[(la[1],lb[0])])-(cell[(la[0],lb[1])]-cell[(la[0],lb[0])]))}
        for key, val in direct.items(): checks.append(_close_check(f'pilot {key} (cell-mean contrast)', pilot['effects'][key]['effect_on_mean'], val, pilot['source'], tolerance=1e-5))
    if pchart and pchart.get('status') == 'calculated':
        checks.append(_close_check('p-chart center line', pchart.get('center_line'), pchart['total_defects']/pchart['total_units'], pchart['source']))
    if financial and financial.get('status') == 'forecast_only':
        inp=financial['inputs']; gross=(inp['annual_production_units']*(inp['baseline_defect_rate']-inp['target_defect_rate'])*inp['avoidable_cost_per_defective_unit_inr'])
        checks.append(_close_check('gross annual savings forecast', financial.get('gross_annual_savings_forecast_inr'), gross, 'financial_model'))
        checks.append(_close_check('first-year net forecast', financial.get('first_year_net_forecast_inr'), gross-(inp.get('implementation_cost_inr') or 0), 'financial_model'))
    failures = [c for c in checks if c.get('status') == 'failed']
    return {'status':'passed' if checks and not failures else ('failed_checks' if failures else 'no_checks_available'), 'check_count':len(checks), 'failed_check_count':len(failures), 'checks':checks, 'method_note':'Independent arithmetic cross-checks use separate formulas over the same source rows. This is not an external audit or validation of source-data truth.'}


def analyze_dmaic_case(files: list[tuple[str, bytes]], prompt: str) -> dict:
    dataframes, documents, structured, inventory = _parse_payloads(files)
    charter = _charter(structured, documents)
    embedded_prompts = [text for name, text in documents.items() if any(token in name.lower() for token in ('prompt', 'task', 'instruction', 'brief'))]
    analysis_prompt = prompt.strip() + ('\n\nInstructions extracted from uploaded task/reference files:\n' + '\n\n'.join(embedded_prompts) if embedded_prompts else '')
    lsl, usl, target, spec_source = _find_spec(charter, analysis_prompt, documents)
    if lsl is not None and usl is not None and lsl >= usl: raise ValueError('Invalid specification limits: LSL must be less than USL.')
    baseline_name, baseline_frame = _pick(dataframes, ('baseline',), ('diameter','defect'))
    if baseline_frame is None:
        baseline_name, baseline_frame = _pick(dataframes, (), ('diameter','defect'))
    post_name, post_frame = _pick(dataframes, ('post_improvement','post improvement','after','postchange'))
    production_name, production_frame = _pick(dataframes, ('production_context',))
    root_name, root_frame = _pick(dataframes, ('root_cause','root cause'))
    if root_frame is None and production_frame is not None: root_name, root_frame = production_name, production_frame
    gauge_name, gauge_frame = _pick(dataframes, ('gauge_rr','gauge r&r','grr'))
    pilot_name, pilot_frame = _pick(dataframes, ('improvement_pilot','pilot','factorial'))
    daily_name, daily_frame = _pick(dataframes, ('daily_quality','daily','p_chart','p-chart'))
    financial_name, financial_frame = _pick(dataframes, ('financial_model','financial','benefit','savings'))
    dictionary_name, dictionary_frame = _pick(dataframes, ('data_dictionary','dictionary','data dictionary'))
    if baseline_frame is not None:
        # Use limits embedded in dataset if no charter/prompt limits were supplied.
        if lsl is None:
            col = _column(baseline_frame, 'lsl'); s = _num(baseline_frame, col); lsl = float(s.iloc[0]) if len(s) else None
        if usl is None:
            col = _column(baseline_frame, 'usl'); s = _num(baseline_frame, col); usl = float(s.iloc[0]) if len(s) else None
        if target is None:
            col = _column(baseline_frame, 'target'); s = _num(baseline_frame, col); target = float(s.iloc[0]) if len(s) else None
        if spec_source == 'not_supplied' and (lsl is not None or usl is not None): spec_source = 'baseline_dataset_columns'
    baseline = _baseline_analysis(baseline_name, baseline_frame, lsl, usl) if baseline_frame is not None else {'status':'blocked','reason':'No baseline dataset identified. Upload a baseline dataset or name it clearly in the prompt.'}
    post = _baseline_analysis(post_name, post_frame, lsl, usl) if post_frame is not None else None
    comparisons = None
    if post and baseline.get('status') == 'calculated' and post.get('status') == 'calculated':
        b = baseline['specification']; p = post['specification']
        if b.get('status') == 'calculated' and p.get('status') == 'calculated':
            x1, n1 = b['out_of_spec_count'], b['n']; x2, n2 = p['out_of_spec_count'], p['n']
            pooled = (x1+x2)/(n1+n2) if n1+n2 else 0
            se = math.sqrt(pooled*(1-pooled)*(1/n1+1/n2)) if n1 and n2 else None
            z = ((x1/n1)-(x2/n2))/se if se and se>0 else None
            expected = np.array([[x1, n1-x1], [x2, n2-x2]], dtype=float)
            row_totals = expected.sum(axis=1); col_totals = expected.sum(axis=0); grand = expected.sum()
            exp_counts = np.outer(row_totals, col_totals)/grand if grand else expected
            if (exp_counts < 5).any():
                _, pval = stats.fisher_exact(expected.astype(int))
                test_name = 'Fisher exact test (two-sided; sparse counts)'
            else:
                pval = 2*stats.norm.sf(abs(z)) if z is not None else None
                test_name = 'two-proportion z-test (two-sided)'
            p1, p2 = x1/n1, x2/n2
            ci1, ci2 = b.get('defect_ci_95'), p.get('defect_ci_95')
            diff = p1-p2
            diff_ci = [None, None]
            if ci1 and ci2:
                diff_ci = [_round(diff-math.sqrt(max(p1-ci1[0],0)**2+max(ci2[1]-p2,0)**2)), _round(diff+math.sqrt(max(ci1[1]-p1,0)**2+max(p2-ci2[0],0)**2))]
            comparisons = {'baseline_defect_rate':_round(p1),'post_defect_rate':_round(p2),'absolute_reduction_percentage_points':_round(100*diff,3),'absolute_reduction_ci_95':[_round(diff_ci[0]*100,3) if diff_ci[0] is not None else None,_round(diff_ci[1]*100,3) if diff_ci[1] is not None else None],'relative_reduction':_round(diff/p1) if p1 else None,
                           'baseline_defect_rate_ci_95':ci1,'post_defect_rate_ci_95':ci2,'test_name':test_name,'test_p_value':_round(pval),
                           'interpretation':'Descriptive pre/post comparison. Nonrandomized samples, possible time confounding, small defect counts, and synthetic data prevent causal claims.'}
    # Pareto by defect type, only where the source label exists.
    pareto = []
    if baseline_frame is not None:
        dtype = _column(baseline_frame, 'defect_type', 'failure_mode', 'reason')
        flag = _column(baseline_frame, 'defect', 'defective', 'reject')
        if dtype:
            work = baseline_frame.copy()
            if flag:
                f = pd.to_numeric(work[flag], errors='coerce')
                work = work.loc[f == 1]
            counts = work[dtype].fillna('Unknown').astype(str).value_counts()
            total = int(counts.sum()); cumulative = 0
            for label, count in counts.items():
                cumulative += int(count); pareto.append({'category':label,'count':int(count),'share':_round(count/total) if total else None,'cumulative_share':_round(cumulative/total) if total else None})
    association = []
    association_sources = []
    if root_frame is not None: association_sources.append((root_name, root_frame))
    if production_frame is not None and production_frame is not root_frame: association_sources.append((production_name, production_frame))
    seen_association_factors = set()
    for source_name, source_frame in association_sources:
        for item in _association_tests(source_frame):
            factor_key = (item.get('factor'), item.get('test'))
            if factor_key in seen_association_factors:
                continue
            item['source'] = source_name
            association.append(item)
            seen_association_factors.add(factor_key)
    association.sort(key=lambda x: x.get('p_value') if x.get('p_value') is not None else 1.0)
    gauge = _gauge_rr(gauge_frame, gauge_name, lsl, usl) if gauge_frame is not None else None
    pilot = _pilot_analysis(pilot_frame, pilot_name) if pilot_frame is not None else None
    pchart = _p_chart(daily_frame, daily_name) if daily_frame is not None else None
    financial = _financial_model(financial_frame, charter) if financial_frame is not None else _financial_model(pd.DataFrame(), charter) if charter and charter.get('financial_assumptions') else None
    role_assignments = {name: role for name, role in [
        (baseline_name,'Baseline process outcome / Measure baseline'), (post_name,'Post-improvement verification sample'),
        (production_name,'Production context / stratification'), (root_name,'Root-cause investigation context'),
        (gauge_name,'Measurement system study / Gauge R&R'), (pilot_name,'Improvement pilot / 2×2 factorial'),
        (daily_name,'Control-phase subgroup data / p-chart'), (financial_name,'Financial assumptions / benefit forecast'),
        (dictionary_name,'Data dictionary / field definitions')] if name}
    for item in inventory:
        fname = item.get('filename','')
        item['dmaic_role'] = role_assignments.get(fname)
        if not item['dmaic_role']:
            low = fname.lower()
            if 'charter' in low: item['dmaic_role'] = 'Define / project charter'
            elif any(x in low for x in ('prompt','task','instruction','brief')): item['dmaic_role'] = 'User task instructions'
            elif 'readme' in low: item['dmaic_role'] = 'Package context and limitations'
            elif 'manifest' in low: item['dmaic_role'] = 'Package manifest / provenance'
            elif item['kind'] == 'dataset': item['dmaic_role'] = 'Additional dataset; role needs confirmation'
            elif item['kind'] in ('document','structured_json'): item['dmaic_role'] = 'Reference document / structured context'
            else: item['dmaic_role'] = 'Unsupported or unclassified input'
    data_dictionary_entries = []
    if dictionary_frame is not None:
        field_col = _column(dictionary_frame, 'field', 'column', 'variable', 'name')
        definition_col = _column(dictionary_frame, 'definition', 'description', 'meaning')
        unit_col = _column(dictionary_frame, 'unit', 'values', 'allowed')
        for _, row in dictionary_frame.head(100).iterrows():
            data_dictionary_entries.append({'field':str(row[field_col]) if field_col and pd.notna(row[field_col]) else '', 'definition':str(row[definition_col]) if definition_col and pd.notna(row[definition_col]) else '', 'unit_or_values':str(row[unit_col]) if unit_col and pd.notna(row[unit_col]) else ''})
    # Global file-level quality screening.
    quality = []
    for name, frame in dataframes.items():
        warnings = []
        duplicate_rows = int(frame.duplicated().sum())
        if duplicate_rows: warnings.append(f'{duplicate_rows} exact duplicate row(s); retained and not silently removed.')
        missing = int(frame.isna().sum().sum())
        if missing: warnings.append(f'{missing} missing cell(s) across the table.')
        if frame.empty: warnings.append('Dataset has no rows.')
        quality.append({'source':name,'rows':int(len(frame)),'columns':int(len(frame.columns)),'duplicate_rows':duplicate_rows,'missing_cells':missing,'warnings':warnings})
    # Warn when datasets share most of their unit IDs; do not treat overlapping files as independent samples.
    id_overlap_audit = []
    id_col_tokens = ('part_id', 'unit_id', 'sample_id', 'record_id')
    named_frames = [(baseline_name, baseline_frame), (production_name, production_frame), (root_name, root_frame), (post_name, post_frame)]
    for i, (name_a, frame_a) in enumerate(named_frames):
        if frame_a is None or not name_a: continue
        id_a_col = _column(frame_a, *id_col_tokens)
        if not id_a_col: continue
        ids_a = set(frame_a[id_a_col].dropna().astype(str))
        for name_b, frame_b in named_frames[i+1:]:
            if frame_b is None or not name_b or name_a == name_b: continue
            id_b_col = _column(frame_b, *id_col_tokens)
            if not id_b_col: continue
            ids_b = set(frame_b[id_b_col].dropna().astype(str))
            overlap = len(ids_a & ids_b)
            if overlap:
                id_overlap_audit.append({'dataset_a':name_a,'dataset_b':name_b,'shared_id_count':overlap,'shared_fraction_of_smaller_dataset':_round(overlap/max(1,min(len(ids_a),len(ids_b)))),'warning':'Shared unit IDs mean these files may be alternate views of the same observations; do not pool them as independent samples without confirming provenance.'})
    calculation_validation = _calculation_cross_checks(baseline, post, gauge, pilot, pchart, financial, baseline_frame, post_frame, pilot_frame)
    findings = []
    if baseline.get('status') == 'calculated':
        spec = baseline.get('specification', {})
        findings.append({'kind':'calculated','claim':f"Baseline: {baseline['n_valid']} valid measurements; mean {baseline['mean']}; sample SD {baseline['sample_std_dev']}.",'source':baseline_name})
        if spec.get('status') == 'calculated': findings.append({'kind':'calculated','claim':f"Observed baseline defects: {spec['out_of_spec_count']}/{spec['n']} ({spec['defect_rate']:.1%}); observed yield {spec['yield']:.1%}.",'source':baseline_name})
        if baseline.get('defect_flag_validation',{}).get('status') == 'mismatch_found': findings.append({'kind':'data_quality','claim':f"Defect flag does not match strict specification checks on {baseline['defect_flag_validation']['mismatch_count']} rows.",'source':baseline_name})
    if comparisons: findings.append({'kind':'descriptive_comparison','claim':f"Observed defect-rate change from {comparisons['baseline_defect_rate']:.1%} to {comparisons['post_defect_rate']:.1%}; this is not causal proof.",'source':f'{baseline_name} + {post_name}'})
    hypothesis_candidates = []
    for a in association[:8]:
        levels = a.get('levels') or []
        if a.get('p_value') is not None and a['p_value'] < .05:
            hypothesis_candidates.append({'hypothesis':f"{a['factor']} may be associated with the defect outcome in this dataset.",'evidence':f"Exploratory {a['test']} p={a['p_value']}; group rates are descriptive.",'status':'hypothesis_only','source':a.get('source',root_name)})
    limitations = [
        'The supplied README labels this package as synthetic demonstration data; findings must not be represented as real company performance.',
        'A statistical association is not proof of causation. Root-cause candidates need process observation, confounder review, and prospective verification.',
        'Capability indices are provisional until the measurement system is adequate and process stability/distribution assumptions are reviewed.',
        'A 100-unit sample gives limited precision for low defect rates; confidence intervals should accompany point estimates.',
        'The p-chart implementation checks only 3-sigma point signals, not all run/trend rules. Control limits are not specification limits.',
        'Financial values are forecast scenarios based on supplied assumptions, not realized savings.',
    ]
    if not charter: limitations.insert(0, 'No machine-readable project charter was identified. The charter/SIPOC/VOC elements are drafts and require owner/customer confirmation.')
    if lsl is None or usl is None: limitations.insert(0, 'One or both specification limits are missing; spec-based defect rate and capability cannot be fully evaluated.')
    if not documents: limitations.append('No reference documents were indexed. Add work instructions, prior reports, problem descriptions, and project requirements to ground recommendations.')
    charter_assumption_validation = {'status':'not_available'}
    if charter:
        assumptions = charter.get('financial_assumptions') or {}
        observed_base = (baseline.get('specification') or {}).get('defect_rate')
        observed_post = (post or {}).get('specification', {}).get('defect_rate') if post else None
        assumed_base = assumptions.get('baseline_defect_rate')
        target_rate = assumptions.get('target_defect_rate')
        charter_assumption_validation = {'status':'calculated','assumed_baseline_defect_rate':assumed_base,'observed_baseline_defect_rate':observed_base,
            'baseline_assumption_difference_percentage_points':_round((observed_base-assumed_base)*100,4) if observed_base is not None and assumed_base is not None else None,
            'baseline_assumption_consistent_with_sample':abs(observed_base-assumed_base)<=0.005 if observed_base is not None and assumed_base is not None else None,
            'target_defect_rate':target_rate,'observed_post_defect_rate':observed_post,
            'post_sample_at_or_below_target':observed_post<=target_rate if observed_post is not None and target_rate is not None else None,
            'note':'Sample-level reconciliation only. A target met in one sample does not establish sustained improvement.'}
    deliverables = _charter_deliverables(charter, analysis_prompt, documents)
    phase_status = [
        {'phase':'Define','status':'draft_for_review' if charter else 'blocked_missing_charter','deliverables':['Confirm problem, goal, scope, sponsor, process owner, customer CTQ, and benefit rules.'],'exit_evidence':['Approved charter','Validated VOC/CTQ','Process boundary and SIPOC confirmed']},
        {'phase':'Measure','status':'evidence_available_needs_validation' if baseline.get('status')=='calculated' else 'blocked_missing_baseline','deliverables':['Validate data definitions and measurement-system adequacy.','Reconcile supplied defect flags with specification-derived flags.'],'exit_evidence':['Data collection plan','MSA acceptance','Stable baseline with documented sampling']},
        {'phase':'Analyze','status':'hypotheses_to_verify' if association or pareto else 'blocked_insufficient_data','deliverables':['Use Pareto to prioritize observed failure modes.','Test association hypotheses with a designed follow-up; do not infer causation from this synthetic observational dataset.'],'exit_evidence':['Verified cause(s) with evidence','Confounder review','Replicated or designed test']},
        {'phase':'Improve','status':'pilot_available_for_review' if pilot else 'blocked_no_pilot_data','deliverables':['Review 2×2 pilot main effects and interaction if supplied.','Predefine success criteria, guardrails, randomization, and rollback before production deployment.'],'exit_evidence':['Approved pilot protocol','Pilot results reviewed','Process owner sign-off']},
        {'phase':'Control','status':'monitoring_data_available' if pchart else 'blocked_no_subgroup_monitoring','deliverables':['Review p-chart signals and confirm subgroup logic.','Set owner, frequency, reaction plan, escalation path, and sustained follow-up.'],'exit_evidence':['Approved control plan','SPC reaction plan','Sustained post-change evidence']},
    ]
    report = {'report_type':'evidence-led_dmaic_case_review','report_version':'2.0','run_id':None,'status':'completed_with_limitations','prompt':prompt,'embedded_task_prompt_sources':[name for name in documents if any(token in name.lower() for token in ('prompt','task','instruction','brief'))],'uploaded_task_instructions':[{'source':name,'text':text[:10000]} for name,text in documents.items() if any(token in name.lower() for token in ('prompt','task','instruction','brief'))],
              'data_provenance':{'synthetic_data_detected':any('synthetic' in (documents.get(n,'').lower()) for n in documents) or any('manifest.json' in n.lower() and isinstance(obj, dict) and bool(obj.get('synthetic_data')) for n,obj in structured.items()) or bool(charter and 'synthetic' in str(charter.get('dataset_status','')).lower()),
                                 'specification_limits':{'lsl':lsl,'target':target,'usl':usl,'source':spec_source},'files_received':len(files),'parsed_data_tables':len(dataframes),'documents_indexed':len(documents),
                                 'file_inventory':inventory,'datasets':quality,'shared_unit_id_audit':id_overlap_audit,'document_sources':[{'filename':n,'characters_indexed':len(t),'excerpt':t[:500]} for n,t in documents.items()]},
              'project_charter':deliverables,'baseline_analysis':baseline,'data_dictionary':{'source':dictionary_name,'fields':data_dictionary_entries} if data_dictionary_entries else None,'calculation_validation':calculation_validation,'charter_assumption_validation':charter_assumption_validation,'pareto':pareto,'production_context_associations':association,'measurement_system_analysis':gauge,'improvement_pilot':pilot,
              'post_improvement_analysis':post,'pre_post_comparison':comparisons,'process_control':pchart,'financial_forecast':financial,'dmaic_phase_review':phase_status,
              'verified_findings':findings,'root_cause_hypotheses':hypothesis_candidates,
              'fishbone_hypotheses':{'status':'hypothesis_generation_only','categories':{'Machine':['Tool wear / machine condition; validate using tool-age and maintenance logs.'],'Method':['Setup check and offset-verification adherence; verify with direct observation and randomized/controlled study.'],'Measurement':['Gauge variation / calibration; use Gauge R&R and calibration evidence.'],'Material':['Material lot variation; no supplier/material study supplied.'],'People':['Training or shift handover variation; avoid individual blame and investigate system conditions.'],'Environment':['Coolant temperature or ambient conditions; verify with measured time-aligned data.']},'note':'These are investigation prompts, not findings of cause.'},
              'recommended_actions':[{'priority':'P0','phase':'Measure','owner':'Metrology Engineer + Statistician','action':'Validate crossed Gauge R&R and measurement procedure; confirm parts span the operating range.','completion_evidence':'Signed MSA result and calibration status.'},
                                    {'priority':'P0','phase':'Measure','owner':'Data Analyst + Process Owner','action':'Reconcile defect flags to LSL/USL and resolve any mismatches before capability or benefit claims.','completion_evidence':'Row-level discrepancy disposition and approved operational definition.'},
                                    {'priority':'P1','phase':'Analyze','owner':'Quality Engineer','action':'Investigate the top Pareto failure modes and strongest associations; check confounding and verify with a planned test.','completion_evidence':'Root-cause verification record, not only a fishbone or p-value.'},
                                    {'priority':'P1','phase':'Improve','owner':'Process Owner + Manufacturing Engineer','action':'Review the 2×2 pilot and run a prospective, adequately powered confirmation pilot before production-wide rollout.','completion_evidence':'Predefined acceptance criteria, guardrails, and independent confirmation.'},
                                    {'priority':'P1','phase':'Control','owner':'Control Plan Owner','action':'Review p-chart and create a reaction plan for out-of-control signals; keep specification and control limits distinct.','completion_evidence':'Approved control plan, response owner, sampling cadence, and sustained monitoring.'},
                                    {'priority':'P2','phase':'Finance','owner':'Finance Representative','action':'Validate forecast assumptions and reconcile any benefit to actual scrap/rework records after implementation.','completion_evidence':'Finance-approved realized-benefit calculation.'}],
              'limitations':limitations,'evidence_policy':'Numbers in statistical sections are deterministic calculations from supplied files. Recommendations and fishbone items are not facts. No causal claim or realized savings claim is made.'}
    return report


def report_to_markdown(report: dict) -> str:
    def fmt(v):
        if v is None: return 'Not available'
        if isinstance(v, float): return f'{v:.6g}'
        return str(v)
    base = report.get('baseline_analysis') or {}
    summary_lines = []
    if base.get('status') == 'calculated':
        spec = base.get('specification') or {}
        summary_lines.append(f"- **Baseline:** n={base.get('n_valid')}; mean={base.get('mean')}; sample SD={base.get('sample_std_dev')}; observed defects={spec.get('out_of_spec_count')}/{spec.get('n')} ({spec.get('defect_rate')}).")
        summary_lines.append(f"- **Measurement and capability status:** {base.get('capability',{}).get('status')}; normality diagnostic p={base.get('normality_diagnostic',{}).get('p_value')}; capability remains provisional until stability and MSA are accepted.")
        summary_lines.append(f"- **Defect flag check:** {(base.get('defect_flag_validation') or {}).get('status')}; independent arithmetic cross-check status: {(report.get('calculation_validation') or {}).get('status')} ({(report.get('calculation_validation') or {}).get('failed_check_count',0)} failed checks).")
    comparison = report.get('pre_post_comparison')
    if comparison:
        summary_lines.append(f"- **Pre/post sample comparison:** defect rate {comparison['baseline_defect_rate']:.1%} → {comparison['post_defect_rate']:.1%}; reduction {comparison['absolute_reduction_percentage_points']} percentage points; {comparison['test_name']} p={comparison['test_p_value']}. This is not causal proof.")
    pilot_result = report.get('improvement_pilot') or {}
    if pilot_result.get('status') == 'calculated':
        pvals = [v.get('p_value') for v in pilot_result.get('effects',{}).values() if v.get('p_value') is not None]
        summary_lines.append(f"- **Pilot:** 2×2 factorial model analyzed; effect p-values={', '.join(f'{x:.4g}' for x in pvals)}. Review confidence intervals, practical size, design, and residual assumptions before selecting a change.")
    control = report.get('process_control') or {}
    if control.get('status') == 'calculated': summary_lines.append(f"- **Control:** {control.get('subgroups')} subgroups and {control.get('total_units')} units; {len(control.get('signals_outside_limits',[]))} points beyond 3-sigma p-chart limits. This is not a full run-rule analysis.")
    finance = report.get('financial_forecast') or {}
    if finance.get('status') == 'forecast_only': summary_lines.append(f"- **Financial scenario:** gross annual savings forecast ₹{finance.get('gross_annual_savings_forecast_inr'):,.0f}; forecast only, not realized or Finance-approved.")
    if report['data_provenance'].get('synthetic_data_detected'): summary_lines.append('- **Provenance warning:** supplied evidence identifies the data as synthetic; findings are a software validation exercise, not actual operating performance.')
    lines = [f"# SigmaPilot DMAIC Case Report — {report.get('project_charter',{}).get('title','Project review')}", '', f"**Run ID:** {report.get('run_id') or 'pending'}", f"**Status:** {report.get('status')}", '', '## Executive summary',
             *summary_lines, '', 'This report uses deterministic calculations for metrics. Hypotheses, assumptions, recommendations, and forecasts are explicitly separated. Human process, quality, and finance owners must validate before operational decisions.', '',
             '## 1. Evidence pack and provenance', f"- Files received: {report['data_provenance']['files_received']}", f"- Parsed data tables: {report['data_provenance']['parsed_data_tables']}", f"- Reference documents indexed: {report['data_provenance']['documents_indexed']}", f"- Synthetic data detected: {report['data_provenance']['synthetic_data_detected']}", f"- Specification limits: LSL={report['data_provenance']['specification_limits']['lsl']}, target={report['data_provenance']['specification_limits']['target']}, USL={report['data_provenance']['specification_limits']['usl']} (source: {report['data_provenance']['specification_limits']['source']})", '', '### File inventory']
    for f in report['data_provenance']['file_inventory']:
        lines.append(f"- **{f['filename']}** — {f.get('dmaic_role','Role not mapped')} · {f['kind']} / {f['status']}; {f.get('rows','?')} rows; {f.get('error','')}")
    if report.get('uploaded_task_instructions'):
        lines += ['', '### Uploaded task instructions considered']
        for task in report['uploaded_task_instructions']:
            lines += [f"#### Source: {task['source']}", task['text'], '']
    c = report['project_charter']; lines += ['', '## 2. Define — charter, VOC/CTQ, and SIPOC', f"- **Problem:** {c['problem_statement']}", f"- **Goal:** {c['goal_statement']}", f"- **Primary metric:** {c['primary_metric']}", f"- **Scope in:** {', '.join(c['scope_in']) or 'Needs confirmation'}", f"- **Scope out:** {', '.join(c['scope_out']) or 'Needs confirmation'}", f"- **CTQ:** {json.dumps(c['ctq'], ensure_ascii=False)}", '', '### SIPOC (draft; confirm with process owner)']
    for k,v in c['sipoc'].items(): lines.append(f"- **{k.title()}:** {', '.join(v)}")
    lines += ['', '### VOC / CTQ note', c['voc_ctq']['voice_of_customer'], '', '## 3. Measure — baseline and data validity']
    b = report['baseline_analysis']
    if b.get('status') == 'calculated':
        lines += [f"- Source: `{b['source']}`; outcome: `{b['outcome_column']}`", f"- Valid n: {b['n_valid']} of {b['n_rows']} rows; invalid/missing: {b['invalid_or_missing_measurements']}", f"- Mean: {b['mean']}; median: {b['median']}; sample SD: {b['sample_std_dev']}; min/max: {b['min']} / {b['max']}", f"- Observed out-of-spec: {b['specification'].get('out_of_spec_count')} / {b['specification'].get('n')} ({b['specification'].get('defect_rate')}); yield: {b['specification'].get('yield')}; 95% Wilson CI for defect rate: {b['specification'].get('defect_ci_95')}", f"- Defect-flag reconciliation: {json.dumps(b['defect_flag_validation'], ensure_ascii=False)}", f"- DPMO with one CTQ opportunity per unit: {b.get('dpmo_one_opportunity_per_unit')} (assumption: {b.get('dpmo_assumption')})", f"- Yield-based sigma benchmark with +1.5 shift: {b.get('sigma_benchmark_with_1_5_shift')} ({b.get('sigma_benchmark_status')})", f"- Capability: {json.dumps(b.get('capability'), ensure_ascii=False)}", f"- Normality diagnostic: {json.dumps(b.get('normality_diagnostic'), ensure_ascii=False)}"]
    else: lines.append(f"- Baseline blocked: {b.get('reason')}")
    if report['data_provenance'].get('shared_unit_id_audit'):
        lines += ['', '### Shared unit-ID / overlap audit']
        for item in report['data_provenance']['shared_unit_id_audit']:
            lines.append(f"- `{item['dataset_a']}` ↔ `{item['dataset_b']}`: {item['shared_id_count']} shared IDs ({item['shared_fraction_of_smaller_dataset']:.1%} of smaller ID set). {item['warning']}")
    lines += ['', '### Data quality by dataset']
    for q in report['data_provenance']['datasets']:
        lines.append(f"- `{q['source']}`: {q['rows']} rows, {q['columns']} columns, {q['duplicate_rows']} duplicate rows, {q['missing_cells']} missing cells. {' '.join(q['warnings'])}")
    if report.get('data_dictionary'):
        lines += ['', '### Data dictionary used']
        for item in report['data_dictionary']['fields']:
            lines.append(f"- `{item['field']}` — {item['definition']} ({item['unit_or_values']})")
    cv = report.get('calculation_validation') or {}
    lines += ['', '### Independent calculation cross-check', f"- Status: {cv.get('status')}; {cv.get('check_count',0)} checks; {cv.get('failed_check_count',0)} failed."]
    for check in cv.get('checks', []):
        lines.append(f"- **{check['metric']} — {check['status']}** from `{check['source']}`: reported={check.get('reported')}; recomputed={check.get('independently_recomputed')}; difference={check.get('absolute_difference')}")
    lines += ['', '### Charter assumption reconciliation', json.dumps(report.get('charter_assumption_validation'), ensure_ascii=False, indent=2), '', '## 4. Analyze — Pareto and cause hypotheses', '### Pareto of observed failure modes']
    if report['pareto']:
        for p in report['pareto']: lines.append(f"- {p['category']}: {p['count']} ({p['share']:.1%}); cumulative {p['cumulative_share']:.1%}")
    else: lines.append('- No defect-type field found; Pareto not calculated.')
    lines += ['', '### Exploratory association screens']
    for a in report['production_context_associations'][:15]: lines.append(f"- **{a['factor']}** — {a['test']}; p={a.get('p_value')}; {a.get('interpretation')}")
    if not report['production_context_associations']: lines.append('- No suitable defect/context columns found for association screening.')
    lines += ['', '### Fishbone hypotheses (not established causes)']
    for k,v in report['fishbone_hypotheses']['categories'].items(): lines.append(f"- **{k}:** {'; '.join(v)}")
    lines += ['', '### Root-cause hypotheses from data']
    for h in report['root_cause_hypotheses']: lines.append(f"- {h['hypothesis']} {h['evidence']} Status: hypothesis only.")
    if not report['root_cause_hypotheses']: lines.append('- No candidate passed the simple exploratory p<0.05 screen, or no suitable associations were calculated. This does not prove no cause exists.')
    lines += ['', '## 5. Measurement system — Gauge R&R']
    g = report.get('measurement_system_analysis')
    lines.append(json.dumps(g, ensure_ascii=False, indent=2) if g else 'No Gauge R&R dataset was identified.')
    lines += ['', '## 6. Improve — factorial pilot']
    lines.append(json.dumps(report.get('improvement_pilot'), ensure_ascii=False, indent=2) if report.get('improvement_pilot') else 'No pilot dataset was identified.')
    lines += ['', '## 7. Verify improvement — post-change comparison']
    lines.append(json.dumps(report.get('post_improvement_analysis'), ensure_ascii=False, indent=2) if report.get('post_improvement_analysis') else 'No post-improvement dataset was identified.')
    lines.append(json.dumps(report.get('pre_post_comparison'), ensure_ascii=False, indent=2) if report.get('pre_post_comparison') else 'A pre/post comparison could not be calculated.')
    lines += ['', '## 8. Control — p-chart', json.dumps(report.get('process_control'), ensure_ascii=False, indent=2) if report.get('process_control') else 'No subgroup data found for a p-chart.', '', '## 9. Financial case (forecast, not realized)', json.dumps(report.get('financial_forecast'), ensure_ascii=False, indent=2) if report.get('financial_forecast') else 'No financial model was supplied.', '', '## 10. DMAIC stage gates']
    for g in report['dmaic_phase_review']:
        lines += [f"### {g['phase']} — {g['status']}"] + [f"- Deliverable: {x}" for x in g['deliverables']] + [f"- Exit evidence: {x}" for x in g['exit_evidence']]
    lines += ['', '## 11. Prioritized action plan']
    for a in report['recommended_actions']: lines += [f"### {a['priority']} · {a['phase']} · {a['owner']}", a['action'], f"**Closure evidence:** {a['completion_evidence']}", '']
    synthesis = report.get('ai_team_synthesis') or {}
    lines += ['', '## 12. AI-assisted team synthesis (advisory, not evidence)', f"- Provider status: {synthesis.get('provider_status', 'not run')}", synthesis.get('summary', 'No AI synthesis attached.') ]
    for section in ('hypotheses', 'recommendations', 'risks'):
        lines += ['', f'### AI {section.title()}'] + [f'- {x}' for x in synthesis.get(section, [])]
    lines += ['', '## 13. Limitations and assumptions'] + [f'- {x}' for x in report['limitations']] + ['', '## Evidence discipline', report['evidence_policy'], '']
    return '\n'.join(lines)
