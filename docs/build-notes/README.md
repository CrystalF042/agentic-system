# Build notes

One note per build, written at the time the change shipped. They are the
engineering record of this project: each one names a defect found in real
output, why it did not raise an error, and the probe added so it cannot
recur silently.

They are written in Chinese, and they are not required reading — the
project README covers the architecture. They are kept because the pattern
they document is the point: nearly every defect in this system has been a
**silent** one, where the code ran, the logs were clean, and the output was
wrong in a way that looked normal.

Recurring themes across the notes:

- **Assert on structure, not text.** Tests that grepped the source passed
  because the searched phrase also appeared in the comment explaining the fix.
- **`null` is not `0`.** "Could not compute" and "computed, and it is zero"
  must not collapse into the same value.
- **Count events, not records.** One press release carried by three outlets
  is one event.
- **Mutation-test every new rule.** Break it deliberately; if nothing goes
  red, either the rule is not load-bearing or the fixture is too weak.
