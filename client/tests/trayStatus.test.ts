import assert from 'node:assert/strict';
import test from 'node:test';

import { trayView, trayViewKey } from '../electron/trayStatus.mjs';

const running = { health: 'running', activeRuns: 0, appUrl: 'http://127.0.0.1:8000/', stopping: false } as const;

void test('a running idle app offers open and stop without confirmation', () => {
  const view = trayView(running);
  assert.equal(view.statusLabel, 'NeuroCade is running');
  assert.equal(view.runsLabel, null);
  assert.equal(view.canOpen, true);
  assert.equal(view.canStop, true);
  assert.equal(view.stopNeedsConfirmation, false);
  assert.equal(view.tooltip, 'NeuroCade is running\nhttp://127.0.0.1:8000/');
});

void test('running workflows are counted and make stopping ask first', () => {
  const one = trayView({ ...running, activeRuns: 1 });
  assert.equal(one.runsLabel, '1 workflow in progress');
  assert.equal(one.stopNeedsConfirmation, true);
  assert.match(one.stopConfirmationDetail ?? '', /interrupts it\.$/);

  const two = trayView({ ...running, activeRuns: 2 });
  assert.equal(two.runsLabel, '2 workflows in progress');
  assert.match(two.stopConfirmationDetail ?? '', /interrupts them\.$/);
});

void test('an unknown run count never blocks or claims workflows', () => {
  const view = trayView({ ...running, activeRuns: null });
  assert.equal(view.runsLabel, null);
  assert.equal(view.stopNeedsConfirmation, false);
});

void test('health states are reported distinctly', () => {
  assert.equal(trayView({ ...running, health: 'starting' }).statusLabel, 'NeuroCade is starting…');
  assert.equal(trayView({ ...running, health: 'unresponsive' }).statusLabel, 'NeuroCade is not responding');
});

void test('while stopping, actions are disabled and the URL is not advertised', () => {
  const view = trayView({ ...running, activeRuns: 1, stopping: true });
  assert.equal(view.statusLabel, 'NeuroCade is stopping…');
  assert.equal(view.canOpen, false);
  assert.equal(view.canStop, false);
  assert.doesNotMatch(view.tooltip, /http/);
});

void test('without a known URL there is nothing to open', () => {
  assert.equal(trayView({ ...running, appUrl: null }).canOpen, false);
});

void test('the menu key changes exactly when the view does', () => {
  assert.equal(trayViewKey(trayView(running)), trayViewKey(trayView({ ...running })));
  assert.notEqual(trayViewKey(trayView(running)), trayViewKey(trayView({ ...running, activeRuns: 1 })));
});
