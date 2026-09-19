# Git rules

- **No `Co-Authored-By` trailer in this repository.** No "Generated with" line in
  PR bodies either. Deliberate, project-specific.
- Branch `main` only until there is a reason otherwise. This is a solo portfolio
  repo; branch-per-stage adds ceremony and no safety.
- Conventional commits: `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`.
  Scope is the stage area: `feat(ingest): parse SLB bhavcopy`.
- **Push at the end of every stage** and tag it `stage-N`. Not before the check
  passes and CI is green.
- Never commit: `data/raw/`, `data/processed/`, `.env`, `*.pbix` over 50 MB.
  Fixtures in `tests/fixtures/` are committed on purpose — they are small, real,
  and make CI hermetic.
- Commit message body explains *why* when the diff does not. One paragraph, wrapped.
