# Working rules for this repo

Read [`rules/`](rules) before writing code. Short version:

- **Ponytail.** Simplest thing that works. Stdlib before a dependency, one line
  before ten, a `.sql` file before an ORM. See [`rules/CODING.md`](rules/CODING.md).
- **SQL is a deliverable.** Analytics live in `db/queries/*.sql` with CTEs and
  window functions. Never `SELECT *`. See [`rules/SQL.md`](rules/SQL.md).
- **Finance correctness is not negotiable.** Day counts, sign conventions and
  units are specified, not guessed. See [`rules/FINANCE.md`](rules/FINANCE.md).
- **Git.** No `Co-Authored-By` trailer in this repo. Conventional commits, push at
  the end of every stage, tag `stage-N`. See [`rules/GIT.md`](rules/GIT.md).
- **No login page.** This runs on localhost. Auth is out of scope, permanently.
- **Frontend has gradients and motion.** It is a portfolio piece; it should look
  like one. See [`rules/FRONTEND.md`](rules/FRONTEND.md).

Current stage: see [`ROADMAP.md`](ROADMAP.md).
