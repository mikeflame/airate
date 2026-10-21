// Reference solution for tasks/js-date-parse used by the tests to prove the task is solvable with Node alone.
const MONTHS = {
  jan: 1, january: 1, feb: 2, february: 2, mar: 3, march: 3, apr: 4, april: 4, may: 5, jun: 6, june: 6,
  jul: 7, july: 7, aug: 8, august: 8, sep: 9, sept: 9, september: 9, oct: 10, october: 10, nov: 11, november: 11, dec: 12, december: 12,
};
const RELATIVE = { today: 0, yesterday: -1, tomorrow: 1 };

function format(y, m, d) {
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

function build(y, m, d) {
  if (m < 1 || m > 12 || d < 1) return null;
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return d <= daysInMonth ? format(y, m, d) : null;
}

export function parseDate(input, now) {
  if (typeof input !== "string") return null;
  const s = input.trim().toLowerCase();
  if (!s) return null;
  if (s in RELATIVE) {
    const d = new Date(now.getTime() + RELATIVE[s] * 86400000);
    return format(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate());
  }
  let m;
  if ((m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) return build(+m[1], +m[2], +m[3]);
  if ((m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/))) return build(+m[3], +m[1], +m[2]);
  if ((m = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/))) return build(+m[3], +m[2], +m[1]);
  if ((m = s.match(/^([a-z]+)\.? (\d{1,2}),? (\d{4})$/))) return MONTHS[m[1]] ? build(+m[3], MONTHS[m[1]], +m[2]) : null;
  if ((m = s.match(/^(\d{1,2}) ([a-z]+)\.?,? (\d{4})$/))) return MONTHS[m[2]] ? build(+m[3], MONTHS[m[2]], +m[1]) : null;
  return null;
}
