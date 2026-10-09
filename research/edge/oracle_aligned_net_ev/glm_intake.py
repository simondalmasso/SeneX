"""Strict original CSV loader; derived explicit correction never overwrites bytes."""
import csv
import hashlib
import io

class IntakeError(ValueError):pass

def _original_table(raw):
    if type(raw) is not bytes or not raw:raise IntakeError('no original bytes')
    try:
        text=raw.decode('utf-8-sig',errors='strict')
        rows=list(csv.reader(io.StringIO(text,newline=''),strict=True))
    except (UnicodeDecodeError,csv.Error) as exc:
        raise IntakeError('unparseable original CSV') from exc
    if len(rows)<2 or len(set(rows[0]))!=len(rows[0]):raise IntakeError('missing/duplicate header')
    return rows

def parse_glm_csv(raw):
    rows=_original_table(raw)
    if any(len(row)!=len(rows[0]) for row in rows[1:]):
        raise IntakeError('CSV_HEADER_12_ROW_11: reject original, do not shift fields')
    return [dict(zip(rows[0],row)) for row in rows[1:]]

def derived_fixed_csv(raw,*,missing_column,insert_value):
    rows=_original_table(raw)
    header=rows[0]
    if missing_column not in header or not insert_value or len(header)!=12 or len(rows)<2:
        raise IntakeError('ambiguous correction requires explicit source column')
    idx=header.index(missing_column)
    if any(len(row)!=11 for row in rows[1:]):
        raise IntakeError('not the approved 12-vs-11 mismatch')
    transformed=[header]+[row[:idx]+[insert_value]+row[idx:] for row in rows[1:]]
    f=io.StringIO(newline='');csv.writer(f,lineterminator='\n').writerows(transformed)
    derived=f.getvalue().encode('utf8')
    assert len(parse_glm_csv(derived))==len(rows)-1
    return {'original_bytes':raw,'original_sha256':hashlib.sha256(raw).hexdigest(),
        'derived_bytes':derived,'derived_sha256':hashlib.sha256(derived).hexdigest(),
        'transformation':'INSERT_EXPLICIT_MISSING_SOURCE_COLUMN',
        'scientific_authorized':False,'original_retained':True,
        'provenance':'SYNTHETIC_EXAMPLE_UNLESS_ORIGINAL_SUPPLIED'}

def evaluate_glm_claims(claims):
    return {'historical_order099':'REFUTED_ISSUE_139',
         'glm_r4':'UNREPRODUCED','glm_hypotheses':'EXPLORATORY_ONLY',
         'hypothesis_families':['PDF_M1-M5','R9_M1-M5'],
         'twap_delta_units':'PRICE_NOT_REVERSAL_PROBABILITY',
         'regimes':'SEPARATE_SNAPSHOT_TWAP30_TWAP60',
         'feynman':'NO_VERIFIED_NATIVE_PAPER_READS',
         'primary_comparators':['A_NO_TRADE','B_MARKET_PRIOR','C_SENEX_CALIBRATED'],
         'trial_ledger_policy':'REGISTER_EVERY_VARIANT_NO_POST_HOC_WINNER',
         'source_admissible':False,'cohort_authorized':False}
