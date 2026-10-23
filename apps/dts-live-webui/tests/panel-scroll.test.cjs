const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createModuleLoader } = require('./helpers/load-typescript.cjs');

class Element {
  parentElement = null;
  scrollTop = 0;
  scrollLeft = 0;
  clientWidth = 200;
  clientHeight = 200;
  scrollWidth = 200;
  scrollHeight = 200;
  style = { overflowX: 'visible', overflowY: 'visible', lineHeight: 'normal' };
}
const { scrollPanelWheel } = createModuleLoader({
  Element, HTMLElement: Element, window: { getComputedStyle: element => element.style },
})(path.join(__dirname, '../src/shared/panelScroll.ts'));

function wheel(target, boundary, values = {}) {
  const event = { target, currentTarget: boundary, deltaX: 0, deltaY: 100, deltaMode: 0, shiftKey: false,
    defaultPrevented: false, propagationStopped: false,
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; }, ...values };
  scrollPanelWheel(event);
  assert.ok(event.defaultPrevented);
  assert.ok(event.propagationStopped);
}

test('panel wheel scrolls a nested list even when the host cancelled native scrolling', () => {
  const boundary = new Element(), list = new Element(), thumbnail = new Element();
  thumbnail.parentElement = list; list.parentElement = boundary;
  list.style.overflowY = 'auto'; list.scrollHeight = 1200;
  wheel(thumbnail, boundary, { defaultPrevented: true });
  assert.equal(list.scrollTop, 100);
  assert.equal(boundary.scrollTop, 0);
  wheel(thumbnail, boundary, { deltaY: -0.5 });
  assert.equal(list.scrollTop, 99.5);
});

test('nested list edges are clamped without scrolling its outer panel or scene', () => {
  const outer = new Element(), boundary = new Element(), list = new Element();
  boundary.parentElement = outer; list.parentElement = boundary;
  for (const element of [outer, boundary, list]) { element.style.overflowY = 'auto'; element.scrollHeight = 400; }
  list.scrollTop = 200;
  wheel(list, boundary);
  assert.equal(list.scrollTop, 200);
  assert.equal(boundary.scrollTop, 0);
  assert.equal(outer.scrollTop, 0);
  wheel(list, boundary, { deltaY: -1000 });
  assert.equal(list.scrollTop, 0);
  wheel(boundary, boundary, { deltaY: 500 });
  assert.equal(boundary.scrollTop, 200);
  assert.equal(outer.scrollTop, 0);
});

test('line, page and horizontal trackpad wheel units scroll only their matching axes', () => {
  const list = new Element();
  list.style = { overflowY: 'auto', overflowX: 'auto', lineHeight: '20px' };
  list.scrollWidth = 1000; list.scrollHeight = 1200;
  wheel(list, list, { deltaY: 3, deltaMode: 1 });
  assert.equal(list.scrollTop, 60);
  wheel(list, list, { deltaY: 1, deltaMode: 2 });
  assert.equal(list.scrollTop, 260);
  wheel(list, list, { deltaX: 30, deltaY: 0 });
  assert.equal(list.scrollLeft, 30);
  wheel(list, list, { deltaY: 40, shiftKey: true });
  assert.equal(list.scrollLeft, 70);
  assert.equal(list.scrollTop, 260);
});
