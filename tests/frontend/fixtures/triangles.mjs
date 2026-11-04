const dates = [];
const day = new Date('2026-06-01T00:00:00Z');
while (dates.length < 93) {
  if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6)
    dates.push(day.toISOString().slice(0, 10));
  day.setUTCDate(day.getUTCDate() + 1);
}
const closes = [
  ...Array.from({ length: 80 }, (_, index) => 150 - index * 0.8),
  ...Array.from({ length: 9 }, (_, index) => 87 + index * 1.5),
  100.5,
  102,
  134,
  136,
];
export const triangleFixtureBars = closes.map((close, index) => ({
  kind: 'bar',
  date: dates[index],
  open: close,
  high: close + 0.2,
  low: close - 0.2,
  close,
}));
for (const index of [30, 45, 60]) triangleFixtureBars[index].low = 80;
for (const index of [35, 50, 65]) triangleFixtureBars[index].high = 130;
triangleFixtureBars[89].low = 80.8;
export const triangleFixtureAsOf = dates[92];
