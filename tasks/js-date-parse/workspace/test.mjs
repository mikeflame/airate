import test from "node:test";
import assert from "node:assert/strict";
import { parseDate } from "./parse.mjs";

const now = new Date("2026-10-20T15:00:00Z");

const cases = [
  ["2026-10-20", "2026-10-20"],
  ["10/20/2026", "2026-10-20"],
  ["20.10.2026", "2026-10-20"],
  ["Oct 20, 2026", "2026-10-20"],
  ["20 October 2026", "2026-10-20"],
  ["  2026-10-20  ", "2026-10-20"],
  ["TODAY", "2026-10-20"],
  ["yesterday", "2026-10-19"],
  ["tomorrow", "2026-10-21"],
  ["1/2/2026", "2026-01-02"],
  ["02.01.2026", "2026-01-02"],
  ["Feb 29, 2024", "2024-02-29"],
  ["Feb 30, 2026", null],
  ["13/13/2026", null],
  ["not a date", null],
  ["", null],
];

for (const [input, expected] of cases) {
  test(`parseDate(${JSON.stringify(input)}) → ${expected}`, () => {
    assert.equal(parseDate(input, now), expected);
  });
}
