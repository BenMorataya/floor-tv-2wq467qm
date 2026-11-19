Files for the Ambassador floor TVs in El Salvador.

- `site/index.html` is the TV page and `site/data.js` holds the team totals (no names except approved recognitions, no amounts).
  The floor update sends new totals twice a day on weekdays.
- `.github/workflows/render.yml` turns them into what the Roku app shows: `screen-1/2/3.jpg`, one-minute moving loops
  `screen-1/2/3.mp4` and `status.json` (theme and light positions). It runs on new totals, hourly on weekdays, or by hand,
  and folds the history back to two commits so the repo stays small.
