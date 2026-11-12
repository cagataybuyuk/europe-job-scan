const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const {spawnSync} = require('node:child_process');

const ROOT = path.resolve(__dirname, '../..');
const SHA = 'a'.repeat(40);
const NOW = '2026-11-12T22:30:00.000Z';
const canonical = value => JSON.stringify(value, function(key, item) {
  if (item && typeof item === 'object' && !Array.isArray(item)) {
    return Object.keys(item).sort().reduce((out, name) => {out[name] = item[name]; return out;}, {});
  }
  return item;
});
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const isoDate = value => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Istanbul', year: 'numeric', month: '2-digit', day: '2-digit'
}).format(value);

function createHarness() {
  const state = {lock: false, writes: [], flushes: 0, failAt: null, afterFlush: null,
    rows: [], formulas: {}, allowed: ['To Apply', 'Applied', 'Rejected'],
    allowInvalid: false, validationType: 'VALUE_IN_LIST', spreadsheetTitle: 'TEST_EU_Job_Tracker',
    spreadsheetId: 'synthetic_test_tracker_123456789', zone: 'Europe/Istanbul', sheetId: 77,
    props: new Map(), maxColumns: 24};
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [NOW])); }
    static now() { return Date.parse(NOW); }
  }
  const sheet = {
    getSheetId: () => state.sheetId,
    getLastRow: () => state.rows.length,
    getMaxColumns: () => state.maxColumns,
    getRange(row, column, rowCount = 1, columnCount = 1) {
      return {
        getValues: () => Array.from({length: rowCount}, (_, r) => Array.from({length: columnCount}, (_, c) =>
          state.rows[row - 1 + r]?.[column - 1 + c] ?? '')),
        getFormulas: () => Array.from({length: rowCount}, (_, r) => Array.from({length: columnCount}, (_, c) =>
          state.formulas[`${row + r}:${column + c}`] ?? '')),
        getDataValidation: () => state.validationType === null ? null : ({
          getCriteriaType: () => state.validationType,
          getCriteriaValues: () => [state.allowed, true],
          getAllowInvalid: () => state.allowInvalid
        }),
        setValue(value) {
          assert.equal(state.lock, true, 'all mutations require the same project lock');
          if (state.failAt === state.writes.length + 1) throw Error('PRIVATE simulated storage failure');
          state.writes.push({row, column, value: value instanceof Date ? isoDate(value) : value});
          state.rows[row - 1][column - 1] = value;
        }
      };
    }
  };
  const spreadsheet = {
    getId: () => state.spreadsheetId,
    getName: () => state.spreadsheetTitle,
    getSpreadsheetTimeZone: () => state.zone,
    getSheetByName: name => name === 'Applications' ? sheet : null
  };
  const ctx = vm.createContext({Date: Clock, console, Buffer,
    EJS_GH_DEPLOYED_SOURCE_SHA_V1: SHA,
    SpreadsheetApp: {
      openById: id => {assert.equal(id, state.spreadsheetId); return spreadsheet;},
      flush: () => {state.flushes++; if (state.afterFlush) state.afterFlush();},
      DataValidationCriteria: {VALUE_IN_LIST: 'VALUE_IN_LIST'}
    },
    LockService: {getScriptLock: () => ({
      tryLock: () => {if (state.lock) return false; state.lock = true; return true;},
      releaseLock: () => {state.lock = false;}
    })},
    PropertiesService: {getScriptProperties: () => ({getProperty: key => state.props.get(key) ?? null})},
    Utilities: {DigestAlgorithm: {SHA_256: 'sha256'}, Charset: {UTF_8: 'utf8'},
      computeDigest: (algorithm, input) => [...crypto.createHash('sha256').update(input).digest()]
        .map(value => value > 127 ? value - 256 : value),
      formatDate: (value, zone, format) => {assert.equal(zone, 'Europe/Istanbul'); assert.equal(format, 'yyyy-MM-dd'); return isoDate(value);}
    }
  });
  const gs = name => fs.readFileSync(path.join(ROOT, 'google_native/apps_script_zero_cost', name), 'utf8');
  const config = gs('EjsConfigZeroCostV1.gs')
    .replace(/TEST_SPREADSHEET_ID: '[^']*'/, `TEST_SPREADSHEET_ID: '${state.spreadsheetId}'`)
    .replace(/PROD_SPREADSHEET_ID: '[^']*'/, "PROD_SPREADSHEET_ID: 'synthetic_prod_DENIED_123456789'");
  vm.runInContext(config, ctx);
  vm.runInContext(gs('EjsGitHubGatewayV1.gs'), ctx);
  vm.runInContext(gs('EjsCentralOutcomeTestV1.gs'), ctx);
  const headers = JSON.parse(vm.runInContext('JSON.stringify(EJS_CENTRAL_TEST_V1.HEADERS)', ctx));
  const binding = {
    spreadsheet_id: state.spreadsheetId, sheet_id: 77, row_number: 2, headers,
    company: 'EJS Synthetic Fixture', role: 'Central Outcome Synthetic Analyst', country: 'TEST',
    tracked_url: 'https://fixture.invalid/central-outcome',
    opportunity_key: 'ejs-synthetic-fixture:central-outcome-synthetic-analyst:test',
    posting_url: 'https://jobs.smartrecruiters.com/Fixture/123456789012345--synthetic',
    requisition_id: 'SYNTHETIC-REF', candidate_profile_version: 'synthetic:v1',
    submit_key: 'submit:ejs-synthetic-fixture-central-outcome-synthetic-analyst-test:synthetic-ref:synthetic-v1',
    execution_id: 'exec:synthetic:fixture', evidence_ref: 'evidence:sha256:' + 'b'.repeat(64),
    source_sha: SHA, reviewed_at: NOW, sheet_name: 'Applications',
    time_zone: 'Europe/Istanbul', confirmation_kind: 'ATS_ACKNOWLEDGEMENT'
  };
  const review = {schema_version: 'EJS-CENTRAL-OUTCOME-TEST-1', enabled: true, synthetic: true,
    source_sha: SHA, binding, binding_sha256: hash(canonical(binding)), confirmation: {
      status: 'Confirmed', synthetic: true, confirmed_at: '2026-11-12T22:29:59.000Z',
      binding_sha256: hash(canonical(binding)), submit_key: binding.submit_key,
      execution_id: binding.execution_id, requisition_id: binding.requisition_id, evidence_ref: binding.evidence_ref
    }};
  function pinReview() {
    state.props.set('EJS_CENTRAL_OUTCOME_TEST_REVIEW_V1', canonical(review));
    state.props.set('EJS_CENTRAL_OUTCOME_TEST_REVIEW_SHA256_V1', hash(canonical(review)));
  }
  pinReview();
  const row = Array(24).fill('');
  Object.assign(row, {0: '2026-11-12', 1: binding.company, 2: binding.role, 3: binding.country,
    5: binding.tracked_url, 7: 'Unknown', 9: 'To Apply', 17: 'SYNTHETIC preserved notes', 23: '2026-11-12'});
  state.rows = [[...headers], row];
  const plain = value => JSON.parse(JSON.stringify(value));
  const read = () => plain(ctx.ejsCentralOutcomeTestReadV1_(binding));
  function request(snapshot = read()) {
    return {spreadsheet_id: binding.spreadsheet_id, sheet_id: binding.sheet_id, sheet_name: binding.sheet_name,
      row_number: binding.row_number, identity_key: [binding.company, binding.role, binding.country],
      operation_key: hash(canonical(['EJS-CENTRAL-APPLICATION-1', hash(canonical(binding)), binding.evidence_ref, '2026-11-13'])),
      expected_fingerprint: hash(canonical([snapshot.spreadsheet_id, snapshot.sheet_id, snapshot.sheet_name,
        snapshot.headers, snapshot.status_allowed_values, snapshot.identity_matches.map(row => [row.row_number, row.cells])])),
      patches: [{column: 'J', row: 2, value: 'Applied'}, {column: 'K', row: 2, value: '2026-11-13'},
        {column: 'X', row: 2, value: '2026-11-13'}]};
  }
  const cas = value => plain(ctx.ejsCentralOutcomeTestCasV1_(value));
  const capabilities = () => plain(ctx.ejsCentralOutcomeTestCapabilitiesV1_());
  return {state, binding, review, pinReview, read, request, cas, capabilities};
}

