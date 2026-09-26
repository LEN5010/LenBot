`v1-synthetic.sqlite3`, `v2-synthetic.sqlite3`, `v3-synthetic.sqlite3`,
`v4-synthetic.sqlite3`, `v5-synthetic.sqlite3`, `v6-synthetic.sqlite3`,
and `v7-synthetic.sqlite3`
use the exact table DDL from commits `f90ac4a`, `118bade`, `3629cbf`,
`cd31695`, `807470f`, `475ffbd`, and `91e4786` respectively
(`src/len_bot/next/store.py`). All scene IDs, QQ IDs, times, message bodies,
native assistant/tool pairs, calls, turn states, recap, attention state, and
one-time arrangements are synthetic placeholders, not platform recordings or
real chat excerpts. The format-7 sample includes pending, delivered, blocked,
and cancelled arrangements with their original metadata.
