const test = require("node:test");
const assert = require("node:assert/strict");
const { createImportLock } = require("../app/static/js/import-lock.js");

test("rapid repeated submissions start one import and the lock is released after completion", () => {
  const lock = createImportLock();
  let operations = 0;
  for (let click = 0; click < 3; click += 1) {
    if (lock.tryStart()) operations += 1;
  }
  assert.equal(operations, 1);

  lock.finish();
  assert.equal(lock.tryStart(), true);
  lock.finish();
});

test("an import failure can release the lock so retry is possible", () => {
  const lock = createImportLock();
  assert.equal(lock.tryStart(), true);
  try {
    throw new Error("network failure");
  } catch {
    lock.finish();
  }
  assert.equal(lock.tryStart(), true);
});