if (process.argv.includes('--bridge')) {
  // A single process owns an isolated synthetic Sheet; no network/credentials.
  const h = createHarness();
  require('node:readline').createInterface({input: process.stdin}).on('line', line => {
    try {
      const command = JSON.parse(line);
      let value;
      if (command.action === 'config') value = {binding: h.binding, now: NOW, confirmation: h.review.confirmation};
      else if (command.action === 'read') value = h.read();
      else if (command.action === 'cas') value = h.cas(command.request);
      else if (command.action === 'state') value = {writes: h.state.writes, lock: h.state.lock, flushes: h.state.flushes};
      else throw Error('unsupported');
      process.stdout.write(JSON.stringify({ok: true, value}) + '\n');
    } catch (error) {
      process.stdout.write(JSON.stringify({ok: false, error: 'CENTRAL_TEST_BRIDGE_REJECTED'}) + '\n');
    }
  });
} else {
  test('actual owner-callable TEST service writes only K/X/J under lock and verifies readback', () => {
    const h = createHarness(); const before = [...h.state.rows[1]];
    const request = h.request(); const receipt = h.cas(request);
    assert.deepEqual(receipt, {operation_key: request.operation_key, comparison_succeeded: true, wrote: true});
    assert.deepEqual(h.state.writes.map(write => [write.row, write.column]), [[2, 11], [2, 24], [2, 10]]);
    assert.equal(h.state.flushes, 1); assert.equal(h.state.lock, false);
    const after = h.read().identity_matches[0].cells;
    assert.equal(after[9], 'Applied'); assert.equal(after[10], '2026-11-13'); assert.equal(after[23], '2026-11-13');
    assert.ok(before.every((value, index) => [9, 10, 23].includes(index) || value === after[index]));
    assert.equal(h.cas(request).comparison_succeeded, false); assert.equal(h.state.writes.length, 3);
  });

  test('cooperative lock contention prevents every cell write', () => {
    const h = createHarness(); const request = h.request(); h.state.lock = true;
    assert.throws(() => h.cas(request), /CENTRAL_TEST_SYNC_LOCKED/); assert.equal(h.state.writes.length, 0);
  });

  for (const kind of ['duplicate', 'missing', 'moved', 'url', 'notes', 'Rejected', 'headers', 'validation', 'formula']) {
    test(`complete locked rescan rejects ${kind} drift before first write`, () => {
      const h = createHarness(); const request = h.request();
      if (kind === 'duplicate') h.state.rows.push([...h.state.rows[1]]);
      if (kind === 'missing') h.state.rows[1][1] = 'Other';
      if (kind === 'moved') {h.state.rows.push([...h.state.rows[1]]); h.state.rows[1][1] = 'Other';}
      if (kind === 'url') h.state.rows[1][5] = 'https://fixture.invalid/other';
      if (kind === 'notes') h.state.rows[1][17] = 'SYNTHETIC changed';
      if (kind === 'Rejected') h.state.rows[1][9] = 'Rejected';
      if (kind === 'headers') [h.state.rows[0][9], h.state.rows[0][10]] = [h.state.rows[0][10], h.state.rows[0][9]];
      if (kind === 'validation') h.state.allowed = ['To Apply', 'Rejected'];
      if (kind === 'formula') h.state.formulas['2:10'] = '=SYNTHETIC';
      try {assert.equal(h.cas(request).comparison_succeeded, false);} catch (error) {assert.match(error.message, /^CENTRAL_TEST_SYNC_REJECTED$/);}
      assert.equal(h.state.writes.length, 0); assert.equal(h.state.lock, false);
    });
  }

  for (const mutate of [
    h => {h.review.enabled = false;}, h => {h.review.synthetic = false;},
    h => {h.review.source_sha = 'c'.repeat(40);}, h => {h.review.binding.company = 'Real Employer';},
    h => {h.review.confirmation.status = 'Submitted / Unconfirmed';},
    h => {h.review.confirmation.evidence_ref = 'evidence:sha256:' + 'c'.repeat(64);},
    h => {h.review.binding.reviewed_at = '2026-11-12T22:00:00.000Z';},
    h => {h.review.confirmation.confirmed_at = '2026-11-13T22:00:00.000Z';},
    h => {h.review.confirmation.confirmed_at = '2026-02-30T22:00:00.000Z';},
    h => {h.review.confirmation.confirmed_at = '2026-11-12T23:29:59.000+01:00';}
  ]) {
    test('owner property pin does not bypass TEST, synthetic, freshness or confirmation gates', () => {
      const h = createHarness(); const request = h.request(); mutate(h); h.pinReview();
      assert.throws(() => h.cas(request), /^Error: CENTRAL_TEST_SYNC_REJECTED$/);
      assert.equal(h.state.writes.length, 0); assert.equal(h.state.lock, false);
    });
  }

  test('property tamper without external pin and changed TEST title fail closed', () => {
    for (const mutate of [h => h.state.props.set('EJS_CENTRAL_OUTCOME_TEST_REVIEW_SHA256_V1', '0'.repeat(64)),
      h => {h.state.spreadsheetTitle = 'EU Job Tracker';}, h => {h.state.zone = 'UTC';},
      h => {h.state.allowInvalid = true;}]) {
      const h = createHarness(); const request = h.request(); mutate(h);
      assert.throws(() => h.cas(request), /CENTRAL_TEST_SYNC_REJECTED/); assert.equal(h.state.writes.length, 0);
    }
  });

  test('extra write cell, wrong row/identity/operation and wrong dates never reach Sheets', () => {
    for (const mutate of [r => r.patches.push({column: 'R', row: 2, value: 'SYNTHETIC forbidden'}),
      r => {r.row_number = 3;}, r => {r.identity_key[0] = 'Other';}, r => {r.operation_key = '0'.repeat(64);},
      r => {r.patches[1].value = '2026-01-01';}]) {
      const h = createHarness(); const request = h.request(); mutate(request);
      assert.throws(() => h.cas(request), /CENTRAL_TEST_SYNC_REJECTED/); assert.equal(h.state.writes.length, 0);
    }
  });

  for (const failAt of [1, 2, 3]) {
    test(`storage failure at write ${failAt} is sanitized and cannot mark Applied or auto-repair`, () => {
      const h = createHarness(); const request = h.request(); h.state.failAt = failAt;
      assert.throws(() => h.cas(request), /^Error: CENTRAL_TEST_SYNC_REJECTED$/);
      assert.equal(h.state.writes.length, failAt - 1); assert.equal(h.state.rows[1][9], 'To Apply');
      assert.equal(h.state.rows[1][17], 'SYNTHETIC preserved notes'); assert.equal(h.state.lock, false);
      if (failAt > 1) {
        assert.equal(h.cas(request).comparison_succeeded, false);
        assert.equal(h.state.writes.length, failAt - 1);
      }
    });
  }

  test('postflush later outcome is detected and never repaired to Applied', () => {
    const h = createHarness(); const request = h.request(); h.state.afterFlush = () => {h.state.rows[1][9] = 'Rejected';};
    assert.throws(() => h.cas(request), /CENTRAL_TEST_SYNC_REJECTED/);
    assert.equal(h.state.rows[1][9], 'Rejected'); assert.equal(h.state.writes.length, 3); assert.equal(h.state.lock, false);
  });

  test('no web gateway operation exposes this owner-only synthetic transport', () => {
    const gateway = fs.readFileSync(path.join(ROOT, 'google_native/apps_script_zero_cost/EjsGitHubGatewayV1.gs'), 'utf8');
    assert.ok(!gateway.includes('ejsCentralOutcomeTest'));
    const source = fs.readFileSync(path.join(ROOT, 'google_native/apps_script_zero_cost/EjsCentralOutcomeTestV1.gs'), 'utf8');
    assert.ok(!source.includes('function doPost'));
    assert.ok(!source.includes('UrlFetchApp'));
    assert.ok(!source.includes('insertRow') && !source.includes('appendRow') && !source.includes('deleteRow'));
    assert.deepEqual(createHarness().capabilities(), {environment: 'TEST', synthetic_only: true,
      lock_scope: 'same_apps_script_project_cooperative_writers', atomic_row_compare_and_set: false,
      globally_atomic: false, rollback_supported: false, live_transport_implemented: false});
  });

  test('actual Python confirmed ledger and sync service cross the real V8 TEST transport once', () => {
    const code = String.raw`
import json, os, pathlib, subprocess, sys, tempfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from ejs.domain.submission import ConfirmationEvidence, ConfirmationType, SubmissionAttempt, SubmissionAttemptStatus
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.central_application_outcome import CentralApplicationBinding, CentralApplicationRead, CentralApplicationRow, CentralApplicationReceipt, central_binding_sha256, sync_confirmed_application
p = subprocess.Popen(['node', sys.argv[1], '--bridge'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def call(action, **extra):
    p.stdin.write(json.dumps(dict(action=action, **extra)) + '\n'); p.stdin.flush()
    result = json.loads(p.stdout.readline())
    assert result['ok'], result
    return result['value']
try:
    config = call('config'); raw = config['binding']; raw['headers'] = tuple(raw['headers'])
    binding = CentralApplicationBinding(**raw); now = datetime.fromisoformat(config['now'].replace('Z', '+00:00'))
    class IsolatedSyntheticTransport:
        # This process owns the ONLY fake Sheet. Never a real Sheets capability.
        atomic_row_compare_and_set = True
        def read_bound_rows(self, _):
            read = call('read'); read['headers'] = tuple(read['headers']); read['status_allowed_values'] = tuple(read['status_allowed_values'])
            read['identity_matches'] = tuple(CentralApplicationRow(row['row_number'], tuple(row['cells'])) for row in read['identity_matches'])
            return CentralApplicationRead(**read)
        def compare_and_set(self, request):
            return CentralApplicationReceipt(**call('cas', request=asdict(request)))
    with tempfile.TemporaryDirectory() as directory:
        ledger = SqliteSubmissionLedger(pathlib.Path(directory).resolve() / 'private' / 'fixture.sqlite3')
        attempt = SubmissionAttempt(binding.submit_key, binding.execution_id, binding.opportunity_key, binding.requisition_id, binding.candidate_profile_version, 'SUBMIT-1.0', 'validation:synthetic', (now - timedelta(seconds=3)).isoformat(), SubmissionAttemptStatus.VALIDATED, ats_family='smartrecruiters', form_fingerprint='form:synthetic')
        assert ledger.reserve_intent(attempt, now - timedelta(seconds=3))
        ledger.mark_started(binding.submit_key, binding.execution_id, now - timedelta(seconds=2))
        evidence = ConfirmationEvidence(binding.submit_key, ConfirmationType.ATS_ACKNOWLEDGEMENT, binding.evidence_ref, (now - timedelta(seconds=1)).isoformat(), True, binding.execution_id, binding.requisition_id)
        assert ledger.confirm(binding.submit_key, evidence, now - timedelta(seconds=1))
        kwargs = dict(ledger=ledger, binding=binding, binding_sha256=central_binding_sha256(binding), reviewed_source_sha=binding.source_sha, transport=IsolatedSyntheticTransport(), now=now, write_enabled=True)
        result = sync_confirmed_application(**kwargs)
        assert result['status'] == 'CENTRAL_RECORDED' and result['central_readback_verified'], result
        assert sync_confirmed_application(**kwargs)['status'] == 'ALREADY_RECORDED'
        state = call('state'); assert len(state['writes']) == 3 and not state['lock'] and state['flushes'] == 1, state
        assert ledger.snapshot()['status'] == 'Confirmed'
        print('actual Python/V8 synthetic synchronization passed')
finally:
    p.stdin.close(); p.wait(timeout=5)
`;
    const result = spawnSync(process.env.EJS_TEST_PYTHON || 'python3', ['-c', code, __filename], {
      cwd: ROOT, env: {...process.env, PYTHONPATH: path.join(ROOT, 'src')}, encoding: 'utf8', timeout: 20000
    });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /actual Python\/V8 synthetic synchronization passed/);
  });
}
