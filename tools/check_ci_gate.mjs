// Exercise the actual workflow script with GitHub-shaped responses.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const yaml = readFileSync(new URL('../.github/workflows/required-checks-fail-closed.yml', import.meta.url), 'utf8');
const script = yaml.split('          script: |\n')[1].split('\n').map(line => line.slice(12)).join('\n');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const runGate = new AsyncFunction('context', 'github', 'core', 'process', script);
const head = 'a'.repeat(40);
const merge = 'b'.repeat(40);
const check = (name, overrides = {}) => ({
  id: 1, name, app: { id: 15368 }, head_sha: head,
  status: 'completed', conclusion: 'success', ...overrides,
});
const passing = [check('core-and-artifacts'), check('gitleaks')];
const cases = [
  ['PR head succeeds despite synthetic merge SHA', passing, false],
  ['missing check', passing.slice(1), true],
  ['failed check', [passing[0], check('gitleaks', { conclusion: 'failure' })], true],
  ['skipped check', [passing[0], check('gitleaks', { conclusion: 'skipped' })], true],
  ['pending check', [passing[0], check('gitleaks', { status: 'in_progress', conclusion: null })], true],
  ['wrong app', [passing[0], check('gitleaks', { app: { id: 7 } })], true],
  ['wrong head', [passing[0], check('gitleaks', { head_sha: merge })], true],
  ['newer failure wins', [...passing, check('gitleaks', { id: 2, conclusion: 'failure' })], true],
  ['second page is considered', [...Array.from({ length: 100 }, (_, i) => check(`other-${i}`)), ...passing], false],
];
for (const [name, checks, expectedFailure] of cases) {
  const requests = [];
  const failures = [];
  await runGate(
    { payload: { pull_request: { head: { sha: head } } }, sha: merge, repo: { owner: 'example', repo: 'example' } },
    { request: async (route, args) => {
      requests.push(args.ref);
      if (route.endsWith('/statuses')) return { data: [{ context: 'gitleaks', state: 'success' }] };
      return { data: { check_runs: args.ref === head ? checks.slice((args.page - 1) * 100, args.page * 100) : [] } };
    } },
    { setFailed: text => failures.push(text), info() {}, warning() {} },
    { env: { REQUIRED_CHECKS: 'core-and-artifacts,gitleaks', MAX_ATTEMPTS: '1', SLEEP_SECONDS: '1' } },
  );
  assert.equal(failures.length > 0, expectedFailure, name);
  assert.ok(requests.every(ref => ref === head), `${name}: only the selected PR head may supply evidence`);
  console.log(`PASS ${name}`);
}
