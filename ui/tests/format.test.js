import test from 'node:test';
import assert from 'node:assert/strict';
import { fromInputDate, toInputDate, monthLabel, groupByMonth, formatSize, formatDate } from '../src/lib/format.js';

test('French and ISO dates are read, impossible ones refused', () => {
  assert.equal(fromInputDate('14/10/2026'), '2026-10-14');
  assert.equal(fromInputDate('4.3.26'), '2026-03-04');
  assert.equal(fromInputDate('2026-10-14'), '2026-10-14');
  assert.equal(fromInputDate('31/02/2026'), null);
  assert.equal(fromInputDate('demain'), null);
});
test('dates are shown in French', () => {
  assert.equal(toInputDate('2026-03-04'), '04/03/2026');
  assert.equal(formatDate('2026-03-04'), '4 mars 2026');
  assert.equal(monthLabel('2026-08'), 'Août 2026');
  assert.equal(monthLabel('inconnu'), 'Sans date');
});
test('documents are grouped by month in order', () => {
  const groups = groupByMonth([{ date: '2026-11-03' }, { date: '2026-11-01' }, { date: '2026-10-30' }, { created_at: '2026-10-02T10:00:00' }]);
  assert.deepEqual(groups.map((g) => [g.key, g.docs.length]), [['2026-11', 2], ['2026-10', 2]]);
});
test('sizes', () => {
  assert.equal(formatSize(900), '900 o');
  assert.equal(formatSize(2.4 * 1024 * 1024), '2,4 Mo');
});
