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
The format-8 fixture was upgraded from the format-7 synthetic fixture with
the migration code in `f555d4c`, then given one synthetic discovered-tool
name to check that the next upgrade preserves existing session state.
The format-9 fixture was produced with the format-9 store and migration code
from `8b2c2ec`, then given one synthetic webpage document. Its URL, extracted
text, notice, timestamps, and all other rows are placeholders, not network
captures or real chat data.
The format-10 fixture uses the exact new-database DDL from committed
`src/len_bot/next/store.py` at `4241e3a`. It carries forward only the synthetic
format-9 rows, then adds a second native assistant/tool exchange reusing the
first exchange's tool-call ID, two synthetic model calls, and a generated 2x2
JPEG cache row. All contents, identities, times, usage, and image pixels are
synthetic; this is not a platform or model response recording. The repeated
tool-call ID is deliberately local to each native exchange and must not be
used to backfill the new format-11 call-position column.
The format-11 fixture was upgraded from the same synthetic format-10 fixture
using the committed format-11 model_calls column and index. Two synthetic
model calls have distinct, explicit existing assistant entry positions; all
other rows and identities remain synthetic. It has no expression-latency
columns, so the format-12 upgrade must preserve those positions and leave
the new turn facts null rather than infer older timestamps.
The format-12 fixture was upgraded from the synthetic format-11 fixture with
the committed three nullable `turns` columns. One existing settled turn has
synthetic `sent` expression timing; one added settled turn has distinct
synthetic `simulated` timing. The queued turn remains without a first
expression. Existing native call anchors are unchanged. No timestamp or
delivery value comes from a real platform or model run.

The format-19 fixture was created from the actual format-19 new-database DDL
before the format-20 change. Its message, native mind entry/call, arrangement,
task, expression, and expression-embedding call are all synthetic records. It
contains no jargon table or real platform/model response. The 19→20 test checks
that these existing rows and FTS text remain unchanged while the three empty
jargon tables are added offline.
